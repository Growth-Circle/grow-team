import {current_user} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import * as sub_store from "./sub_store.ts";
import type {Task} from "./task_board_data.ts";

// Mirrors user_can_modify_task in zerver/lib/tasks.py. Task events
// carry one payload for every viewer, so the server cannot tell each
// client whether it may edit; the client works it out the same way.
// The server stays the authority and rejects a change that this
// check let through.
export function can_edit_task(task: Task): boolean {
    if (current_user.is_admin) {
        return true;
    }
    if ([task.creator_id, task.assignee_id, task.reviewer_id].includes(current_user.user_id)) {
        return true;
    }
    if (task.stream_id === null || current_user.is_guest) {
        return false;
    }
    const sub = sub_store.get(task.stream_id);
    return sub !== undefined && stream_data.can_administer_channel(sub);
}

// Mirrors user_can_move_task in zerver/lib/tasks.py. Every card on the
// board is one the user can read, so only the guest rule is left.
export function can_move_task(task: Task): boolean {
    return !current_user.is_guest || can_edit_task(task);
}
