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
import * as narrow_title from "./narrow_title.ts";
import * as nav_counts from "./nav_counts.ts";
import type {NeedGroup, NeedItem, NeedKind, NeedTab} from "./needs_data.ts";
import * as needs_data from "./needs_data.ts";
import {page_params} from "./page_params.ts";
import * as sanji_format from "./sanji_format.ts";
import {realm} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import * as unread_ui from "./unread_ui.ts";
import * as util from "./util.ts";
import * as views_util from "./views_util.ts";

// The Needs you page (#needs, #needs/<tab>, #needs/<id>,
// #needs/<id>/confirm): what waits on this person across every room,
// in tabs. need_card.hbs is the card, and card_context() builds its
// fields, so that the Today page shows the same card.

const OPTION_LABEL_LIMIT = 40;
// The data-choice of the Reject button of a call of an external client.
const REJECT_CHOICE = "reject";

let visible = false;
// The open list is here once the first load ends, the done list once
// this page has been shown and loaded it.
let loaded = false;
let load_failed = false;
let done_loaded = false;
let current_tab: NeedTab = "all";
// Mentions that name a whole team are hidden until the person asks.
let show_team_wide = false;
// The groups the person opened, by key.
const expanded_groups = new Set<string>();
// The calls of external clients whose whole content the person opened.
const expanded_mcp_calls = new Set<string>();
// Each group has an id in the page, for the button that opens it.
const group_dom_ids = new Map<string, string>();
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

// The badge says where the request comes from.
function tag_text(kind: NeedKind): string {
    const labels: Record<NeedKind, string> = {
        approval: $t({defaultMessage: "Approval"}),
        mention: $t({defaultMessage: "Chat"}),
        decision: $t({defaultMessage: "Decision"}),
        card_mention: $t({defaultMessage: "Card note"}),
        card_assigned: $t({defaultMessage: "Assigned to you"}),
        reply: $t({defaultMessage: "Replies"}),
    };
    return labels[kind];
}

function tab_label(tab: NeedTab): string {
    const labels: Record<NeedTab, string> = {
        all: $t({defaultMessage: "All"}),
        mentions: $t({defaultMessage: "Mentions"}),
        replies: $t({defaultMessage: "Replies"}),
        assigned: $t({defaultMessage: "Assigned to you"}),
        approval: $t({defaultMessage: "Approvals"}),
        decision: $t({defaultMessage: "Decisions"}),
    };
    return labels[tab];
}

// What an empty tab collects, so the person knows what to expect.
function empty_tab_text(tab: NeedTab): string {
    const texts: Record<NeedTab, string> = {
        all: $t({defaultMessage: "Nothing is waiting for you."}),
        mentions: $t({
            defaultMessage: "Mentions of you in chat and in card notes collect here.",
        }),
        replies: $t({
            defaultMessage:
                "New messages in topics you follow collect here, after you have written.",
        }),
        assigned: $t({defaultMessage: "Cards that are made yours collect here."}),
        approval: $t({defaultMessage: "Approvals that an agent asks from you collect here."}),
        decision: $t({defaultMessage: "Decisions that an agent asks from you collect here."}),
    };
    return texts[tab];
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
    const time = sanji_format.format_relative(new Date(item.time));
    if (needs_data.is_card_kind(item.kind)) {
        return item.task === null
            ? $t({defaultMessage: "{who} · {time}"}, {who, time})
            : $t(
                  {defaultMessage: "{who} · {card_id} · {time}"},
                  {who, card_id: item.task.display_id, time},
              );
    }
    const room = item.stream_id === null ? "" : stream_data.get_stream_name_from_id(item.stream_id);
    if (item.reply !== undefined && room !== "" && item.topic !== null) {
        return $t(
            {defaultMessage: "{count} new from {names} · # {room} › {topic} · {time}"},
            {
                count: item.reply.count,
                names: item.reply.sender_names.join(", "),
                room,
                topic: util.get_final_topic_display_name(item.topic),
                time,
            },
        );
    }
    if (room === "") {
        return $t({defaultMessage: "{who} · {time}"}, {who, time});
    }
    if (item.topic === null) {
        return $t({defaultMessage: "{who} · # {room} · {time}"}, {who, room, time});
    }
    return $t(
        {defaultMessage: "{who} · # {room} › {topic} · {time}"},
        {who, room, topic: util.get_final_topic_display_name(item.topic), time},
    );
}

