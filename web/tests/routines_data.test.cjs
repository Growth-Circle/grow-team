"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let next_response;
let last_call;
mock_esm("../src/channel", {
    get(options) {
        last_call = {method: "GET", ...options};
        return Promise.resolve(next_response);
    },
    post(options) {
        last_call = {method: "POST", ...options};
        return Promise.resolve(next_response);
    },
    patch(options) {
        last_call = {method: "PATCH", ...options};
        return Promise.resolve(next_response);
    },
    del(options) {
        last_call = {method: "DELETE", ...options};
        return Promise.resolve(next_response);
    },
});

const routines_data = zrequire("routines_data");

const ID = "6f1c8a52-3a52-4d56-9f0a-0d7a8c1d2e34";
const OTHER_ID = "0b7f8a9e-8c53-4d63-a5c9-2f1a1c5e9a10";

function make_run(overrides) {
    return {
        id: "a9d1e6c7-4b3c-4c07-8c7a-5b0d2f4a7e11",
        routine_id: ID,
        trigger: "schedule",
        scheduled_for: 1_760_491_200,
        started_at: 1_760_491_201,
        finished_at: 1_760_491_261,
        status: "succeeded",
        error_code: "",
        result_message_id: 4521,
        cost_microunits: null,
        ...overrides,
    };
}

function make_routine(overrides) {
    return {
        id: ID,
        title: "Brief pagi",
        instructions: "Rangkum yang perlu diperhatikan hari ini.",
        agent_profile_id: OTHER_ID,
        agent_name: "Sanji Agent",
        owner_id: 10,
        stream_id: 12,
        topic: "Brief",
        pattern_kind: "daily",
        interval: null,
        time_of_day: "08:00",
        weekdays: [],
        month_day: null,
        timezone: "Asia/Jakarta",
        model_preset: "",
        enabled: true,
        paused_reason: "",
        next_run_at: 1_760_577_600,
        last_run_at: 1_760_491_200,
        last_status: "succeeded",
        executor: "agent",
        can_edit: true,
        last_run: make_run({}),
        ...overrides,
    };
}

const defaults = {agent_profile_id: OTHER_ID, stream_id: ""};

run_test("parse_route", () => {
    assert.deepEqual(routines_data.parse_route([]), {kind: "list"});
    assert.deepEqual(routines_data.parse_route(["new"]), {kind: "new", template_id: undefined});
    assert.deepEqual(routines_data.parse_route(["new", "morning_brief"]), {
        kind: "new",
        template_id: "morning_brief",
    });
    assert.deepEqual(routines_data.parse_route([ID, "edit"]), {kind: "edit", id: ID});
    assert.deepEqual(routines_data.parse_route([ID, "history"]), {kind: "history", id: ID});
    // An address that names nothing real opens the list.
    assert.deepEqual(routines_data.parse_route([ID]), {kind: "list"});
    assert.deepEqual(routines_data.parse_route(["12", "edit"]), {kind: "list"});
    assert.deepEqual(routines_data.parse_route(["nonsense"]), {kind: "list"});
});

run_test("route_hash", () => {
    assert.equal(routines_data.route_hash({kind: "list"}), "#routines");
    assert.equal(routines_data.route_hash({kind: "new", template_id: undefined}), "#routines/new");
    assert.equal(
        routines_data.route_hash({kind: "new", template_id: "client_check"}),
        "#routines/new/client_check",
    );
    assert.equal(routines_data.route_hash({kind: "edit", id: ID}), `#routines/${ID}/edit`);
    assert.equal(routines_data.route_hash({kind: "history", id: ID}), `#routines/${ID}/history`);
    for (const hash of [
        "#routines",
        "#routines/new/client_check",
        `#routines/${ID}/edit`,
        `#routines/${ID}/history`,
    ]) {
        const route = routines_data.parse_route(hash.split("/").slice(1));
        assert.equal(routines_data.route_hash(route), hash);
    }
});

run_test("drafts", () => {
    const empty = routines_data.empty_draft(defaults);
    assert.equal(empty.agent_profile_id, OTHER_ID);
    assert.equal(empty.pattern_kind, "daily");
    assert.deepEqual([empty.hour, empty.minute], ["08", "00"]);

    const from_template = routines_data.draft_from_template(
        {
            id: "weekly_team_digest",
            title: "Rangkuman mingguan tim",
            instructions: "Rangkum minggu ini.",
            pattern_kind: "weekly",
            time_of_day: "16:00",
            weekdays: [4],
        },
        defaults,
    );
    assert.equal(from_template.title, "Rangkuman mingguan tim");
    assert.deepEqual([from_template.hour, from_template.minute], ["16", "00"]);
    assert.deepEqual(from_template.weekdays, [4]);
    // A template does not choose the channel or the topic.
    assert.equal(from_template.stream_id, "");
    assert.equal(from_template.topic, "");

    const from_routine = routines_data.draft_from_routine(
        make_routine({pattern_kind: "every_minutes", interval: 30, time_of_day: null}),
        defaults,
    );
    assert.equal(from_routine.interval, "30");
    assert.equal(from_routine.stream_id, "12");
    assert.equal(from_routine.topic, "Brief");
    assert.equal(from_routine.agent_profile_id, OTHER_ID);

    assert.equal(routines_data.is_dirty(empty, empty), false);
    assert.equal(routines_data.is_dirty(empty, {...empty, title: "x"}), true);
});

