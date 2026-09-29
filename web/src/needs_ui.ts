import $ from "jquery";
import _ from "lodash";

import render_needs_view from "../templates/needs_view.hbs";

import * as compose_closed_ui from "./compose_closed_ui.ts";
import * as confirm_dialog from "./confirm_dialog.ts";
import type {DialogWidgetConfig} from "./dialog_widget.ts";
import * as feedback_widget from "./feedback_widget.ts";
import * as hash_util from "./hash_util.ts";
import {$t, $t_html} from "./i18n.ts";
import * as left_sidebar_navigation_area from "./left_sidebar_navigation_area.ts";
import * as live_updates from "./live_updates.ts";
import * as nav_counts from "./nav_counts.ts";
import type {NeedItem, NeedKind, NeedTab} from "./needs_data.ts";
import * as needs_data from "./needs_data.ts";
import {page_params} from "./page_params.ts";
import {realm} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import * as timerender from "./timerender.ts";
import * as unread_ui from "./unread_ui.ts";
import * as views_util from "./views_util.ts";

// The Needs you page (#needs, #needs/<id>, #needs/<id>/confirm): what
// waits on this person across every room, in three kinds. need_card.hbs
// is the card, and card_context() builds its fields, so that the Today
// page shows the same card.

const TABS: NeedTab[] = ["all", "approval", "mention", "decision"];
const OPTION_LABEL_LIMIT = 40;

let visible = false;
// The open list is here once the first load ends, the done list once
// this page has been shown and loaded it.
let loaded = false;
let load_failed = false;
let done_loaded = false;
let current_tab: NeedTab = "all";
let open_items: NeedItem[] = [];
let done_items: NeedItem[] = [];
// The ids that already had a card on screen, so that only a new card
// pops in.
const shown_ids = new Set<string>();
let latest_load = 0;
let args_to_handle: string[] | undefined;

function set_visible(value: boolean): void {
    visible = value;
}

// ----- Turning one item into template fields -----

function tag_text(kind: NeedKind): string {
    const labels: Record<NeedKind, string> = {
        approval: $t({defaultMessage: "APPROVAL"}),
        mention: $t({defaultMessage: "MENTION"}),
        decision: $t({defaultMessage: "DECISION"}),
    };
    return labels[kind];
}

function tab_label(tab: NeedTab): string {
    const labels: Record<NeedTab, string> = {
        all: $t({defaultMessage: "All"}),
        approval: $t({defaultMessage: "Approval"}),
        mention: $t({defaultMessage: "Mention"}),
        decision: $t({defaultMessage: "Decision"}),
    };
    return labels[tab];
}

// The color of an agent goes in a style attribute, so only a plain hex
// color passes.
function avatar_color(actor: NeedItem["actor"]): string {
    return actor.color !== null && /^#[\da-f]{6}$/i.test(actor.color)
        ? actor.color
        : "var(--sj-redup)";
}

function meta_text(item: NeedItem): string {
    const who = item.actor.name;
    const time = timerender.relative_time_string_from_date(new Date(item.time));
    const room = item.stream_id === null ? "" : stream_data.get_stream_name_from_id(item.stream_id);
    if (room === "") {
        return $t({defaultMessage: "{who} · {time}"}, {who, time});
    }
    return $t({defaultMessage: "{who} · # {room} · {time}"}, {who, room, time});
}

// Where the person can see the item: the topic of a room, or the
// direct messages with who asked. A mention opens at the message.
function conversation_url(item: NeedItem): string {
    const near = item.kind === "mention" ? `/near/${needs_data.raw_id(item.id)}` : "";
    if (item.stream_id !== null && item.topic !== null) {
        return hash_util.by_stream_topic_url(item.stream_id, item.topic) + near;
    }
    return `#narrow/dm/${item.actor.id}${near}`;
}

type CardAction = {
    label: string;
    variant?: "primary" | "secondary";
    choice?: string;
    href?: string;
    is_expired?: boolean;
};

