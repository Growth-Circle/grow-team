import $ from "jquery";

import render_chat_drawer from "../templates/chat_drawer.hbs";
import render_chat_drawer_composer from "../templates/chat_drawer_composer.hbs";
import render_chat_drawer_log from "../templates/chat_drawer_log.hbs";

import * as channel from "./channel.ts";
import * as chat_drawer_data from "./chat_drawer_data.ts";
import type {ChatMessage, Conversation} from "./chat_drawer_data.ts";
import * as composebox_typeahead from "./composebox_typeahead.ts";
import * as drawer from "./drawer.ts";
import * as hash_util from "./hash_util.ts";
import {$t, $t_html} from "./i18n.ts";
import * as people from "./people.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import {web_mark_read_on_scroll_policy_values} from "./settings_config.ts";
import * as stream_data from "./stream_data.ts";
import * as sub_store from "./sub_store.ts";
import * as timerender from "./timerender.ts";
import * as ui_report from "./ui_report.ts";
import * as unread_ops from "./unread_ops.ts";
import {user_settings} from "./user_settings.ts";
import * as util from "./util.ts";

/*

The chat drawer shows the last messages of one conversation, a channel
and a topic, in the drawer of drawer.ts, so a person can read and answer
without leaving the page. It is not a message list: it keeps its own
short list of messages and does not touch the message lists of the page.
The server renders each message, and rendered_markdown.ts gives its
links and mentions the same behavior as in the feed.

Everything the drawer shows counts as read while the window has focus,
unless the person turned off marking messages as read when they scroll.
The drawer shows one conversation, so the "conversation only" setting
allows it too.

Messages come in three ways: the fetch of the last messages, the
message events (message_events.ts calls handle_new_messages), and the
message that this drawer sent. Each list entry has the message id, so
a message that comes in two ways shows once.

*/

type State = {
    conversation: Conversation;
    messages: ChatMessage[];
    status: "loading" | "ready" | "error";
    sending: boolean;
    $log: JQuery;
    $input: JQuery<HTMLTextAreaElement>;
    $send: JQuery;
    $error: JQuery;
};

let current: State | undefined;

function time_label(timestamp: number): string {
    return timerender.get_localized_date_or_time_for_format(new Date(timestamp * 1000), "time");
}

function time_title(timestamp: number): string {
    return timerender.get_localized_date_or_time_for_format(
        new Date(timestamp * 1000),
        "dayofyear_time",
    );
}

function conversation_label(conversation: Conversation): string {
    const stream_name = sub_store.maybe_get_stream_name(conversation.stream_id) ?? "";
    if (conversation.topic === "") {
        return `#${stream_name}`;
    }
    return `#${stream_name} › ${util.get_final_topic_display_name(conversation.topic)}`;
}

function is_near_bottom(scroller: HTMLElement): boolean {
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
}

function render_log(state: State): void {
    const scroller = state.$log.closest(".sj-drawer__body").get(0);
    const keep_at_bottom = scroller === undefined || is_near_bottom(scroller);

    const groups = chat_drawer_data.group_by_sender(state.messages).map((group) => {
        const sender = people.maybe_get_user_by_id(group.sender_id, true);
        return {
            sender_name: group.sender_full_name,
            avatar_url:
                sender === undefined ? "" : people.small_avatar_url_for_user_id(sender.user_id),
            time_label: time_label(group.timestamp),
            messages: group.messages.map((message) => ({
                id: message.id,
                content: message.content,
                time_title: time_title(message.timestamp),
            })),
        };
    });
    state.$log.html(
        render_chat_drawer_log({
            loading: state.status === "loading",
            error:
                state.status === "error"
                    ? $t({defaultMessage: "Could not load this conversation."})
                    : "",
            has_groups: groups.length > 0,
            groups,
        }),
    );
    state.$log.find(".sj-chat__message").each(function () {
        const $content = $(this);
        rendered_markdown.update_elements($content);
        $content.find(".user-mention[data-user-id]").each(function () {
            if (people.is_my_user_id(Number($(this).attr("data-user-id")))) {
                $(this).addClass("user-mention-me");
            }
        });
    });

    if (scroller !== undefined && keep_at_bottom) {
        scroller.scrollTop = scroller.scrollHeight;
    }
}

function update_send_button(state: State): void {
    const has_text = (state.$input.val() ?? "").trim() !== "";
    state.$send.prop("disabled", !has_text || state.sending || state.$input.is(":disabled"));
}

function mark_displayed_messages_as_read(state: State): void {
    if (
        current !== state ||
        !unread_ops.is_window_focused() ||
        user_settings.web_mark_read_on_scroll_policy ===
            web_mark_read_on_scroll_policy_values.never.code
    ) {
        return;
    }
    unread_ops.notify_server_message_ids_read(state.messages.map((message) => message.id));
}

function add_messages(state: State, messages: ChatMessage[]): void {
    state.messages = chat_drawer_data.merge_messages(state.messages, messages);
    render_log(state);
    mark_displayed_messages_as_read(state);
}

function fetch_latest(state: State, {on_failure}: {on_failure: boolean}): void {
    void channel.get({
        url: "/json/messages",
        data: {
            anchor: "newest",
            num_before: chat_drawer_data.PAGE_SIZE,
            num_after: 0,
            narrow: chat_drawer_data.narrow_for(state.conversation),
            apply_markdown: true,
            client_gravatar: false,
            allow_empty_topic_name: true,
        },
        success(raw_data) {
            if (current !== state) {
                return;
            }
            const {messages} = chat_drawer_data.chat_messages_response_schema.parse(raw_data);
            state.status = "ready";
            add_messages(state, messages);
        },
        error() {
            if (current !== state || !on_failure) {
                return;
            }
            state.status = "error";
            render_log(state);
        },
    });
}

