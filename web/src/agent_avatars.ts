// Looks up an agent's avatar shape and color by its bot user id, so a
// room (RM-24..34) can draw a shaped avatar and an AGENT label for
// agent senders instead of the stock round avatar image.
//
// The list loads once per page session from the agent directory. A
// message can render before that fetch resolves. When it resolves with
// agents, this triggers one full re-render so those messages pick up
// their shape.

import * as z from "zod/mini";

import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as message_live_update from "./message_live_update.ts";
import * as peer_data from "./peer_data.ts";
import * as util from "./util.ts";

export type AgentAvatarShape = "circle" | "ring" | "box";

export type AgentAvatarInfo = {
    name: string;
    shape: AgentAvatarShape;
    color: string;
};

// The directory returns more fields per profile. Only these matter here.
const profiles_schema = z.object({
    profiles: z.array(
        z.object({
            bot_user_id: z.number(),
            name: z.string(),
            avatar_shape: z.optional(z.string()),
            avatar_color: z.optional(z.string()),
        }),
    ),
});

const by_bot_user_id = new Map<number, AgentAvatarInfo>();
const loaded_callbacks: (() => void)[] = [];
let load_promise: Promise<void> | undefined;

// Runs `callback` after the agent list loads, for text that names
// agents and was drawn before they were known.
export function on_loaded(callback: () => void): void {
    loaded_callbacks.push(callback);
}

function to_shape(value: string | undefined): AgentAvatarShape {
    return value === "ring" || value === "box" ? value : "circle";
}

// The color goes into a style attribute, so anything but a plain hex
// color counts as no color.
function to_color(value: string | undefined): string {
    return value !== undefined && /^#[\da-f]{6}$/i.test(value) ? value : "";
}

async function load(): Promise<void> {
    try {
        const data: unknown = await channel.get({url: "/json/agent/profiles?offset=0&limit=50"});
        const {profiles} = profiles_schema.parse(data);
        for (const profile of profiles) {
            by_bot_user_id.set(profile.bot_user_id, {
                name: profile.name,
                shape: to_shape(profile.avatar_shape),
                color: to_color(profile.avatar_color),
            });
        }
        if (profiles.length > 0) {
            message_live_update.rerender_messages_view();
            for (const callback of loaded_callbacks) {
                callback();
            }
        }
    } catch {
        // ponytail: a failed fetch leaves agents in the stock
        // rendering until the next page load. Add a retry if this
        // proves noisy.
    }
}

export async function ensure_loaded(): Promise<void> {
    load_promise ??= load();
    await load_promise;
}

function start_loading_once(): void {
    if (load_promise === undefined) {
        void ensure_loaded();
    }
}

export function get_agent_for_bot_user_id(user_id: number): AgentAvatarInfo | undefined {
    start_loading_once();
    return by_bot_user_id.get(user_id);
}

// RM-51: up to 3 "@Name" mentions for the composer placeholder, such
// as "@Kaki, @Ayame, or @Matcha". Returns undefined when no known agent
// subscribes to the room, so the caller keeps the plain placeholder.
export function compose_mention_hint(stream_id: number, max = 3): string | undefined {
    start_loading_once();
    const names: string[] = [];
    for (const [user_id, agent] of by_bot_user_id) {
        if (peer_data.is_user_loaded_and_subscribed(stream_id, user_id)) {
            names.push(`@${agent.name}`);
        }
        if (names.length >= max) {
            break;
        }
    }
    if (names.length === 0) {
        return undefined;
    }
    // The composer names agents to pick one from, so the list reads
    // "A, B, or C" (a disjunction), not "A, B, and C".
    return util.format_array_as_list(names, "long", "disjunction");
}

// RM-51: the placeholder of the room composer.
export function room_composer_placeholder(stream_id: number, topic: string): string {
    const topic_name = util.get_final_topic_display_name(topic);
    const agent_mentions = compose_mention_hint(stream_id);
    if (agent_mentions === undefined) {
        return $t({defaultMessage: "Write in {topic_name}…"}, {topic_name});
    }
    return $t(
        {defaultMessage: "Write in {topic_name}… type {agent_mentions} to call an agent"},
        {topic_name, agent_mentions},
    );
}

// Two-letter initials for a room avatar (RM-24..34). This mirrors
// pm_list_dom.ts and people.ts, which keep their own private copy.
export function initials_for_name(full_name: string): string {
    const words = full_name.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) {
        return "";
    }
    if (words.length === 1) {
        return words[0]!.slice(0, 2).toUpperCase();
    }
    return (words[0]![0]! + words.at(-1)![0]!).toUpperCase();
}