run_test("invalid_fields", () => {
    const ok = {
        ...routines_data.empty_draft(defaults),
        title: "Brief pagi",
        instructions: "Rangkum.",
        stream_id: "12",
        topic: "Brief",
    };
    assert.deepEqual(routines_data.invalid_fields(ok), []);
    assert.deepEqual(
        routines_data.invalid_fields(
            routines_data.empty_draft({agent_profile_id: "", stream_id: ""}),
        ),
        ["title", "instructions", "agent", "stream", "topic"],
    );
    assert.deepEqual(routines_data.invalid_fields({...ok, title: "  ", topic: "x".repeat(61)}), [
        "title",
        "topic",
    ]);

    // The bounds of an interval are the ones the server keeps.
    const every = {...ok, pattern_kind: "every_minutes"};
    for (const [interval, valid] of [
        ["14", false],
        ["15", true],
        ["59", true],
        ["60", false],
        ["", false],
        ["1.5", false],
        ["abc", false],
    ]) {
        assert.equal(
            routines_data.invalid_fields({...every, interval}).includes("interval"),
            !valid,
            interval,
        );
    }
    const hourly = {...ok, pattern_kind: "every_hours"};
    assert.equal(routines_data.invalid_fields({...hourly, interval: "1"}).length, 0);
    assert.equal(routines_data.invalid_fields({...hourly, interval: "23"}).length, 0);
    assert.deepEqual(routines_data.invalid_fields({...hourly, interval: "24"}), ["interval"]);
    // An interval that the pattern does not use is not checked.
    assert.deepEqual(routines_data.invalid_fields({...ok, interval: "1"}), []);

    assert.deepEqual(routines_data.invalid_fields({...ok, pattern_kind: "weekly", weekdays: []}), [
        "weekdays",
    ]);
});

run_test("draft_to_params", () => {
    const base = {
        ...routines_data.empty_draft(defaults),
        title: "  Brief pagi ",
        instructions: " Rangkum. ",
        stream_id: "12",
        topic: " Brief ",
        hour: "08",
        minute: "30",
    };
    assert.deepEqual(routines_data.draft_to_params(base), {
        title: "Brief pagi",
        instructions: "Rangkum.",
        agent_profile_id: OTHER_ID,
        stream_id: "12",
        topic: "Brief",
        pattern_kind: "daily",
        model_preset: "",
        time_of_day: "08:30",
    });
    assert.deepEqual(
        routines_data.draft_to_params({...base, pattern_kind: "weekly", weekdays: [4, 0]}),
        {
            ...routines_data.draft_to_params(base),
            pattern_kind: "weekly",
            weekdays: "[0,4]",
        },
    );
    assert.equal(
        routines_data.draft_to_params({...base, pattern_kind: "monthly", month_day: "31"})[
            "month_day"
        ],
        "31",
    );
    const interval = routines_data.draft_to_params({
        ...base,
        pattern_kind: "every_minutes",
        interval: " 15 ",
        weekdays: [1],
    });
    assert.equal(interval["interval"], "15");
    // Only the fields of the pattern travel.
    assert.equal("time_of_day" in interval, false);
    assert.equal("weekdays" in interval, false);
});

run_test("sort_routines", () => {
    const soon = make_routine({id: "a", title: "Soon", next_run_at: 100});
    const later = make_routine({id: "b", title: "Later", next_run_at: 200});
    const paused = make_routine({id: "c", title: "Paused", enabled: false, next_run_at: 50});
    const outside = make_routine({id: "d", title: "Outside", next_run_at: null});
    assert.deepEqual(
        routines_data.sort_routines([paused, outside, later, soon]).map((routine) => routine.id),
        ["a", "b", "c", "d"],
    );
});

run_test("replace_routine", () => {
    const first = make_routine({id: "a"});
    const second = make_routine({id: "b"});
    const changed = make_routine({id: "b", title: "Changed"});
    assert.deepEqual(routines_data.replace_routine([first, second], changed), [first, changed]);
    assert.deepEqual(routines_data.replace_routine([first], changed), [first, changed]);
});

run_test("run helpers", () => {
    assert.equal(routines_data.run_seconds(make_run({})), 60);
    assert.equal(routines_data.run_seconds(make_run({finished_at: null})), undefined);
    assert.equal(routines_data.is_running("running"), true);
    assert.equal(routines_data.is_running("failed"), false);
    assert.equal(routines_data.was_skipped("skipped_budget"), true);
    assert.equal(routines_data.was_skipped("failed"), false);
});

