// The room header (RM-02..10) and the summary banner (RM-19..23): room
// name and type, owner and member counts, the Kaki summary switch, and
// Kaki's summary of the day.
//
// A room does not go through center_views.ts (see the comment there).
// message_view.ts calls show() and hide() where it narrows to a
// channel, or away from it.

import $ from "jquery";
import * as z from "zod/mini";

import render_room_header from "../templates/room_header.hbs";
import render_room_summary_banner from "../templates/room_summary_banner.hbs";

import * as channel from "./channel.ts";
import {show_toast} from "./feedback_widget.ts";
import {$t} from "./i18n.ts";
import * as live_updates from "./live_updates.ts";
import type {StreamSubscription} from "./sub_store.ts";

const meta_schema = z.object({
    folder_name: z.nullable(z.string()),
    owner: z.nullable(z.object({id: z.number(), full_name: z.string()})),
    summary_enabled: z.boolean(),
    people_count: z.number(),
    agent_count: z.number(),
    whatsapp: z.boolean(),
    can_toggle_summary: z.boolean(),
});
type Meta = z.infer<typeof meta_schema>;

const digest_schema = z.object({
    digest: z.nullable(z.object({message_count: z.number(), summary: z.string()})),
});

// ponytail: the back link (RM-04) and the Drive chip (RM-09) lead to
// the Today and Drive screens, which are still placeholders. Set each
// flag to true in the change that ships that screen.
const show_back_link = false;
const show_drive_link = false;

// The room type comes from the channel folder: Proyek, Klien, or Tim.
// A folder with another name shows as a neutral chip.
const TYPE_KINDS = new Map([
    ["proyek", "project"],
    ["project", "project"],
    ["klien", "client"],
    ["client", "client"],
    ["tim", "team"],
    ["team", "team"],
]);

let current_stream: StreamSubscription | undefined;
let current_meta: Meta | undefined;
let toggle_in_flight = false;
// Set once the server answers that it has no summaries to read.
let digest_missing = false;
let bound = false;

function members_line(meta: Meta): string {
    // "4 people · 3 agents · WA" (RM-07). Each count is its own
    // translatable fragment. The "·" is a fixed separator.
    const parts = [
        $t(
            {defaultMessage: "{N, plural, one {# person} other {# people}}"},
            {N: meta.people_count},
        ),
        $t({defaultMessage: "{N, plural, one {# agent} other {# agents}}"}, {N: meta.agent_count}),
    ];
    if (meta.whatsapp) {
        parts.push("WA");
    }
    return parts.join(" · ");
}

function owner_line(meta: Meta): string {
    const members = members_line(meta);
    if (meta.owner === null) {
        return members;
    }
    return $t(
        {defaultMessage: "Owner: {owner_name} · {members}"},
        {owner_name: meta.owner.full_name, members},
    );
}

function render_header(stream: StreamSubscription, meta: Meta): void {
    const folder_name = meta.folder_name?.trim();
    $("#room-header").html(
        render_room_header({
            stream_name: stream.name,
            show_back_link,
            show_drive_link,
            type_label: folder_name,
            type_kind:
                folder_name === undefined ? undefined : TYPE_KINDS.get(folder_name.toLowerCase()),
            owner_line: owner_line(meta),
            summary_enabled: meta.summary_enabled,
            can_toggle_summary: meta.can_toggle_summary,
            toggle_disabled_reason: $t({
                defaultMessage: "Only the room owner can change the summary.",
            }),
        }),
    );
}

function render_banner(digest: {message_count: number; summary: string} | null): void {
    const $banner = $("#room-summary-banner");
    if (digest === null || digest.summary === "") {
        $banner.empty();
        return;
    }
    $banner.html(
        render_room_summary_banner({
            message_count: digest.message_count,
            summary: digest.summary,
        }),
    );
}

