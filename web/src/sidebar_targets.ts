// Where each sidebar entry leads. A page that is not built has a
// fallback that already works, or none. An entry with no fallback stays
// hidden, so no entry opens an empty page.
//
// Set `built: true` for an entry when its page ships.

export type TargetId =
    | "search"
    | "home"
    | "needs"
    | "tasks"
    | "agents"
    | "mcp"
    | "runners"
    | "drive"
    | "settings";

type Target = {
    // The route of the page, without a sub-path.
    hash: string;
    built: boolean;
    // The route to open while the page is not built. `null`: hide the entry.
    fallback_hash: string | null;
};

const targets: Record<TargetId, Target> = {
    search: {hash: "", built: false, fallback_hash: null},
    home: {hash: "#today", built: false, fallback_hash: "#inbox"},
    needs: {hash: "#needs", built: false, fallback_hash: null},
    tasks: {hash: "#tasks", built: true, fallback_hash: null},
    agents: {hash: "#agents", built: false, fallback_hash: null},
    mcp: {hash: "#mcp", built: false, fallback_hash: null},
    runners: {hash: "#runners", built: false, fallback_hash: null},
    drive: {hash: "#drive", built: false, fallback_hash: null},
    settings: {hash: "#workspace-settings", built: false, fallback_hash: "#organization"},
};

const TARGET_IDS: TargetId[] = [
    "home",
    "needs",
    "tasks",
    "agents",
    "mcp",
    "runners",
    "drive",
    "settings",
];

// The route to open for an entry, or `undefined` when the entry is hidden.
// `path` picks a section of a page that has sections, for example "members".
export function get_hash(id: TargetId, path?: string): string | undefined {
    const target = targets[id];
    if (target.built) {
        return path === undefined ? target.hash : `${target.hash}/${path}`;
    }
    return target.fallback_hash ?? undefined;
}

export function is_built(id: TargetId): boolean {
    return targets[id].built;
}

export function is_shown(id: TargetId): boolean {
    const target = targets[id];
    return target.built || target.fallback_hash !== null;
}

function matches_route(hash: string, route: string): boolean {
    return hash === route || hash.startsWith(`${route}/`);
}

// The entry that a browser hash belongs to, or `undefined`.
export function id_for_hash(hash: string): TargetId | undefined {
    for (const id of TARGET_IDS) {
        const route = get_hash(id);
        if (route !== undefined && matches_route(hash, route)) {
            return id;
        }
    }
    return undefined;
}

// The hint on the search button and in the list of shortcuts.
export function search_shortcut_hint(is_mac: boolean): string {
    return is_mac ? "⌘K" : "Ctrl K";
}

export type SidebarRole = "owner" | "admin" | "moderator" | "member" | "guest";

type RoleFlags = {
    is_owner: boolean;
    is_admin: boolean;
    is_moderator?: boolean | undefined;
    is_guest: boolean;
};

// The role of a user, read from the flags that the page already has.
// They are there before any request finishes, and they are the flags
// that the server changes when a role changes.
export function role_of(user: RoleFlags): SidebarRole {
    if (user.is_owner) {
        return "owner";
    }
    if (user.is_admin) {
        return "admin";
    }
    if (user.is_moderator === true) {
        return "moderator";
    }
    return user.is_guest ? "guest" : "member";
}

export function can_manage_workspace(role: SidebarRole): boolean {
    return role === "owner" || role === "admin";
}
