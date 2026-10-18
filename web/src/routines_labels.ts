import {$t} from "./i18n.ts";
import type {PatternKind} from "./routines_data.ts";
import * as sanji_format from "./sanji_format.ts";
import {user_settings} from "./user_settings.ts";

// The words of the Scheduled page: how a pattern, a status and a reason
// read. They are in one place so that the list, the form and the history
// say the same thing in the same way.

export type PatternFields = {
    pattern_kind: PatternKind;
    interval: number | null;
    time_of_day: string | null;
    weekdays: number[];
    month_day: number | null;
    timezone: string;
};

const ZONE_NAMES = new Map([
    ["Asia/Jakarta", "WIB"],
    ["Asia/Makassar", "WITA"],
    ["Asia/Jayapura", "WIT"],
]);

export function zone_label(timezone: string): string {
    return ZONE_NAMES.get(timezone) ?? timezone;
}

// "09.00" in Indonesian, "09:00" elsewhere: the 24-hour clock either way.
export function format_clock(time_of_day: string): string {
    return user_settings.default_language.startsWith("id")
        ? time_of_day.replace(":", ".")
        : time_of_day;
}

export function weekday_name(weekday: number): string {
    // 1 Jan 2024 was a Monday, and Monday is 0.
    return new Intl.DateTimeFormat(user_settings.default_language, {
        weekday: "long",
        timeZone: "UTC",
    }).format(new Date(Date.UTC(2024, 0, 1 + weekday)));
}

function weekday_list(weekdays: number[]): string {
    return new Intl.ListFormat(user_settings.default_language, {
        style: "long",
        type: "conjunction",
    }).format(weekdays.toSorted((a, b) => a - b).map((weekday) => weekday_name(weekday)));
}

export function pattern_text(fields: PatternFields): string {
    const count = sanji_format.format_number(fields.interval ?? 0);
    if (fields.pattern_kind === "every_minutes") {
        return $t({defaultMessage: "Every {count} minutes"}, {count});
    }
    if (fields.pattern_kind === "every_hours") {
        return fields.interval === 1
            ? $t({defaultMessage: "Every hour"})
            : $t({defaultMessage: "Every {count} hours"}, {count});
    }
    const time = `${format_clock(fields.time_of_day ?? "00:00")} ${zone_label(fields.timezone)}`;
    switch (fields.pattern_kind) {
        case "daily":
            return $t({defaultMessage: "Every day at {time}"}, {time});
        case "weekdays":
            return $t({defaultMessage: "Every weekday at {time}"}, {time});
        case "weekly":
            return $t(
                {defaultMessage: "Every {days} at {time}"},
                {days: weekday_list(fields.weekdays), time},
            );
        case "monthly":
            return $t(
                {defaultMessage: "On day {day} of every month at {time}"},
                {day: sanji_format.format_number(fields.month_day ?? 1), time},
            );
    }
}

// How a routine that repeats within a day reads in a small place, such
// as a day of the week.
export function interval_chip_text(fields: {
    pattern_kind: PatternKind;
    interval: number | null;
}): string {
    const count = sanji_format.format_number(fields.interval ?? 0);
    return fields.pattern_kind === "every_hours"
        ? $t({defaultMessage: "every {count} h"}, {count})
        : $t({defaultMessage: "every {count} min"}, {count});
}

export function pattern_kind_label(kind: PatternKind): string {
    switch (kind) {
        case "daily":
            return $t({defaultMessage: "Every day"});
        case "weekdays":
            return $t({defaultMessage: "Every weekday"});
        case "weekly":
            return $t({defaultMessage: "Every week"});
        case "monthly":
            return $t({defaultMessage: "Every month"});
        case "every_hours":
            return $t({defaultMessage: "Every few hours"});
        case "every_minutes":
            return $t({defaultMessage: "Every few minutes"});
    }
}

export function status_label(status: string): string {
    switch (status) {
        case "running":
            return $t({defaultMessage: "Running"});
        case "succeeded":
            return $t({defaultMessage: "Done"});
        case "failed":
            return $t({defaultMessage: "Failed"});
        case "skipped_budget":
            return $t({defaultMessage: "Skipped: budget"});
        case "skipped_unavailable":
            return $t({defaultMessage: "Skipped: not available"});
        case "skipped_missed":
            return $t({defaultMessage: "Missed"});
        default:
            return $t({defaultMessage: "Has not run yet"});
    }
}

// The tone of a status in the page: how it is drawn, not what it says.
export type StatusTone = "good" | "bad" | "busy" | "quiet";

export function status_tone(status: string): StatusTone {
    switch (status) {
        case "succeeded":
            return "good";
        case "failed":
        case "skipped_budget":
        case "skipped_unavailable":
            return "bad";
        case "running":
            return "busy";
        default:
            return "quiet";
    }
}

export function reason_text(error_code: string): string {
    switch (error_code) {
        case "":
            return "";
        case "agent_unavailable":
            return $t({defaultMessage: "The agent was archived, paused or not ready."});
        case "agent_access":
            return $t({defaultMessage: "The owner could not use the agent."});
        case "channel_unavailable":
            return $t({defaultMessage: "The owner could not read the channel."});
        case "owner_unavailable":
            return $t({defaultMessage: "The owner is no longer an active member."});
        case "budget_exceeded":
            return $t({defaultMessage: "The budget for this month was used up."});
        case "job_failed":
            return $t({defaultMessage: "The agent's job failed."});
        case "job_cancelled":
            return $t({defaultMessage: "The agent's job was cancelled."});
        case "job_interrupted":
            return $t({defaultMessage: "The agent's job was interrupted."});
        case "job_blocked":
            return $t({defaultMessage: "The agent's job could not start."});
        case "job_missing":
            return $t({defaultMessage: "The agent's job is gone."});
        case "job_not_created":
            return $t({defaultMessage: "The agent's job could not be started."});
        default:
            return $t({defaultMessage: "Something went wrong."});
    }
}

export function paused_text(reason: string): string {
    switch (reason) {
        case "failures":
            return $t({defaultMessage: "Paused after 3 failed runs"});
        case "budget":
            return $t({defaultMessage: "Paused: the budget is used up"});
        case "unavailable":
            return $t({defaultMessage: "Paused: the agent or channel is not available"});
        default:
            return $t({defaultMessage: "Paused"});
    }
}

export function trigger_label(trigger: "schedule" | "manual"): string {
    return trigger === "manual"
        ? $t({defaultMessage: "Run now"})
        : $t({defaultMessage: "On schedule"});
}

export function preset_label(preset: string): string {
    switch (preset) {
        case "fast":
            return $t({defaultMessage: "Fast"});
        case "balanced":
            return $t({defaultMessage: "Balanced"});
        case "best":
            return $t({defaultMessage: "Best"});
        default:
            return $t({defaultMessage: "Model of the agent"});
    }
}

// Nobody measures the cost of a run yet, and an unknown cost must never
// look like zero.
export function cost_text(cost_microunits: number | null): string {
    return cost_microunits === null
        ? $t({defaultMessage: "Unknown"})
        : sanji_format.format_number(cost_microunits / 1_000_000);
}

export function duration_text(seconds: number | undefined): string {
    if (seconds === undefined) {
        return "–";
    }
    return seconds < 60
        ? $t({defaultMessage: "{count} sec"}, {count: sanji_format.format_number(seconds)})
        : $t(
              {defaultMessage: "{count} min"},
              {count: sanji_format.format_number(Math.round(seconds / 60))},
          );
}
