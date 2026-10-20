import $ from "jquery";

import render_sidebar_rooms from "../templates/sidebar_rooms.hbs";

import * as app_actions from "./app_actions.ts";
import * as channel_folders from "./channel_folders.ts";
import {$t} from "./i18n.ts";
import * as live_updates from "./live_updates.ts";
import {localstorage} from "./localstorage.ts";
import * as quiet_rooms from "./quiet_rooms.ts";
import * as room_create_modal from "./room_create_modal.ts";
import {current_user} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import type {StreamListSection} from "./stream_list_sort.ts";
import * as sub_store from "./sub_store.ts";
import * as ui_util from "./ui_util.ts";

// The ROOMS block: the "+" button, the color of each group's dot, the
// one call to action for a workspace with no rooms, the list of
// quiet rooms below the groups, and the button that closes the block.

// The folders that the workspace setup creates, and the dot color of each.
const FOLDER_ACCENTS = new Map([
    ["Proyek", "var(--sj-kaki)"],
    ["Klien", "var(--sj-ayame)"],
    ["Tim", "var(--sj-matcha)"],
]);
const PINNED_ACCENT = "var(--sj-kuning)";
const OTHER_ACCENT = "var(--sj-sidebar-text-muted)";

export function accent_for_section(
    section: {id: string; folder_id: number | null},
    folders: readonly {id: number; name: string}[],
): string {
    if (section.id === "pinned-streams") {
        return PINNED_ACCENT;
    }
    const folder_name = folders.find((folder) => folder.id === section.folder_id)?.name;
    return (
        (folder_name === undefined ? undefined : FOLDER_ACCENTS.get(folder_name)) ?? OTHER_ACCENT
    );
}

// Sets a custom property on each group's container. left_sidebar.css
// paints the group's dot with it, so stream_list.ts and its templates
// do not need to know the colors.
export function apply_section_accents(sections: StreamListSection[]): void {
    const folders = channel_folders.get_channel_folders();
    for (const section of sections) {
        document
            .querySelector<HTMLElement>(`#stream-list-${CSS.escape(section.id)}-container`)
            ?.style.setProperty("--sj-section-accent", accent_for_section(section, folders));
    }
}

// A workspace with no rooms shows one "Create room" button and nothing else.
export function update_no_rooms_cta(): void {
    const has_no_rooms = stream_data.num_subscribed_subs() === 0;
    $("#sidebar-no-rooms-cta").toggleClass("hidden", !has_no_rooms);
    $("#sidebar-rooms-header").toggleClass("hidden", has_no_rooms);
}

type QuietRoomRow = {name: string; idle_label: string};

export function quiet_room_rows(
    quiet_channels: readonly quiet_rooms.QuietChannel[],
    get_name: (stream_id: number) => string | undefined,
    now_seconds: number,
): QuietRoomRow[] {
    const rows: QuietRoomRow[] = [];
    for (const quiet_channel of quiet_channels) {
        const name = get_name(quiet_channel.stream_id);
        if (name !== undefined) {
            const days = Math.floor((now_seconds - quiet_channel.last_message_at) / 86400);
            rows.push({name, idle_label: $t({defaultMessage: "{days} days"}, {days})});
        }
    }
    return rows;
}

const ls = localstorage();
const rooms_collapsed_ls_key = "left_sidebar_rooms_collapsed";
let rooms_unread_count = 0;

// A closed block shows the unread count of the rooms it hides. A search
// opens it, so the rooms that match are in sight.
export function get_rooms_header_state(opts: {
    saved_collapsed: boolean;
    search_active: boolean;
    unread_count: number;
}): {collapsed: boolean; show_unread: boolean} {
    const collapsed = opts.saved_collapsed && !opts.search_active;
    return {collapsed, show_unread: collapsed && opts.unread_count > 0};
}

export function update_rooms_collapsed_state(): void {
    const state = get_rooms_header_state({
        saved_collapsed: ls.get(rooms_collapsed_ls_key) === true,
        search_active: ui_util.get_left_sidebar_search_term() !== "",
        unread_count: rooms_unread_count,
    });
    $("#left_sidebar_scroll_container").toggleClass("rooms-collapsed", state.collapsed);
    $("#sidebar-rooms-toggle").attr("aria-expanded", String(!state.collapsed));
    $("#sidebar-rooms-header .sidebar-rooms-unread")
        .toggleClass("hide", !state.show_unread)
        .text(state.show_unread ? String(rooms_unread_count) : "");
}

export function toggle_rooms_section(): void {
    ls.set(rooms_collapsed_ls_key, ls.get(rooms_collapsed_ls_key) !== true);
    update_rooms_collapsed_state();
}

export function update_rooms_unread_count(count: number): void {
    rooms_unread_count = count;
    update_rooms_collapsed_state();
}

let is_expanded = false;

function render_quiet_rooms(): void {
    // Guests do not see quiet rooms.
    const rows = current_user.is_guest
        ? []
        : quiet_room_rows(
              quiet_rooms.get_quiet_channels(),
              (stream_id) => sub_store.get(stream_id)?.name,
              Date.now() / 1000,
          );
    $("#sidebar-quiet-rooms").html(
        rows.length === 0
            ? ""
            : render_sidebar_rooms({
                  toggle_label: $t(
                      {defaultMessage: "{count} quiet rooms, over {threshold} days"},
                      {count: rows.length, threshold: quiet_rooms.get_threshold_days()},
                  ),
                  is_expanded,
                  rooms: rows,
              }),
    );
}

export function initialize(): void {
    const open_room_modal = (event: JQuery.ClickEvent): void => {
        event.preventDefault();
        room_create_modal.open();
    };
    $("#left-sidebar-new-room-button").on("click", open_room_modal);
    $("#sidebar-no-rooms-cta").on("click", ".sidebar-no-rooms-button", open_room_modal);
    $("#sidebar-quiet-rooms").on("click", "#sidebar-quiet-rooms-toggle", () => {
        is_expanded = !is_expanded;
        render_quiet_rooms();
    });
    update_no_rooms_cta();
    $("#sidebar-rooms-toggle").on("click", toggle_rooms_section);
    update_rooms_collapsed_state();

    quiet_rooms.on_change(render_quiet_rooms);
    quiet_rooms.refresh();
    live_updates.on("room_meta", quiet_rooms.refresh);

    app_actions.register({
        name: "new_room",
        label: $t({defaultMessage: "New room"}),
        permission: "room_create",
        run() {
            room_create_modal.open();
        },
    });
}
