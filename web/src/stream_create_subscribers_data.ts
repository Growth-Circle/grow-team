import * as people from "./people.ts";
import type {User} from "./people.ts";
import {current_user} from "./state_data.ts";

let user_id_set: Set<number>;

export function initialize_with_current_user(): void {
    user_id_set = new Set([current_user.user_id]);
}

export function get_principals(): number[] {
    // Return list of user ids which were selected by user.
    return [...user_id_set];
}

export function get_potential_subscribers(): User[] {
    const potential_subscribers = people.get_realm_users();
    return potential_subscribers.filter((user) => !user_id_set.has(user.user_id));
}

// "All team" means the active members of the organization. Bots are
// left out, as nobody expects a new channel to start with every
// integration in it. Guests are left out too: they are people from
// outside the team, such as clients, and each one should be added by
// choice.
function get_team_members(): User[] {
    return people.get_realm_active_human_users().filter((user) => !user.is_guest);
}

export function get_team_size(): number {
    return get_team_members().length;
}

export function get_team_members_not_yet_added(): User[] {
    return get_team_members().filter((user) => !user_id_set.has(user.user_id));
}

export function add_user_ids(user_ids: number[]): void {
    for (const user_id of user_ids) {
        if (!user_id_set.has(user_id) && people.maybe_get_user_by_id(user_id)) {
            user_id_set.add(user_id);
        }
    }
}

export function sync_user_ids(user_ids: number[]): void {
    user_id_set = new Set(user_ids);
}
