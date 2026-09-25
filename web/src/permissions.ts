import * as z from "zod/mini";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import * as live_updates from "./live_updates.ts";

// Client copy of the workspace permission matrix. It only helps the
// UI hide a control before a request fails: the server checks the
// same permission on every request.

const role_schema = z.enum(["owner", "admin", "moderator", "member", "guest"]);
export type Role = z.infer<typeof role_schema>;

const permission_cell_schema = z.object({
    allowed: z.boolean(),
    locked: z.boolean(),
    reason: z.nullable(z.string()),
});

const permission_row_schema = z.object({
    key: z.string(),
    group: z.string(),
    cells: z.record(z.string(), permission_cell_schema),
});

const realm_permissions_schema = z.object({
    my_role: role_schema,
    permissions: z.array(permission_row_schema),
});

// Until the first load succeeds, every permission is denied and the
// role is the least privileged one. A gate that fails this way only
// hides a control; it never offers one that the server refuses.
let role: Role = "guest";
let permission_rows = new Map<string, z.infer<typeof permission_row_schema>>();
let initialized = false;
let latest_request_id = 0;

async function fetch_and_apply(): Promise<void> {
    latest_request_id += 1;
    const request_id = latest_request_id;
    let data: z.infer<typeof realm_permissions_schema>;
    try {
        const raw_data: unknown = await channel.get({url: "/json/realm/permissions"});
        data = realm_permissions_schema.parse(raw_data);
    } catch (error) {
        blueslip.warn("permissions: could not load /json/realm/permissions.", {error});
        return;
    }
    if (request_id !== latest_request_id) {
        // Two changes arrived close together, and the answer to the
        // older request came back last. The newer answer applies.
        return;
    }
    role = data.my_role;
    permission_rows = new Map(data.permissions.map((row) => [row.key, row]));
}

export async function initialize(): Promise<void> {
    if (!initialized) {
        initialized = true;
        live_updates.on("realm_permissions", () => {
            void fetch_and_apply();
        });
    }
    await fetch_and_apply();
}

// server_events_dispatch.js calls this for the two Zulip events that
// can change the matrix: a realm update_dict, and a realm_user update
// for the current user. Before initialize() there is nothing to
// refresh; a spectator never calls initialize() at all.
export function refetch(): void {
    if (!initialized) {
        return;
    }
    void fetch_and_apply();
}

export function can(key: string): boolean {
    return permission_rows.get(key)?.cells[role]?.allowed ?? false;
}

export function my_role(): Role {
    return role;
}
