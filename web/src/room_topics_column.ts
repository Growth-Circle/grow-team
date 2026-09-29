// The room topic column (RM-11..16): the topics of the open room with
// their message counts, and the active topic in ink. A room opened
// without a topic goes to its top (most recently active) topic.
//
// A room does not go through center_views.ts (see the comment there).
// message_view.ts calls show() and hide() where it narrows to a
// channel, or away from it.

import $ from "jquery";
import * as z from "zod/mini";

import render_room_topics_column from "../templates/room_topics_column.hbs";

import * as browser_history from "./browser_history.ts";
import * as channel from "./channel.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as util from "./util.ts";

const topics_schema = z.object({
    topics: z.array(z.object({name: z.string(), max_id: z.number(), message_count: z.number()})),
});
type Topic = z.infer<typeof topics_schema>["topics"][number];

let current_stream_id: number | undefined;
let current_topic: string | undefined;
// The last list per room. A topic switch draws from it at once, then
// a fetch refreshes it, so a new topic shows up without a flash.
const cached_topics = new Map<number, Topic[]>();

function render(stream_id: number, topics: Topic[]): void {
    const active = current_topic?.toLowerCase();
    $("#room-topics-column").html(
        render_room_topics_column({
            topics: topics.map((topic) => {
                const is_active = topic.name.toLowerCase() === active;
                return {
                    name: util.get_final_topic_display_name(topic.name),
                    is_active,
                    meta: is_active
                        ? $t({defaultMessage: "Open"})
                        : $t(
                              {defaultMessage: "{N, plural, one {# message} other {# messages}}"},
                              {N: topic.message_count},
                          ),
                    url: hash_util.by_stream_topic_url(stream_id, topic.name),
                };
            }),
        }),
    );
}

// Goes to the top topic of a room that was opened without a topic. It
// replaces the history entry: with a normal entry, Back would return to
// the bare room and redirect again.
function open_top_topic(stream_id: number, topics: Topic[]): void {
    window.location.replace(
        browser_history.get_full_url(hash_util.by_stream_topic_url(stream_id, topics[0]!.name)),
    );
}

// `open_top` is true when the narrow is the bare room: no topic and no
// other term, such as a message id to jump to.
export function show(stream_id: number, topic: string | undefined, open_top: boolean): void {
    current_stream_id = stream_id;
    current_topic = topic;
    const $column = $("#room-topics-column");
    $column
        .attr("role", "navigation")
        .attr("aria-label", $t({defaultMessage: "Topics"}))
        .show();

    const cached = cached_topics.get(stream_id);
    if (cached === undefined) {
        $column.html(render_room_topics_column({loading: true}));
    } else if (open_top && cached.length > 0) {
        open_top_topic(stream_id, cached);
        return;
    } else {
        render(stream_id, cached);
    }

    void (async () => {
        let topics;
        try {
            topics = topics_schema.parse(
                await channel.get({url: `/json/streams/${stream_id}/topics`}),
            ).topics;
        } catch {
            if (cached === undefined && current_stream_id === stream_id) {
                // Do not leave the loading placeholder on screen.
                $column.empty();
            }
            return;
        }
        cached_topics.set(stream_id, topics);
        if (current_stream_id !== stream_id) {
            // The user left this room while the list loaded.
            return;
        }
        if (open_top && cached === undefined && topics.length > 0) {
            open_top_topic(stream_id, topics);
            return;
        }
        render(stream_id, topics);
    })();
}

export function hide(): void {
    current_stream_id = undefined;
    current_topic = undefined;
    $("#room-topics-column").empty();
}
