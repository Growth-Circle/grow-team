// Badge counts for the sidebar nav items that show one: Needs you,
// Tasks, MCP connections, and Runners. The sidebar and any other
// surface that shows the same number read it from here, and a live
// update handler can change a count without importing the sidebar.

export type NavCountId = "needs" | "tasks" | "mcp" | "runners";

const counts = new Map<NavCountId, number>();
const change_listeners = new Set<(id: NavCountId, count: number) => void>();

export function set_count(id: NavCountId, count: number): void {
    if (counts.get(id) === count) {
        return;
    }
    counts.set(id, count);
    for (const cb of change_listeners) {
        cb(id, count);
    }
}

export function get_count(id: NavCountId): number {
    return counts.get(id) ?? 0;
}

export function on_change(cb: (id: NavCountId, count: number) => void): void {
    change_listeners.add(cb);
}
