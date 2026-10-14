import {$t} from "./i18n.ts";
import * as people from "./people.ts";
import type {TaskHistoryEntry} from "./task_board_data.ts";

function history_string(entry: TaskHistoryEntry, key: string): string {
    const value = entry.extra_data[key];
    return typeof value === "string" ? value : "";
}

function name_of(user_id: number | null, fallback: string): string {
    if (user_id === null) {
        return fallback;
    }
    // Deactivated users stay in the people data. One the client has
    // never heard of gets the fallback, never an empty name.
    return people.maybe_get_user_by_id(user_id, true)?.full_name ?? fallback;
}

export function history_text(entry: TaskHistoryEntry): string {
    const actor = name_of(entry.acting_user_id, $t({defaultMessage: "Someone"}));

    switch (entry.kind) {
        case "created":
            return $t({defaultMessage: "{actor} created the card"}, {actor});
        case "moved":
            return $t(
                {defaultMessage: "{actor} moved the card to {column}"},
                {actor, column: history_string(entry, "to_column_name")},
            );
        case "assigned": {
            const assignee_id = entry.extra_data["assignee_id"];
            if (typeof assignee_id !== "number") {
                return $t({defaultMessage: "{actor} removed the responsible member"}, {actor});
            }
            return $t(
                {defaultMessage: "{actor} made {name} responsible"},
                {actor, name: name_of(assignee_id, $t({defaultMessage: "a former member"}))},
            );
        }
        default:
            return $t({defaultMessage: "{actor} edited the card"}, {actor});
    }
}
