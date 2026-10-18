import {differenceInCalendarDays} from "date-fns";

import * as timerender from "./timerender.ts";
import {user_settings} from "./user_settings.ts";

// The one place where fork-owned UI turns a number, an amount of money,
// a date or a time into text. Everything goes through Intl with the
// language and time zone of the person looking at the page, so a number
// reads "6,9 rb" in Indonesian and "6.9K" in English, and a date reads
// "14 Okt" and "Oct 14". Dates and times reuse timerender, which already
// follows the 24-hour setting.

// What a value that is not a finite number looks like. It must not
// look like zero.
const MISSING_VALUE = "–";

const MAX_RELATIVE_DAYS = 30;

const number_format_cache = new Map<string, Intl.NumberFormat>();

function number_formatter(options: Intl.NumberFormatOptions): Intl.NumberFormat {
    const key = `${user_settings.default_language}:${JSON.stringify(options)}`;
    let formatter = number_format_cache.get(key);
    if (formatter === undefined) {
        formatter = new Intl.NumberFormat(user_settings.default_language, options);
        number_format_cache.set(key, formatter);
    }
    return formatter;
}

export function format_number(value: number): string {
    return Number.isFinite(value) ? number_formatter({}).format(value) : MISSING_VALUE;
}

export function format_compact(value: number): string {
    return Number.isFinite(value)
        ? number_formatter({
              notation: "compact",
              compactDisplay: "short",
              maximumFractionDigits: 1,
          }).format(value)
        : MISSING_VALUE;
}

export function format_currency(value: number, currency = "IDR"): string {
    return Number.isFinite(value)
        ? number_formatter({style: "currency", currency}).format(value)
        : MISSING_VALUE;
}

export function format_date_short(date: Date, {include_year = false} = {}): string {
    return timerender.get_localized_date_or_time_for_format(
        date,
        include_year ? "dayofyear_year" : "dayofyear",
    );
}

export function format_time(date: Date): string {
    return timerender.get_localized_date_or_time_for_format(date, "time");
}

export function format_date_time(date: Date): string {
    return timerender.get_localized_date_or_time_for_format(date, "dayofyear_time");
}

function capitalize(text: string): string {
    return text.charAt(0).toLocaleUpperCase(user_settings.default_language) + text.slice(1);
}

// "5 minutes ago", "yesterday", "in 2 days", and a plain date once a
// moment is more than a month away. `now` is a parameter for tests.
export function format_relative(date: Date, now: Date = new Date()): string {
    const elapsed_ms = date.getTime() - now.getTime();
    if (!Number.isFinite(elapsed_ms)) {
        return MISSING_VALUE;
    }
    const relative = new Intl.RelativeTimeFormat(user_settings.default_language, {
        numeric: "auto",
    });
    const minutes = Math.trunc(elapsed_ms / 60_000);
    if (minutes === 0) {
        return capitalize(relative.format(0, "second"));
    }
    if (Math.abs(minutes) < 60) {
        return capitalize(relative.format(minutes, "minute"));
    }
    const hours = Math.trunc(minutes / 60);
    if (Math.abs(hours) < 24) {
        return capitalize(relative.format(hours, "hour"));
    }
    const days = differenceInCalendarDays(date, now, {in: timerender.display_tz});
    if (Math.abs(days) <= MAX_RELATIVE_DAYS) {
        return capitalize(relative.format(days, "day"));
    }
    return format_date_short(date, {include_year: date.getFullYear() !== now.getFullYear()});
}