function card_actions(item: NeedItem): CardAction[] {
    const link = (label: string): CardAction => ({label, href: conversation_url(item)});
    // A mention is never waiting on an agent, so it does not expire.
    const expired = item.expired && item.kind !== "mention";
    const actions: CardAction[] = [];
    if (item.kind === "mention") {
        actions.push(link($t({defaultMessage: "Reply"})));
    } else if (item.kind === "approval" || expired) {
        actions.push(link($t({defaultMessage: "View"})));
    }

    if (expired) {
        actions.push({
            label: $t({defaultMessage: "Expired. Ask the agent to try again."}),
            is_expired: true,
        });
        return actions;
    }

    if (item.kind === "approval") {
        actions.push({label: $t({defaultMessage: "Approve"}), variant: "primary", choice: ""});
    } else if (item.kind === "mention") {
        actions.push({label: $t({defaultMessage: "Mark done"}), variant: "primary", choice: ""});
    } else {
        // The first choice is the main button. The other choices come
        // before it, like the second button of the other kinds.
        const [first, ...others] = needs_data.decision_options(item);
        if (first === undefined) {
            // An open question. The person answers in the room.
            actions.push(link($t({defaultMessage: "Reply"})));
        } else {
            for (const choice of others) {
                actions.push({label: choice, variant: "secondary", choice});
            }
            actions.push({label: first, variant: "primary", choice: first});
        }
    }
    return actions;
}

export function card_context(item: NeedItem): Record<string, unknown> {
    const actor = item.actor;
    const is_human = actor.type === "human";
    return {
        id: item.id,
        kind: item.kind,
        is_new: !shown_ids.has(item.id),
        tag: tag_text(item.kind),
        is_human,
        initials: actor.initials,
        avatar_shape: actor.shape === "ring" || actor.shape === "box" ? actor.shape : "circle",
        avatar_color: avatar_color(actor),
        meta: meta_text(item),
        text: item.text,
        attachment: item.attachment,
        actions: card_actions(item),
    };
}

function needs_view_context(): Record<string, unknown> {
    const cards = needs_data
        .items_for_tab(open_items, current_tab)
        .map((item) => card_context(item));
    return {
        loading: !loaded,
        load_failed,
        header_eyebrow: $t(
            {defaultMessage: "FROM ALL ROOMS IN {workspace}"},
            {workspace: realm.realm_name},
        ),
        tabs: TABS.map((tab) => {
            const label = tab_label(tab);
            return {
                value: tab,
                active: tab === current_tab,
                label: loaded
                    ? $t(
                          {defaultMessage: "{label} · {count}"},
                          {label, count: needs_data.count_for_tab(open_items, tab)},
                      )
                    : label,
            };
        }),
        is_empty: cards.length === 0,
        cards,
        show_done: done_loaded,
        done_label: $t({defaultMessage: "DONE TODAY · {count}"}, {count: done_items.length}),
        done_rows: done_items.map((item) => ({
            id: item.id,
            text: item.text,
            room:
                item.stream_id === null ? "" : stream_data.get_stream_name_from_id(item.stream_id),
        })),
    };
}

// The element that has focus now, as a selector that finds the same
// control after the page draws again.
function focus_selector(): string | undefined {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement) || $("#needs-view").has(element).length === 0) {
        return undefined;
    }
    const tab = element.dataset["tab"];
    if (tab !== undefined) {
        return `.needs-tab[data-tab="${tab}"]`;
    }
    const need_id = element.dataset["needId"];
    if (need_id !== undefined) {
        const variant = element.classList.contains("needs-card-primary") ? "primary" : "secondary";
        const choice = element.dataset["choice"] ?? "";
        return `.needs-card-${variant}[data-need-id="${CSS.escape(need_id)}"][data-choice="${CSS.escape(choice)}"]`;
    }
    return element.classList.contains("needs-retry") ? ".needs-retry" : undefined;
}

function complete_rerender(): void {
    if (!visible) {
        return;
    }
    const selector = focus_selector();
    $("#needs-view").html(render_needs_view(needs_view_context()));
    for (const item of open_items) {
        shown_ids.add(item.id);
    }
    if (selector !== undefined) {
        $(selector).first().trigger("focus");
    }
}

// After a card leaves, focus goes to the card that took its place.
function focus_card_at(index: number): void {
    const $cards = $("#needs-view .needs-card");
    if ($cards.length === 0) {
        $(".needs-tab-active").trigger("focus");
        return;
    }
    $cards
        .eq(Math.min(Math.max(index, 0), $cards.length - 1))
        .find(".needs-btn")
        .last()
        .trigger("focus");
}