run_test("runs_on", () => {
    // 16 Oct 2026 is a Friday.
    const weekdays = make_routine({pattern_kind: "weekdays"});
    assert.equal(routines_data.runs_on(weekdays, 2026, 9, 16), true);
    assert.equal(routines_data.runs_on(weekdays, 2026, 9, 17), false);
    assert.equal(routines_data.runs_on(weekdays, 2026, 9, 18), false);
    assert.equal(routines_data.runs_on(weekdays, 2026, 9, 19), true);

    const weekly = make_routine({pattern_kind: "weekly", weekdays: [4, 0]});
    assert.equal(routines_data.runs_on(weekly, 2026, 9, 16), true);
    assert.equal(routines_data.runs_on(weekly, 2026, 9, 19), true);
    assert.equal(routines_data.runs_on(weekly, 2026, 9, 20), false);

    // A month without the day runs on its last day.
    const monthly = make_routine({pattern_kind: "monthly", month_day: 31});
    assert.equal(routines_data.runs_on(monthly, 2026, 9, 31), true);
    assert.equal(routines_data.runs_on(monthly, 2026, 9, 30), false);
    assert.equal(routines_data.runs_on(monthly, 2026, 10, 30), true);
    assert.equal(routines_data.runs_on(monthly, 2026, 10, 29), false);
    assert.equal(routines_data.runs_on(monthly, 2028, 1, 29), true);

    assert.equal(
        routines_data.runs_on(
            make_routine({pattern_kind: "every_hours", interval: 2}),
            2026,
            9,
            17,
        ),
        true,
    );
});

run_test("week_days", () => {
    const today = new Date(2026, 9, 16);
    const brief = make_routine({id: "a", title: "Brief pagi", time_of_day: "08:00"});
    const wrap = make_routine({
        id: "b",
        title: "Ringkasan",
        pattern_kind: "weekdays",
        time_of_day: "17:30",
    });
    const busy = make_routine({
        id: "c",
        title: "Cek",
        pattern_kind: "every_minutes",
        interval: 30,
        time_of_day: null,
    });
    const paused = make_routine({id: "d", title: "Dijeda", enabled: false});

    const days = routines_data.week_days([wrap, paused, busy, brief], today);

    assert.equal(days.length, 7);
    assert.deepEqual(
        days.map((day) => day.date.getDate()),
        [16, 17, 18, 19, 20, 21, 22],
    );
    // Friday: a routine without a time sorts first, then by time.
    assert.deepEqual(
        days[0].items.map((item) => [item.routine.id, item.time]),
        [
            ["c", undefined],
            ["a", "08:00"],
            ["b", "17:30"],
        ],
    );
    // Saturday and Sunday: no weekday routine, and a paused one never shows.
    assert.deepEqual(
        days[1].items.map((item) => item.routine.id),
        ["c", "a"],
    );
    assert.deepEqual(
        days[2].items.map((item) => item.routine.id),
        ["c", "a"],
    );
    assert.equal(
        days.some((day) => day.items.some((item) => item.routine.id === "d")),
        false,
    );
    // Monday has the weekday routine again.
    assert.equal(
        days[3].items.some((item) => item.routine.id === "b"),
        true,
    );
});

run_test("requests", async () => {
    next_response = {routines: [make_routine({})]};
    const routines = await routines_data.fetch_routines();
    assert.equal(last_call.url, "/json/routines");
    assert.equal(routines[0].title, "Brief pagi");

    next_response = {routine: make_routine({})};
    await routines_data.fetch_routine(ID);
    assert.equal(last_call.url, `/json/routines/${ID}`);

    next_response = {runs: [make_run({})]};
    const runs = await routines_data.fetch_runs(ID);
    assert.equal(last_call.url, `/json/routines/${ID}/runs`);
    assert.equal(runs[0].cost_microunits, null);

    next_response = {
        templates: [
            {
                id: "morning_brief",
                title: "Brief pagi",
                instructions: "Rangkum.",
                pattern_kind: "daily",
                time_of_day: "08:00",
                weekdays: [],
            },
        ],
    };
    assert.equal((await routines_data.fetch_templates())[0].id, "morning_brief");
    assert.equal(last_call.url, "/json/routines/templates");

    next_response = {
        agents: [{id: OTHER_ID, name: "Sanji Agent"}],
        default_agent_id: OTHER_ID,
        default_timezone: "Asia/Jakarta",
    };
    assert.equal((await routines_data.fetch_agents()).default_timezone, "Asia/Jakarta");

    next_response = {routine: make_routine({})};
    await routines_data.create_routine({title: "x"});
    assert.deepEqual(last_call, {method: "POST", url: "/json/routines", data: {title: "x"}});
    await routines_data.save_routine(ID, {title: "y"});
    assert.deepEqual(last_call, {
        method: "PATCH",
        url: `/json/routines/${ID}`,
        data: {title: "y"},
    });
    await routines_data.set_enabled(ID, false);
    assert.equal(last_call.url, `/json/routines/${ID}/pause`);
    await routines_data.set_enabled(ID, true);
    assert.equal(last_call.url, `/json/routines/${ID}/resume`);

    next_response = {run: make_run({trigger: "manual", status: "running"})};
    assert.equal((await routines_data.run_now(ID)).trigger, "manual");
    assert.equal(last_call.url, `/json/routines/${ID}/run`);

    next_response = {};
    await routines_data.delete_routine(ID);
    assert.deepEqual(last_call, {method: "DELETE", url: `/json/routines/${ID}`});
});
