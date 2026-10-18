"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const {initialize_user_settings} = zrequire("user_settings");

const user_settings = {};
initialize_user_settings({user_settings});

const timerender = zrequire("timerender");
const sanji_format = zrequire("sanji_format");

// 14 Oct 2026, 13:30 in Jakarta (UTC+7).
// Intl separates a number from its unit with a no-break space.
const plain = (text) => text.replaceAll(/[\u00A0\u202F]/g, " ");

const lunch = new Date("2026-10-14T06:30:00.000Z");

run_test("numbers follow the user's language", ({override}) => {
    override(user_settings, "default_language", "id");
    assert.equal(sanji_format.format_number(1234567.5), "1.234.567,5");
    override(user_settings, "default_language", "en");
    assert.equal(sanji_format.format_number(1234567.5), "1,234,567.5");
});

run_test("compact numbers", ({override}) => {
    override(user_settings, "default_language", "id");
    assert.equal(plain(sanji_format.format_compact(6900)), "6,9 rb");
    assert.equal(plain(sanji_format.format_compact(999)), "999");
    assert.equal(plain(sanji_format.format_compact(2_500_000)), "2,5 jt");
    override(user_settings, "default_language", "en");
    assert.equal(plain(sanji_format.format_compact(6900)), "6.9K");
    assert.equal(plain(sanji_format.format_compact(2_500_000)), "2.5M");
});

run_test("a value that is not a finite number is never shown as zero", ({override}) => {
    override(user_settings, "default_language", "id");
    for (const value of [Number.NaN, Infinity, -Infinity]) {
        assert.equal(sanji_format.format_number(value), "–");
        assert.equal(plain(sanji_format.format_compact(value)), "–");
        assert.equal(plain(sanji_format.format_currency(value)), "–");
    }
    assert.equal(sanji_format.format_number(0), "0");
    assert.equal(sanji_format.format_number(-1500), "-1.500");
});

run_test("currency defaults to rupiah and takes another code", ({override}) => {
    override(user_settings, "default_language", "id");
    assert.equal(plain(sanji_format.format_currency(1_500_000)), "Rp 1.500.000");
    assert.equal(plain(sanji_format.format_currency(0.5, "USD")), "US$0,50");
    override(user_settings, "default_language", "en");
    assert.equal(plain(sanji_format.format_currency(0.5, "USD")), "$0.50");
});

run_test("dates and times use the display time zone", ({override}) => {
    timerender.set_display_time_zone("Asia/Jakarta");
    override(user_settings, "default_language", "id");
    override(user_settings, "twenty_four_hour_time", true);
    assert.equal(sanji_format.format_date_short(lunch), "14 Okt");
    assert.equal(sanji_format.format_date_short(lunch, {include_year: true}), "14 Okt 2026");
    assert.equal(plain(sanji_format.format_time(lunch)), "13.30");
    assert.equal(sanji_format.format_date_time(lunch), "14 Okt, 13.30");

    override(user_settings, "default_language", "en");
    assert.equal(sanji_format.format_date_short(lunch), "Oct 14");

    // Just after midnight in Jakarta is still the day before in UTC.
    const after_midnight = new Date("2026-10-14T17:30:00.000Z");
    override(user_settings, "default_language", "id");
    assert.equal(sanji_format.format_date_short(after_midnight), "15 Okt");
    timerender.set_display_time_zone("UTC");
    assert.equal(sanji_format.format_date_short(after_midnight), "14 Okt");
});

run_test("the 12-hour setting is respected", ({override}) => {
    timerender.set_display_time_zone("Asia/Jakarta");
    override(user_settings, "default_language", "en");
    override(user_settings, "twenty_four_hour_time", false);
    assert.equal(plain(sanji_format.format_time(lunch)), "1:30 PM");
    override(user_settings, "twenty_four_hour_time", true);
    assert.equal(plain(sanji_format.format_time(lunch)), "13:30");
    timerender.set_display_time_zone("UTC");
});

run_test("relative times", ({override}) => {
    timerender.set_display_time_zone("Asia/Jakarta");
    const now = new Date("2026-10-14T06:30:00.000Z");
    const ago = (ms) => new Date(now.getTime() - ms);
    const MINUTE = 60_000;
    const HOUR = 60 * MINUTE;
    const DAY = 24 * HOUR;

    override(user_settings, "default_language", "en");
    assert.equal(sanji_format.format_relative(ago(30_000), now), "Now");
    assert.equal(sanji_format.format_relative(ago(5 * MINUTE), now), "5 minutes ago");
    assert.equal(sanji_format.format_relative(ago(3 * HOUR), now), "3 hours ago");
    assert.equal(sanji_format.format_relative(ago(26 * HOUR), now), "Yesterday");
    assert.equal(sanji_format.format_relative(ago(5 * DAY), now), "5 days ago");
    assert.equal(sanji_format.format_relative(ago(-2 * DAY), now), "In 2 days");
    assert.equal(sanji_format.format_relative(ago(45 * DAY), now), "Aug 30");
    assert.equal(sanji_format.format_relative(ago(400 * DAY), now), "Sep 9, 2025");

    override(user_settings, "default_language", "id");
    assert.equal(sanji_format.format_relative(ago(5 * MINUTE), now), "5 menit yang lalu");
    assert.equal(sanji_format.format_relative(ago(26 * HOUR), now), "Kemarin");
    assert.equal(sanji_format.format_relative(ago(5 * DAY), now), "5 hari yang lalu");
    assert.equal(sanji_format.format_relative(ago(45 * DAY), now), "30 Agu");

    assert.equal(sanji_format.format_relative(new Date(Number.NaN), now), "–");
    timerender.set_display_time_zone("UTC");
});