function update_nav_count(): void {
    nav_counts.set_count("needs", open_items.length);
}

// ----- Loading data -----

function refresh(with_done: boolean): void {
    latest_load += 1;
    const load = latest_load;
    void (async () => {
        try {
            const [open_result, done_result] = await Promise.all([
                needs_data.fetch_open(),
                with_done ? needs_data.fetch_done_today() : Promise.resolve(undefined),
            ]);
            if (load !== latest_load) {
                return;
            }
            const before = JSON.stringify([
                open_items,
                done_items,
                loaded,
                load_failed,
                done_loaded,
            ]);
            // An item with an action on its way is not open any more,
            // even if the server has not heard of the action yet.
            open_items = open_result.items.filter((item) => !needs_data.is_pending(item.id));
            if (done_result !== undefined) {
                done_items = done_result.items;
                done_loaded = true;
            }
            loaded = true;
            load_failed = false;
            update_nav_count();
            // The same list needs no new drawing, and a new drawing
            // would take focus from a person who is using the keyboard.
            if (
                before !==
                JSON.stringify([open_items, done_items, loaded, load_failed, done_loaded])
            ) {
                complete_rerender();
            }
            handle_args();
        } catch {
            if (load !== latest_load || loaded) {
                return;
            }
            load_failed = true;
            complete_rerender();
        }
    })();
}

// ----- An action with a 5-second window to undo it -----

// A new load gives new objects for the same items, so the id is the
// only safe way to find one.
function remove_open_item(item: NeedItem): number {
    const index = open_items.findIndex((open_item) => open_item.id === item.id);
    if (index !== -1) {
        open_items.splice(index, 1);
    }
    return index;
}

function restore_open_item(item: NeedItem, index: number): void {
    if (open_items.some((open_item) => open_item.id === item.id)) {
        return;
    }
    open_items.splice(Math.max(0, Math.min(index, open_items.length)), 0, item);
}

function start_action(item: NeedItem, commit: () => Promise<void>, success_text: string): void {
    const index = remove_open_item(item);
    complete_rerender();
    focus_card_at(index);
    update_nav_count();

    needs_data.schedule_pending(item.id, commit, (error: unknown) => {
        if (error !== undefined) {
            restore_open_item(item, index);
            complete_rerender();
            update_nav_count();
            feedback_widget.show_toast({
                variant: "error",
                text: $t({defaultMessage: "Could not save. Try again."}),
                on_retry() {
                    start_action(item, commit, success_text);
                },
            });
            // The failure can mean that the item changed on the server.
            refresh(visible);
            return;
        }
        done_items = [item, ...done_items.filter((done_item) => done_item.id !== item.id)];
        complete_rerender();
    });

    feedback_widget.show_toast({
        text: success_text,
        on_undo() {
            if (needs_data.cancel_pending(item.id)) {
                restore_open_item(item, index);
                complete_rerender();
                update_nav_count();
            }
        },
    });
}

function short_option(option: string): string {
    return _.truncate(option, {length: OPTION_LABEL_LIMIT});
}

function start_commit(item: NeedItem, choice: string): void {
    const who = item.actor.name;
    if (item.kind === "approval") {
        start_action(
            item,
            async () => needs_data.commit_approve(item),
            $t({defaultMessage: "Approved. {who} has been notified."}, {who}),
        );
    } else if (item.kind === "mention") {
        start_action(
            item,
            async () => needs_data.commit_mention_done(item),
            $t({defaultMessage: "Marked done."}),
        );
    } else if (choice !== "") {
        start_action(
            item,
            async () => needs_data.commit_decision(item, choice),
            $t(
                {defaultMessage: "{option} chosen. {who} continues working on it."},
                {option: short_option(choice), who},
            ),
        );
    }
}

function find_open_item(need_id: string): NeedItem | undefined {
    return open_items.find((item) => item.id === need_id);
}

// ----- A link with an item: #needs/<id> and #needs/<id>/confirm -----