function send(state: State): void {
    const content = (state.$input.val() ?? "").trim();
    if (content === "" || state.sending) {
        return;
    }
    state.sending = true;
    state.$error.removeClass("show").empty();
    update_send_button(state);

    void channel.post({
        url: "/json/messages",
        data: {
            type: "stream",
            to: JSON.stringify(state.conversation.stream_id),
            topic: state.conversation.topic,
            content,
        },
        success() {
            state.sending = false;
            if (current !== state) {
                return;
            }
            state.$input.val("");
            update_send_button(state);
            // The message event usually arrives first and the id keeps
            // one copy. This fetch covers an event that is late.
            fetch_latest(state, {on_failure: false});
        },
        error(xhr) {
            state.sending = false;
            if (current !== state) {
                return;
            }
            ui_report.error(
                $t_html({defaultMessage: "Could not send the message"}),
                xhr,
                state.$error,
            );
            update_send_button(state);
        },
    });
}

function disabled_reason(conversation: Conversation): string {
    const sub = stream_data.get_sub_by_id(conversation.stream_id);
    if (sub === undefined) {
        return $t({defaultMessage: "You cannot post in this conversation."});
    }
    if (sub.is_archived) {
        return $t({defaultMessage: "This channel is archived."});
    }
    if (!stream_data.can_post_messages_in_stream(sub)) {
        return $t({defaultMessage: "You do not have permission to post in this channel."});
    }
    return "";
}

export function open(conversation: Conversation): void {
    const reason = disabled_reason(conversation);
    const $body = $(
        render_chat_drawer({
            full_view_url: hash_util.by_stream_topic_url(
                conversation.stream_id,
                conversation.topic,
            ),
        }),
    );
    const $composer = $(
        render_chat_drawer_composer({
            disabled_reason: reason,
            placeholder: $t({defaultMessage: "Write a message"}),
        }),
    );
    const state: State = {
        conversation,
        messages: [],
        status: "loading",
        sending: false,
        $log: $body.find(".sj-chat__log"),
        $input: $composer.find(".sj-chat__input"),
        $send: $composer.find(".sj-chat__send"),
        $error: $composer.find(".sj-chat__error"),
    };
    current = state;

    $body.find(".sj-chat__full-view").on("click", () => {
        drawer.close();
    });
    $composer.on("submit", (event) => {
        event.preventDefault();
        send(state);
    });
    state.$input.on("input", () => {
        update_send_button(state);
    });
    // The typeahead goes first so that its keydown handler runs before
    // the one below. Enter that picks a suggestion is then already
    // default-prevented, and it does not send the message.
    composebox_typeahead.initialize_chat_drawer_typeahead(state.$input, conversation);
    state.$input.on("keydown", (event) => {
        if (
            event.key === "Enter" &&
            !event.isDefaultPrevented() &&
            chat_drawer_data.should_send_on_enter({
                enter_sends: user_settings.enter_sends,
                shift_key: event.shiftKey,
                ctrl_key: event.ctrlKey,
                meta_key: event.metaKey,
                is_composing: event.originalEvent?.isComposing === true,
            })
        ) {
            event.preventDefault();
            send(state);
        }
    });

    render_log(state);
    // Messages that arrived while the window was in the background are
    // marked as read once the person comes back to it.
    const mark_read_on_focus = (): void => {
        mark_displayed_messages_as_read(state);
    };
    $(window).on("focus", mark_read_on_focus);
    drawer.open({
        kind_label: $t(
            {defaultMessage: "CHAT · {conversation}"},
            {conversation: conversation_label(conversation)},
        ),
        $body,
        $footer: $composer,
        extra_class: "sj-chat-drawer",
        on_close() {
            $(window).off("focus", mark_read_on_focus);
            if (current === state) {
                current = undefined;
            }
        },
    });
    if (reason === "") {
        // drawer.ts focuses the close button when the drawer shows. A
        // reader who opened the chat most likely wants to write.
        setTimeout(() => {
            if (current === state) {
                state.$input.trigger("focus");
            }
        }, 0);
    }
    fetch_latest(state, {on_failure: true});
}

// message_events.ts calls this with the messages that just arrived.
export function handle_new_messages(
    messages: {
        id: number;
        type: string;
        stream_id?: number;
        topic?: string;
        sender_id: number;
        sender_full_name: string;
        content: string;
        timestamp: number;
    }[],
): void {
    const state = current;
    if (state === undefined) {
        return;
    }
    const matching = messages.filter((message) =>
        chat_drawer_data.is_in_conversation(message, state.conversation),
    );
    if (matching.length > 0) {
        add_messages(state, matching);
    }
}

export function handle_deleted_messages(message_ids: number[]): void {
    const state = current;
    if (state === undefined) {
        return;
    }
    const remaining = chat_drawer_data.remove_messages(state.messages, message_ids);
    if (remaining.length !== state.messages.length) {
        state.messages = remaining;
        render_log(state);
    }
}

export function handle_edited_message(message_id: number, rendered_content: string): void {
    const state = current;
    const edited = state?.messages.find((message) => message.id === message_id);
    if (state === undefined || edited === undefined) {
        return;
    }
    add_messages(state, [{...edited, content: rendered_content}]);
}
