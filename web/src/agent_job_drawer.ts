import $ from "jquery";
import * as z from "zod/mini";

import * as agent_avatars from "./agent_avatars.ts";
import {drawer_step_index, drawer_step_labels} from "./agent_job_labels.ts";
import * as browser_history from "./browser_history.ts";
import * as channel from "./channel.ts";
import * as hash_util from "./hash_util.ts";
import * as message_store from "./message_store.ts";
import * as stream_data from "./stream_data.ts";

/*
    Parts of the job drawer (spec 09 E2, DR-48..65) that draw the shape
    of the agent, the five steps, and the link to the room of the job.
    agent_job_panel.ts calls these from its render function.
*/

const SHAPE_CLASSES =
    "sj-job-drawer__shape--circle sj-job-drawer__shape--ring sj-job-drawer__shape--box";

// DR-49..51: the agent is a circle, a ring, or a rounded square, in the
// color of the agent. An agent that the directory does not list yet has
// no shape, so the head shows only the text.
export function render_shape($shape: JQuery, bot_user_id: number | undefined): void {
    const agent =
        bot_user_id === undefined
            ? undefined
            : agent_avatars.get_agent_for_bot_user_id(bot_user_id);
    $shape.removeClass(SHAPE_CLASSES).prop("hidden", agent === undefined);
    if (agent === undefined) {
        return;
    }
    $shape.addClass(`sj-job-drawer__shape--${agent.shape}`);
    if (agent.color === "") {
        $shape.css("--sj-job-shape-color", "");
    } else {
        $shape.css("--sj-job-shape-color", agent.color);
    }
}

// DR-58..61 and DR-56, DR-57: the steps before the active step have a
// check, the active step has a dot, and the rest are empty.
export function render_steps($list: JQuery, $fill: JQuery, progress: number, status: string): void {
    const active = drawer_step_index(progress, status);
    $list.empty();
    for (const [index, label] of drawer_step_labels().entries()) {
        const state = index < active ? "done" : index === active ? "active" : "todo";
        const $item = $("<li>").addClass("sj-job-drawer__step").attr("data-state", state);
        $("<span>")
            .addClass("sj-job-drawer__step-mark")
            .attr("aria-hidden", "true")
            .text(state === "done" ? "✓" : state === "active" ? "•" : "")
            .appendTo($item);
        $("<span>").addClass("sj-job-drawer__step-label").text(label).appendTo($item);
        if (state === "active") {
            $item.attr("aria-current", "step");
        }
        $list.append($item);
    }
    const percent = Math.max(0, Math.min(100, Math.round(progress * 100)));
    $fill.css("width", `${percent}%`);
    $fill.parent().attr("aria-valuenow", String(percent));
}

const message_response_schema = z.object({
    message: z.object({
        type: z.string(),
        stream_id: z.optional(z.number()),
    }),
});

export type RoomRef = {stream_id: number; name: string};

// The room (channel) that a job belongs to, read from the message that
// asked for it. A direct message has no room, so it gives undefined.
export async function room_for_message(message_id: number): Promise<RoomRef | undefined> {
    let stream_id: number | undefined;
    const known = message_store.get(message_id);
    if (known) {
        stream_id = known.type === "stream" ? known.stream_id : undefined;
    } else {
        try {
            const result: unknown = await channel.get({
                url: `/json/messages/${message_id}`,
                data: {apply_markdown: false},
            });
            const {message} = message_response_schema.parse(result);
            stream_id = message.type === "stream" ? message.stream_id : undefined;
        } catch {
            return undefined;
        }
    }
    if (stream_id === undefined) {
        return undefined;
    }
    const sub = stream_data.get_sub_by_id(stream_id);
    return sub ? {stream_id, name: sub.name} : undefined;
}

export function open_room(room: RoomRef): void {
    browser_history.go_to_location(hash_util.by_stream_url(room.stream_id));
}