function launch_approval_confirm(item: NeedItem): void {
    const conf: DialogWidgetConfig = {
        modal_title_text: $t({defaultMessage: "Confirm this approval"}),
        modal_content_html: $t_html(
            {defaultMessage: "{who} asks you to approve: {text}"},
            {who: item.actor.name, text: item.text},
        ),
        modal_submit_button_text: $t({defaultMessage: "Approve"}),
        on_click() {
            start_commit(item, "");
        },
    };
    confirm_dialog.launch(conf);
}

function safe_decode(value: string): string {
    try {
        return decodeURIComponent(value);
    } catch {
        return value;
    }
}

// A push notification opens the page with the item it is about. The
// person confirms a risky approval here, once, before it goes out.
function handle_args(): void {
    const args = args_to_handle;
    args_to_handle = undefined;
    const [need_id, sub_action] = args ?? [];
    if (need_id === undefined || need_id === "") {
        return;
    }
    const item = find_open_item(safe_decode(need_id));
    if (item === undefined) {
        feedback_widget.show_toast({
            variant: "info",
            text: $t({defaultMessage: "This request no longer waits for you."}),
        });
        return;
    }
    $(`#needs-view .needs-card[data-need-id="${CSS.escape(item.id)}"]`)[0]?.scrollIntoView({
        block: "center",
    });
    if (sub_action === "confirm" && item.kind === "approval" && !item.expired) {
        launch_approval_confirm(item);
    }
}

// ----- Clicks -----

function is_need_tab(value: string | undefined): value is NeedTab {
    return value === "all" || value === "approval" || value === "mention" || value === "decision";
}

function on_view_click(): void {
    const $view = $("#needs-view");

    $view.on("click", ".needs-tab", function (this: HTMLElement) {
        const tab = $(this).attr("data-tab");
        if (is_need_tab(tab) && tab !== current_tab) {
            current_tab = tab;
            complete_rerender();
        }
    });

    $view.on("click", ".needs-retry", () => {
        load_failed = false;
        complete_rerender();
        refresh(true);
    });

    $view.on("click", ".needs-card-primary, .needs-card-secondary", function (this: HTMLElement) {
        const need_id = $(this).attr("data-need-id");
        const item = need_id === undefined ? undefined : find_open_item(need_id);
        if (item !== undefined) {
            start_commit(item, $(this).attr("data-choice") ?? "");
        }
    });
}

// ----- The four exports every center view keeps -----

export function initialize(): void {
    on_view_click();
    // A spectator has no list. The server refuses the request.
    if (page_params.is_spectator) {
        return;
    }
    // The number in the sidebar follows the list, so the list loads
    // before the page is opened. A job that moves, or a message that
    // arrives or is read, can change it.
    const refresh_soon = _.debounce(() => {
        refresh(visible);
    }, 1000);
    live_updates.on("agent_job", refresh_soon);
    // Only a mention or a direct message starts or ends a need, so a
    // message in a room that mentions no one does not load the list.
    let last_message_signal: string | undefined;
    unread_ui.register_update_unread_counts_hook((counts) => {
        const signal = `${counts.mentioned_message_count}/${counts.direct_message_count}`;
        // The first call is the load of the page, which loads the list too.
        if (last_message_signal !== undefined && signal !== last_message_signal) {
            refresh_soon();
        }
        last_message_signal = signal;
    });
    refresh(false);
}

export function show(args: string[]): void {
    // A load that failed while the page was closed is tried again now.
    load_failed = false;
    views_util.show({
        highlight_view_in_left_sidebar() {
            views_util.handle_message_view_deactivated(() => {
                left_sidebar_navigation_area.select_top_left_corner_item("");
            });
        },
        $view: $("#needs-view"),
        update_compose: compose_closed_ui.update_buttons,
        is_visible: () => visible,
        set_visible,
        complete_rerender,
    });
    // This view has no message feed to reply to, so it hides the
    // closed compose bar. An open compose box stays on screen.
    $("#compose").addClass("hide-closed-compose");
    args_to_handle = args;
    refresh(true);
}

export function hide(): void {
    if (!visible) {
        return;
    }
    views_util.hide({$view: $("#needs-view"), set_visible});
    $("#compose").removeClass("hide-closed-compose");
}

export function title(): string {
    return $t({defaultMessage: "Needs you"});
}
