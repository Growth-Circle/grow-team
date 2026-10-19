import * as z from "zod/mini";

import * as channel from "./channel.ts";
import * as live_updates from "./live_updates.ts";

/*

The Status panel of an agent (V3). This module keeps the status that the
server sent for each agent, loads it, and reloads it when an `agent_job`
event names the agent that the open conversation is with. The button in
the conversation and the drawer both read from here, so they show the same
numbers.

*/

const status_item_schema = z.object({
    id: z.string(),
    kind: z.enum(["job", "approval", "decision"]),
    title: z.string(),
    status: z.nullable(z.string()),
    outcome: z.nullable(z.enum(["approved", "rejected", "answered"])),
    detail: z.nullable(z.string()),
    time: z.string(),
    job_id: z.nullable(z.string()),
    message_id: z.nullable(z.number()),
    stream_id: z.nullable(z.number()),
    topic: z.nullable(z.string()),
});

export const agent_status_response_schema = z.object({
    in_progress: z.array(status_item_schema),
    waiting_on_you: z.array(status_item_schema),
    decided: z.array(status_item_schema),
});

export type AgentStatusItem = z.infer<typeof status_item_schema>;
export type AgentStatus = z.infer<typeof agent_status_response_schema>;

export type SectionKey = keyof AgentStatus;

export const SECTION_KEYS: SectionKey[] = ["in_progress", "waiting_on_you", "decided"];

// A burst of events from one job makes one request.
const RELOAD_DELAY_MS = 500;

const status_by_profile_id = new Map<string, AgentStatus>();
const failed_profile_ids = new Set<string>();
const listeners = new Set<(profile_id: string) => void>();
// The newest request of each agent wins. An older answer that comes late
// would show a job in a state that it has left.
const latest_request = new Map<string, number>();
let request_counter = 0;
// The agent of the conversation that is open, whose panel follows events.
let watched_profile_id: string | undefined;

// The status of an agent, or undefined while it is not loaded.
export function get_status(profile_id: string): AgentStatus | undefined {
    return status_by_profile_id.get(profile_id);
}

function notify(profile_id: string): void {
    for (const listener of listeners) {
        listener(profile_id);
    }
}

export function set_status(profile_id: string, status: AgentStatus): void {
    status_by_profile_id.set(profile_id, status);
    failed_profile_ids.delete(profile_id);
    notify(profile_id);
}

// Whether the newest load of an agent's status failed. The status that was
// loaded before can be out of date, so a client shows the failure, not it.
export function has_load_failed(profile_id: string): boolean {
    return failed_profile_ids.has(profile_id);
}

function set_failed(profile_id: string): void {
    failed_profile_ids.add(profile_id);
    notify(profile_id);
}

// Tells when the status of an agent changes. It returns a function that
// stops the listener.
export function on_change(listener: (profile_id: string) => void): () => void {
    listeners.add(listener);
    return () => {
        listeners.delete(listener);
    };
}

export function load(profile_id: string): void {
    request_counter += 1;
    const request_id = request_counter;
    latest_request.set(profile_id, request_id);
    void channel.get({
        url: `/json/agent/profiles/${profile_id}/status`,
        success(raw_data) {
            if (latest_request.get(profile_id) !== request_id) {
                return;
            }
            const parsed = agent_status_response_schema.safeParse(raw_data);
            if (parsed.success) {
                set_status(profile_id, parsed.data);
            } else {
                set_failed(profile_id);
            }
        },
        error() {
            if (latest_request.get(profile_id) === request_id) {
                set_failed(profile_id);
            }
        },
    });
}

// Follows the agent of the open conversation, or none.
export function watch(profile_id: string | undefined): void {
    watched_profile_id = profile_id;
}

let reload_timer: ReturnType<typeof setTimeout> | undefined;

function reload_watched_soon(): void {
    clearTimeout(reload_timer);
    reload_timer = setTimeout(() => {
        if (watched_profile_id !== undefined) {
            load(watched_profile_id);
        }
    }, RELOAD_DELAY_MS);
}

export function initialize(): void {
    live_updates.on("agent_job", (event) => {
        if (watched_profile_id !== undefined && is_event_for_profile(event, watched_profile_id)) {
            reload_watched_soon();
        }
    });
}

// How many items wait on the user, or undefined while the status is not
// loaded, so the button shows no number rather than a wrong zero.
export function waiting_count(profile_id: string): number | undefined {
    return status_by_profile_id.get(profile_id)?.waiting_on_you.length;
}

export function clear(): void {
    status_by_profile_id.clear();
    failed_profile_ids.clear();
    latest_request.clear();
    watched_profile_id = undefined;
}

export function is_job_id(value: string): boolean {
    return /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(value);
}

// The server sends job_id as a UUID. A value of any other form
// has no link, so a bad value never reaches an address.
export function job_link_id(item: AgentStatusItem): string | undefined {
    return item.job_id !== null && is_job_id(item.job_id) ? item.job_id : undefined;
}

// An agent_job event changes this agent's panel when it names the agent's
// profile. An event without a profile (an older server) changes nothing.
export function is_event_for_profile(event: Record<string, unknown>, profile_id: string): boolean {
    return event["profile_id"] === profile_id;
}

// The agent profile of a conversation, when it is a 1:1 direct message
// with an agent bot. `other_user_ids` are the people of the narrow other
// than the user. A group, a person and a regular bot give undefined.
export function dm_agent_profile_id(
    other_user_ids: number[] | undefined,
    profile_id_for_bot_user_id: (user_id: number) => string | undefined,
): string | undefined {
    if (other_user_ids?.length !== 1) {
        return undefined;
    }
    return profile_id_for_bot_user_id(other_user_ids[0]!);
}