// Where the person can see the item: the topic of a room, or the
// direct messages with who asked. A mention opens at the message, and a
// reply at the first message the person has not read.
function conversation_url(item: NeedItem): string {
    let near = "";
    if (item.kind === "mention") {
        near = `/near/${needs_data.raw_id(item.id)}`;
    } else if (item.reply !== undefined) {
        near = `/near/${item.reply.first_unread_message_id}`;
    }
    if (item.stream_id !== null && item.topic !== null) {
        return hash_util.by_stream_topic_url(item.stream_id, item.topic) + near;
    }
    return `#narrow/dm/${item.actor.id}${near}`;
}

function mcp_tool_label(tool: string): string {
    const labels: Record<string, string> = {
        send_message: $t({defaultMessage: "Send a channel message"}),
        send_direct_message: $t({defaultMessage: "Send a direct message"}),
        edit_message: $t({defaultMessage: "Edit a message"}),
        add_reaction: $t({defaultMessage: "Add a reaction"}),
        remove_reaction: $t({defaultMessage: "Remove a reaction"}),
        upload_file: $t({defaultMessage: "Upload a file"}),
        create_task: $t({defaultMessage: "Create a card"}),
        update_task: $t({defaultMessage: "Change a card"}),
        delete_task: $t({defaultMessage: "Delete a card"}),
    };
    return labels[tool] ?? tool;
}

// Where the call goes, by name. Every part is plain text for the template.
function mcp_target_text(target: needs_data.NeedMcpCall["target"]): string {
    const topic = target.topic === null ? "" : util.get_final_topic_display_name(target.topic);
    switch (target.type) {
        case "channel":
            return $t({defaultMessage: "# {room} › {topic}"}, {room: target.name, topic});
        case "direct":
            return $t({defaultMessage: "Direct message to {names}"}, {names: target.name});
        case "message":
            return target.topic === null
                ? $t({defaultMessage: "A message in a direct conversation"})
                : $t(
                      {defaultMessage: "A message in # {room} › {topic}"},
                      {room: target.name, topic},
                  );
        case "column":
            return $t({defaultMessage: "Column {name}"}, {name: target.name});
        default:
            return target.name;
    }
}

function mcp_call_context(item: NeedItem): Record<string, unknown> | undefined {
    const call = item.mcp_call;
    if (call === undefined) {
        return undefined;
    }
    const expanded = expanded_mcp_calls.has(item.id);
    const {text, can_expand} = needs_data.mcp_call_content(call, expanded);
    return {
        tool_label: mcp_tool_label(call.tool),
        target: mcp_target_text(call.target),
        has_mentions: call.mentions.length > 0,
        mentions: call.mentions.join(", "),
        content: text,
        has_content: text !== "",
        can_expand,
        expand_label: expanded
            ? $t({defaultMessage: "Show less"})
            : $t({defaultMessage: "Show all"}),
        expanded,
        expires: sanji_format.format_date_time(new Date(item.expires_at)),
        digest: call.digest,
    };
}

type CardAction = {
    label: string;
    variant?: "primary" | "secondary";
    choice?: string;
    // The button that puts the item off, not a choice of the item.
    is_snooze?: boolean;
    href?: string;
    is_expired?: boolean;
};

