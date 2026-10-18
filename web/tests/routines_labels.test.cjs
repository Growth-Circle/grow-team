"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const {initialize_user_settings} = zrequire("user_settings");

const user_settings = {};
initialize_user_settings({user_settings});

const labels = zrequire("routines_labels");

// The test harness's $t() stub returns "translated: " plus the English
// source text, with the values filled in.
function t(text) {
    return `translated: ${text}`;
}

const fields = {
    pattern_kind: "daily",
    interval: null,
    time_of_day: "09:00",
    weekdays: [],
    month_day: null,
    timezone: "Asia/Jakarta",
};

run_test("zone_label", () => {
    assert.equal(labels.zone_label("Asia/Jakarta"), "WIB");
    assert.equal(labels.zone_label("Asia/Makassar"), "WITA");
    assert.equal(labels.zone_label("Asia/Jayapura"), "WIT");
    assert.equal(labels.zone_label("America/New_York"), "America/New_York");
});

run_test("format_clock is 24-hour in the language of the person", ({override}) => {
    override(user_settings, "default_language", "id");
    assert.equal(labels.format_clock("09:05"), "09.05");
    assert.equal(labels.format_clock("17:30"), "17.30");
    override(user_settings, "default_language", "en");
    assert.equal(labels.format_clock("09:05"), "09:05");
});

run_test("weekday_name", ({override}) => {
    override(user_settings, "default_language", "en");
    assert.equal(labels.weekday_name(0), "Monday");
    assert.equal(labels.weekday_name(4), "Friday");
    assert.equal(labels.weekday_name(6), "Sunday");
    override(user_settings, "default_language", "id");
    assert.equal(labels.weekday_name(0), "Senin");
    assert.equal(labels.weekday_name(4), "Jumat");
});

run_test("pattern_text", ({override}) => {
    override(user_settings, "default_language", "id");
    assert.equal(labels.pattern_text(fields), t("Every day at 09.00 WIB"));
    assert.equal(
        labels.pattern_text({...fields, pattern_kind: "weekdays", time_of_day: "17:30"}),
        t("Every weekday at 17.30 WIB"),
    );
    assert.equal(
        labels.pattern_text({...fields, pattern_kind: "weekly", weekdays: [4, 0]}),
        t("Every Senin dan Jumat at 09.00 WIB"),
    );
    assert.equal(
        labels.pattern_text({...fields, pattern_kind: "monthly", month_day: 31}),
        t("On day 31 of every month at 09.00 WIB"),
    );
    assert.equal(
        labels.pattern_text({...fields, pattern_kind: "every_minutes", interval: 15}),
        t("Every 15 minutes"),
    );
    assert.equal(
        labels.pattern_text({...fields, pattern_kind: "every_hours", interval: 4}),
        t("Every 4 hours"),
    );
    assert.equal(
        labels.pattern_text({...fields, pattern_kind: "every_hours", interval: 1}),
        t("Every hour"),
    );
    assert.equal(
        labels.pattern_text({...fields, timezone: "America/New_York"}),
        t("Every day at 09.00 America/New_York"),
    );
});

run_test("interval_chip_text", () => {
    assert.equal(
        labels.interval_chip_text({pattern_kind: "every_minutes", interval: 30}),
        t("every 30 min"),
    );
    assert.equal(
        labels.interval_chip_text({pattern_kind: "every_hours", interval: 2}),
        t("every 2 h"),
    );
});

run_test("status labels and tones", () => {
    assert.equal(labels.status_label("running"), t("Running"));
    assert.equal(labels.status_label("succeeded"), t("Done"));
    assert.equal(labels.status_label("failed"), t("Failed"));
    assert.equal(labels.status_label("skipped_missed"), t("Missed"));
    assert.equal(labels.status_label(""), t("Has not run yet"));
    assert.equal(labels.status_tone("succeeded"), "good");
    assert.equal(labels.status_tone("failed"), "bad");
    assert.equal(labels.status_tone("skipped_budget"), "bad");
    assert.equal(labels.status_tone("running"), "busy");
    assert.equal(labels.status_tone("skipped_missed"), "quiet");
    assert.equal(labels.status_tone(""), "quiet");
});

run_test("reason_text", () => {
    assert.equal(labels.reason_text(""), "");
    assert.equal(labels.reason_text("job_failed"), t("The agent's job failed."));
    assert.equal(
        labels.reason_text("agent_unavailable"),
        t("The agent was archived, paused or not ready."),
    );
    assert.equal(labels.reason_text("never_heard_of_it"), t("Something went wrong."));
});

run_test("paused_text", () => {
    assert.equal(labels.paused_text("user"), t("Paused"));
    assert.equal(labels.paused_text("failures"), t("Paused after 3 failed runs"));
    assert.equal(labels.paused_text("budget"), t("Paused: the budget is used up"));
});

run_test("an unknown cost is never zero", ({override}) => {
    override(user_settings, "default_language", "en");
    assert.equal(labels.cost_text(null), t("Unknown"));
    assert.notEqual(labels.cost_text(null), "0");
    assert.equal(labels.cost_text(2_500_000), "2.5");
});

run_test("duration_text", () => {
    assert.equal(labels.duration_text(undefined), "–");
    assert.equal(labels.duration_text(42), t("42 sec"));
    assert.equal(labels.duration_text(180), t("3 min"));
});

run_test("preset_label", () => {
    assert.equal(labels.preset_label("fast"), t("Fast"));
    assert.equal(labels.preset_label("balanced"), t("Balanced"));
    assert.equal(labels.preset_label("best"), t("Best"));
    assert.equal(labels.preset_label(""), t("Model of the agent"));
});

run_test("trigger_label", () => {
    assert.equal(labels.trigger_label("manual"), t("Run now"));
    assert.equal(labels.trigger_label("schedule"), t("On schedule"));
});
