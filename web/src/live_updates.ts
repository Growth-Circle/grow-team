// Fans out the five Sanji realtime event types. server_events_dispatch.js
// calls dispatch() once for each event of these types. A module that
// needs one of them calls on() once while it initializes, so the
// dispatcher does not have to import every such module.

export type LiveUpdateType =
    | "agent_job"
    | "agent_runner"
    | "room_meta"
    | "realm_permissions"
    | "agent_realm_settings";

export type LiveUpdateEvent = {
    type: LiveUpdateType;
} & Record<string, unknown>;

const listeners = new Map<LiveUpdateType, Set<(event: LiveUpdateEvent) => void>>();

export function on(type: LiveUpdateType, cb: (event: LiveUpdateEvent) => void): void {
    let type_listeners = listeners.get(type);
    if (type_listeners === undefined) {
        type_listeners = new Set();
        listeners.set(type, type_listeners);
    }
    type_listeners.add(cb);
}

export function dispatch(event: LiveUpdateEvent): void {
    for (const cb of listeners.get(event.type) ?? []) {
        cb(event);
    }
}