// The main action of a mention is the one that takes the person to
// it, on the right as the main button of every kind is.
function card_actions(item: NeedItem): CardAction[] {
    const link = (label: string, variant: "primary" | "secondary"): CardAction => ({
        label,
        variant,
        href: conversation_url(item),
    });
    // A mention is never waiting on an agent, so it does not expire.
    const expired = item.expired && item.kind !== "mention";
    const actions: CardAction[] = [];
    if (!expired) {
        actions.push({label: $t({defaultMessage: "Snooze"}), is_snooze: true});
    }
    if (needs_data.is_card_kind(item.kind)) {
        actions.push({
            label:
                item.kind === "card_mention"
                    ? $t({defaultMessage: "Mark done"})
                    : $t({defaultMessage: "Mark as seen"}),
            variant: "secondary",
            choice: "",
        });
        if (item.task !== null) {
            actions.push({
                label: $t({defaultMessage: "Open card"}),
                variant: "primary",
                href: `#tasks/${item.task.id}`,
            });
        }
        return actions;
    }
    if (item.kind === "mention") {
        actions.push(
            {label: $t({defaultMessage: "Mark done"}), variant: "secondary", choice: ""},
            link($t({defaultMessage: "Reply"}), "primary"),
        );
        return actions;
    }
    // Reading the topic is what ends a reply, so it has no button to
    // mark it done.
    if (item.kind === "reply") {
        actions.push(link($t({defaultMessage: "Open topic"}), "primary"));
        return actions;
    }
    // A call of an external client has no conversation to open.
    if ((item.kind === "approval" || expired) && !needs_data.is_mcp_call(item)) {
        actions.push(link($t({defaultMessage: "View"}), "secondary"));
    }

    if (expired) {
        actions.push({
            label: $t({defaultMessage: "Expired. Ask the agent to try again."}),
            is_expired: true,
        });
        return actions;
    }

    if (item.kind === "approval") {
        if (needs_data.is_mcp_call(item)) {
            actions.push({
                label: $t({defaultMessage: "Reject"}),
                variant: "secondary",
                choice: REJECT_CHOICE,
            });
        }
        actions.push({label: $t({defaultMessage: "Approve"}), variant: "primary", choice: ""});
    } else {
        // The first choice is the main button. The other choices come
        // before it, like the second button of the other kinds.
        const [first, ...others] = needs_data.decision_options(item);
        if (first === undefined) {
            // An open question. The person answers in the room.
            actions.push(link($t({defaultMessage: "Reply"}), "primary"));
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
        team_wide: item.broadcast,
        is_human,
        initials: actor.initials,
        avatar_shape: actor.shape === "ring" || actor.shape === "box" ? actor.shape : "circle",
        avatar_color: avatar_color(actor),
        meta: meta_text(item),
        ...needs_data.card_text(item),
        attachment: item.attachment,
        mcp_call: mcp_call_context(item),
        actions: card_actions(item),
    };
}

function group_dom_id(key: string): string {
    let dom_id = group_dom_ids.get(key);
    if (dom_id === undefined) {
        dom_id = `needs-group-${group_dom_ids.size}`;
        group_dom_ids.set(key, dom_id);
    }
    return dom_id;
}

function group_context(group: NeedGroup): Record<string, unknown> {
    const [newest] = group.items;
    const expanded = expanded_groups.has(group.key);
    return {
        key: group.key,
        dom_id: group_dom_id(group.key),
        expanded,
        title: $t(
            {defaultMessage: "{count} mentions in # {room} › {topic}"},
            {
                count: group.items.length,
                room: stream_data.get_stream_name_from_id(group.stream_id),
                topic: util.get_final_topic_display_name(group.topic),
            },
        ),
        meta:
            newest === undefined
                ? ""
                : $t(
                      {defaultMessage: "Latest: {who} · {time}"},
                      {
                          who: newest.actor.name,
                          time: sanji_format.format_relative(new Date(newest.time)),
                      },
                  ),
        team_wide: group.items.every((item) => item.broadcast),
        count: group.items.length,
        group_cards: expanded ? group.items.map((item) => card_context(item)) : [],
    };
}

function needs_view_context(): Record<string, unknown> {
    const rows = needs_data
        .rows_for_items(needs_data.items_for_tab(open_items, current_tab, {show_team_wide}))
        .map((row) =>
            "group" in row
                ? {is_group: true, ...group_context(row.group)}
                : {is_group: false, ...card_context(row.item)},
        );
    const team_wide_count = needs_data.team_wide_count_for_tab(open_items, current_tab);
    const only_team_wide_waits = rows.length === 0 && team_wide_count > 0 && !show_team_wide;
    return {
        loading: !loaded,
        load_failed,
        header_eyebrow: $t(
            {defaultMessage: "FROM ALL ROOMS IN {workspace}"},
            {workspace: realm.realm_name},
        ),
        tabs: needs_data.NEED_TABS.map((tab) => {
            const label = tab_label(tab);
            const count = needs_data.count_for_tab(open_items, tab, {show_team_wide});
            return {
                value: tab,
                active: tab === current_tab,
                // A tab with nothing in it stays one click away.
                is_empty: loaded && count === 0,
                label: loaded ? $t({defaultMessage: "{label} · {count}"}, {label, count}) : label,
            };
        }),
        team_wide_toggle:
            loaded && team_wide_count > 0
                ? {
                      is_on: show_team_wide,
                      label: show_team_wide
                          ? $t(
                                {defaultMessage: "Hide team-wide mentions ({count})"},
                                {count: team_wide_count},
                            )
                          : $t(
                                {defaultMessage: "Show team-wide mentions ({count})"},
                                {count: team_wide_count},
                            ),
                  }
                : undefined,
        // Handlebars tests only booleans.
        can_mark_all:
            loaded &&
            needs_data.resolvable_items_for_tab(open_items, current_tab, {show_team_wide}).length >
                0,
        is_empty: rows.length === 0,
        empty_text: only_team_wide_waits
            ? $t(
                  {defaultMessage: "Nothing needs you. {count} team-wide mentions are hidden."},
                  {count: team_wide_count},
              )
            : empty_tab_text(current_tab),
        rows,
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
    const group_key = element.dataset["groupKey"];
    if (group_key !== undefined) {
        return `.needs-group-toggle[data-group-key="${CSS.escape(group_key)}"]`;
    }
    if (element.classList.contains("needs-team-wide-toggle")) {
        return ".needs-team-wide-toggle";
    }
    if (element.classList.contains("needs-mark-all")) {
        return ".needs-mark-all";
    }
    const need_id = element.dataset["needId"];
    if (need_id !== undefined && element.classList.contains("needs-mcp-more")) {
        return `.needs-mcp-more[data-need-id="${CSS.escape(need_id)}"]`;
    }
    if (need_id !== undefined && element.classList.contains("needs-card-snooze")) {
        return `.needs-card-snooze[data-need-id="${CSS.escape(need_id)}"]`;
    }
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
    const $card = $cards.eq(Math.min(Math.max(index, 0), $cards.length - 1));
    // A card that holds only a link has no button.
    const $buttons = $card.find("button.needs-btn");
    ($buttons.length > 0 ? $buttons : $card.find(".needs-btn")).last().trigger("focus");
}

function update_nav_count(): void {
    nav_counts.set_count("needs", needs_data.personal_count(open_items));
    // The title of the page carries the same number.
    if (visible) {
        narrow_title.update_narrow_title(undefined);
    }
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

// ----- Handling several items at once -----

// The items come back at the hour the server gave. A page that stays
// open lists them again then, with no click.
const wake_timers = new Map<number, ReturnType<typeof setTimeout>>();

function wake_at(until: Date): void {
    const at = until.getTime();
    const delay = at - Date.now();
    if (wake_timers.has(at) || delay <= 0 || delay > 48 * 60 * 60 * 1000) {
        return;
    }
    wake_timers.set(
        at,
        setTimeout(() => {
            wake_timers.delete(at);
            refresh(visible);
        }, delay + 1000),
    );
}

function remove_items(items: NeedItem[]): void {
    const first_index = Math.min(
        ...items.map((item) => open_items.findIndex((open_item) => open_item.id === item.id)),
    );
    const ids = new Set(items.map((item) => item.id));
    open_items = open_items.filter((item) => !ids.has(item.id));
    complete_rerender();
    update_nav_count();
    focus_card_at(first_index);
}

function show_save_error(retry: () => void): void {
    feedback_widget.show_toast({
        variant: "error",
        text: $t({defaultMessage: "Could not save. Try again."}),
        on_retry: retry,
    });
}

function mark_done_together(items: NeedItem[]): void {
    void (async () => {
        try {
            const result = await needs_data.commit_bulk_done(items);
            const done_ids = new Set(result.done);
            const done = items.filter((item) => done_ids.has(item.id));
            const failed = items.filter((item) => !done_ids.has(item.id));
            if (done.length > 0) {
                remove_items(done);
            }
            if (failed.length > 0) {
                // The failed items stay in the list, and a retry takes
                // only them.
                feedback_widget.show_toast({
                    variant: "error",
                    text: $t(
                        {
                            defaultMessage:
                                "{done} of {total} marked done. {failed} could not be saved.",
                        },
                        {done: done.length, total: items.length, failed: failed.length},
                    ),
                    on_retry() {
                        mark_done_together(failed);
                    },
                });
            } else {
                feedback_widget.show_toast({
                    text: $t({defaultMessage: "{count} marked done."}, {count: done.length}),
                    duration_ms: needs_data.BULK_UNDO_MS,
                    on_undo() {
                        void undo_done_together(done);
                    },
                });
            }
            // What the server did is the truth, whatever it said.
            refresh(visible);
        } catch {
            show_save_error(() => {
                mark_done_together(items);
            });
            refresh(visible);
        }
    })();
}

async function undo_done_together(items: NeedItem[]): Promise<void> {
    try {
        await needs_data.commit_bulk_undo(items);
    } catch {
        show_save_error(() => {
            void undo_done_together(items);
        });
    }
    refresh(true);
}

function snooze_together(items: NeedItem[]): void {
    void (async () => {
        try {
            const until = await needs_data.commit_snooze(items);
            remove_items(items);
            wake_at(until);
            feedback_widget.show_toast({
                text: $t({defaultMessage: "Snoozed until the morning."}),
                duration_ms: needs_data.BULK_UNDO_MS,
                on_undo() {
                    void (async () => {
                        try {
                            await needs_data.commit_unsnooze(items);
                        } catch {
                            show_save_error(() => {
                                snooze_together(items);
                            });
                        }
                        refresh(visible);
                    })();
                },
            });
        } catch {
            show_save_error(() => {
                snooze_together(items);
            });
        }
    })();
}

// A person confirms before many items go at once, and the dialog says
// how many.
function confirm_mark_done(items: NeedItem[]): void {
    if (items.length === 0) {
        return;
    }
    confirm_dialog.launch({
        modal_title_text: $t({defaultMessage: "Mark all done?"}),
        modal_content_html: $t_html(
            {defaultMessage: "{count} items will be marked done. You can undo for 10 seconds."},
            {count: items.length},
        ),
        modal_submit_button_text: $t({defaultMessage: "Mark all done"}),
        on_click() {
            mark_done_together(items);
        },
    });
}

function items_of_group(key: string | undefined): NeedItem[] {
    return needs_data
        .items_for_tab(open_items, current_tab, {show_team_wide})
        .filter((item) => needs_data.group_key_of(item) === key);
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
    if (item.kind === "approval" && needs_data.is_mcp_call(item)) {
        if (choice === REJECT_CHOICE) {
            start_action(
                item,
                async () => needs_data.commit_reject(item),
                $t({defaultMessage: "Rejected. Nothing was run."}),
            );
        } else {
            start_action(
                item,
                async () => needs_data.commit_approve(item),
                $t({defaultMessage: "Approved. {who} can retry now."}, {who}),
            );
        }
    } else if (item.kind === "approval") {
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
    } else if (needs_data.is_card_kind(item.kind)) {
        start_action(
            item,
            async () => needs_data.commit_card_seen(item),
            item.kind === "card_mention"
                ? $t({defaultMessage: "Marked done."})
                : $t({defaultMessage: "Marked as seen."}),
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
    if (item.broadcast && !show_team_wide) {
        // The link is to an item that the filter hides.
        show_team_wide = true;
        complete_rerender();
    }
    // The link is to an item inside a group that is closed.
    const group_key = needs_data.group_key_for_item(
        item,
        needs_data.items_for_tab(open_items, current_tab, {show_team_wide}),
    );
    if (group_key !== undefined && !expanded_groups.has(group_key)) {
        expanded_groups.add(group_key);
        complete_rerender();
    }
    $(`#needs-view .needs-card[data-need-id="${CSS.escape(item.id)}"]`)[0]?.scrollIntoView({
        block: "center",
    });
    if (sub_action === "confirm" && item.kind === "approval" && !item.expired) {
        launch_approval_confirm(item);
    }
}

// ----- Clicks -----

function on_view_click(): void {
    const $view = $("#needs-view");

    $view.on("click", ".needs-tab", function (this: HTMLElement) {
        const tab = needs_data.NEED_TABS.find((value) => value === $(this).attr("data-tab"));
        if (tab !== undefined && tab !== current_tab) {
            current_tab = tab;
            complete_rerender();
        }
    });

    $view.on("click", ".needs-team-wide-toggle", () => {
        show_team_wide = !show_team_wide;
        complete_rerender();
    });

    $view.on("click", ".needs-group-toggle", function (this: HTMLElement) {
        const key = $(this).attr("data-group-key");
        if (key === undefined) {
            return;
        }
        if (expanded_groups.has(key)) {
            expanded_groups.delete(key);
        } else {
            expanded_groups.add(key);
        }
        complete_rerender();
    });

    // Escape closes the group that has focus and puts focus on its
    // button, so a person at the keyboard does not lose their place.
    $view.on("keydown", ".needs-group", function (this: HTMLElement, event) {
        const key = $(this).attr("data-group-key");
        if (event.key !== "Escape" || key === undefined || !expanded_groups.has(key)) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        expanded_groups.delete(key);
        complete_rerender();
        $(`.needs-group-toggle[data-group-key="${CSS.escape(key)}"]`).trigger("focus");
    });

    $view.on("click", ".needs-mark-all", () => {
        confirm_mark_done(
            needs_data.resolvable_items_for_tab(open_items, current_tab, {show_team_wide}),
        );
    });

    $view.on("click", ".needs-group-done", function (this: HTMLElement) {
        confirm_mark_done(items_of_group($(this).attr("data-group-key")));
    });

    $view.on("click", ".needs-group-snooze", function (this: HTMLElement) {
        const items = items_of_group($(this).attr("data-group-key"));
        if (items.length > 0) {
            snooze_together(items);
        }
    });

    $view.on("click", ".needs-card-snooze", function (this: HTMLElement) {
        const need_id = $(this).attr("data-need-id");
        const item = need_id === undefined ? undefined : find_open_item(need_id);
        if (item !== undefined) {
            snooze_together([item]);
        }
    });

    $view.on("click", ".needs-retry", () => {
        load_failed = false;
        complete_rerender();
        refresh(true);
    });

    $view.on("click", ".needs-mcp-more", function (this: HTMLElement) {
        const need_id = $(this).attr("data-need-id");
        if (need_id === undefined) {
            return;
        }
        if (expanded_mcp_calls.has(need_id)) {
            expanded_mcp_calls.delete(need_id);
        } else {
            expanded_mcp_calls.add(need_id);
        }
        complete_rerender();
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
    // A note that mentions the person, a new responsibility, or opening
    // the card on another device changes the list.
    live_updates.on("task_notification", refresh_soon);
    // Marking an item done, or snoozing it, on another device or through
    // an agent changes the list.
    live_updates.on("needs", refresh_soon);
    // Only a mention, a direct message, or a message in a followed topic
    // starts or ends a need, so a message in a room that mentions no one
    // does not load the list.
    let last_message_signal: string | undefined;
    unread_ui.register_update_unread_counts_hook((counts) => {
        const signal = `${counts.mentioned_message_count}/${counts.direct_message_count}/${counts.followed_topic_unread_messages_count}`;
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
    // An address from before the tabs merged names a kind, and opens
    // the tab that now holds it.
    const tab = needs_data.tab_for_hash_arg(args[0]);
    if (tab === undefined) {
        args_to_handle = args;
    } else {
        current_tab = tab;
        args_to_handle = undefined;
        complete_rerender();
    }
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
    const count = needs_data.personal_count(open_items);
    return count > 0
        ? $t({defaultMessage: "Needs you · {count}"}, {count})
        : $t({defaultMessage: "Needs you"});
}