async function load(stream: StreamSubscription, {quiet}: {quiet: boolean}): Promise<void> {
    const stream_id = stream.stream_id;
    try {
        const meta = meta_schema.parse(await channel.get({url: `/json/streams/${stream_id}/meta`}));
        if (current_stream?.stream_id !== stream_id) {
            // The user left this room while the header loaded.
            return;
        }
        current_meta = meta;
        render_header(stream, meta);
    } catch {
        if (current_stream?.stream_id === stream_id) {
            if (!quiet) {
                $("#room-header").empty();
            }
            show_toast({
                text: $t({defaultMessage: "Could not load this room."}),
                variant: "error",
                on_retry() {
                    void load(stream, {quiet: false});
                },
            });
        }
        return;
    }
    await load_banner(stream_id);
}

async function load_banner(stream_id: number): Promise<void> {
    if (current_meta?.summary_enabled !== true || digest_missing) {
        render_banner(null);
        return;
    }
    try {
        const {digest} = digest_schema.parse(
            await channel.get({url: `/json/streams/${stream_id}/digest`}),
        );
        if (current_stream?.stream_id === stream_id) {
            render_banner(digest);
        }
    } catch (error) {
        // No summary to show: the banner stays empty and the room
        // works as it did before. A server without summaries is asked
        // once for the page.
        if (typeof error === "object" && error !== null && "status" in error) {
            digest_missing = error.status === 404;
        }
        render_banner(null);
    }
}

async function set_summary(enabled: boolean): Promise<void> {
    const stream = current_stream;
    if (stream === undefined || current_meta?.can_toggle_summary !== true || toggle_in_flight) {
        return;
    }
    toggle_in_flight = true;
    $("#room-header .room-header-summary-toggle").attr("aria-busy", "true");
    try {
        const meta = meta_schema.parse(
            await channel.patch({
                url: `/json/streams/${stream.stream_id}/meta`,
                data: {summary_enabled: enabled},
            }),
        );
        if (current_stream?.stream_id === stream.stream_id) {
            current_meta = meta;
            render_header(stream, meta);
            void load_banner(stream.stream_id);
        }
        show_toast({
            text: meta.summary_enabled
                ? $t({defaultMessage: "Summary is on. Kaki summarizes this room each morning."})
                : $t({
                      defaultMessage: "Summary is off. Kaki stops reading this room for summaries.",
                  }),
            variant: "success",
        });
    } catch {
        // The switch keeps its old state: the screen changes only
        // after the server accepts the change.
        $("#room-header .room-header-summary-toggle").removeAttr("aria-busy");
        show_toast({
            text: $t({defaultMessage: "Could not change the summary."}),
            variant: "error",
            on_retry() {
                void set_summary(enabled);
            },
        });
    } finally {
        toggle_in_flight = false;
    }
}

function bind_once(): void {
    if (bound) {
        return;
    }
    bound = true;
    $("#room-header").on("click", ".room-header-back", () => {
        window.location.hash = "#today";
    });
    $("#room-header").on("click", ".room-header-drive", () => {
        if (current_stream !== undefined) {
            window.location.hash = `#drive/room/${current_stream.stream_id}`;
        }
    });
    $("#room-header").on("click", ".room-header-summary-toggle", () => {
        if (current_meta?.can_toggle_summary === true) {
            void set_summary(!current_meta.summary_enabled);
        }
    });
    // A room_meta event for the open room refreshes the header. An event
    // for another room changes nothing on screen.
    live_updates.on("room_meta", (event) => {
        if (current_stream !== undefined && event["stream_id"] === current_stream.stream_id) {
            void load(current_stream, {quiet: true});
        }
    });
}

export function show(stream: StreamSubscription): void {
    bind_once();
    $(".column-middle-inner").addClass("sj-room-active");
    $("#compose").addClass("sj-room-composer");
    if (current_stream?.stream_id === stream.stream_id && current_meta !== undefined) {
        // A topic switch inside the same room keeps the header as it is.
        return;
    }
    current_stream = stream;
    current_meta = undefined;
    $("#room-summary-banner").empty();
    $("#room-header").html(render_room_header({loading: true}));
    void load(stream, {quiet: false});
}

export function hide(): void {
    current_stream = undefined;
    current_meta = undefined;
    $(".column-middle-inner").removeClass("sj-room-active");
    $("#compose").removeClass("sj-room-composer");
    $("#room-summary-banner").empty();
    $("#room-header").empty();
}
