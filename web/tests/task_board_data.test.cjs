"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const task_board_data = zrequire("task_board_data");

const board = {id: 1, name: "October release", date_created: 100};

const columns = [
    {id: 10, board_id: 1, name: "Inbox", order: 0, work_limit: null, done_window_days: null},
    {id: 11, board_id: 1, name: "In progress", order: 1, work_limit: 2, done_window_days: null},
    {id: 12, board_id: 1, name: "Done", order: 2, work_limit: null, done_window_days: 7},
];

function make_task(overrides) {
    return {
        id: 1,
        display_id: "GT-1",
        board_id: 1,
        column_id: 10,
        title: "Write the regression test",
        body: "",
        position: 1000,
        stream_id: null,
        topic: "",
        origin_message_id: null,
        creator_id: 5,
        assignee_id: null,
        agent_profile_id: null,
        agent_job_id: null,
        priority: 0,
        labels: [],
        checklist: [],
        due_at: null,
        note_count: 0,
        blocked: false,
        completed_at: null,
        date_created: 100,
        last_updated: 100,
        ...overrides,
    };
}

function load(tasks, folded_task_ids = []) {
    task_board_data.set_board({board, columns, tasks, folded_task_ids});
}

run_test("columns sort by order", () => {
    task_board_data.set_board({
        board,
        columns: [columns[2], columns[0], columns[1]],
        tasks: [],
        folded_task_ids: [],
    });
    assert.deepEqual(
        task_board_data.get_columns().map((column) => column.name),
        ["Inbox", "In progress", "Done"],
    );
});

run_test("cards sort by position inside a column", () => {
    load([
        make_task({id: 1, position: 2000}),
        make_task({id: 2, position: 1000}),
        make_task({id: 3, position: 3000, column_id: 11}),
    ]);

    assert.deepEqual(
        task_board_data.tasks_in_column(10).map((task) => task.id),
        [2, 1],
    );
    assert.deepEqual(
        task_board_data.tasks_in_column(11).map((task) => task.id),
        [3],
    );
});

run_test("a drop lands between the two cards it was dropped between", () => {
    load([make_task({id: 1, position: 1000}), make_task({id: 2, position: 2000})]);
    const visible = [1, 2];

    assert.equal(task_board_data.position_for_slot(10, visible, 0, 99), 0);
    assert.equal(task_board_data.position_for_slot(10, visible, 1, 99), 1500);
    assert.equal(task_board_data.position_for_slot(10, visible, 2, 99), 3000);
    // An empty column gives the first card a position of its own.
    assert.equal(task_board_data.position_for_slot(12, [], 0, 99), 1000);
});

run_test("a drop within a column leaves out the card that moves", () => {
    load([
        make_task({id: 1, position: 1000}),
        make_task({id: 2, position: 2000}),
        make_task({id: 3, position: 3000}),
    ]);
    // Card 1 is dragged; the others on screen are 2 and 3.
    const visible = [2, 3];

    assert.equal(task_board_data.position_for_slot(10, visible, 0, 1), 1000);
    assert.equal(task_board_data.position_for_slot(10, visible, 1, 1), 2500);
    assert.equal(task_board_data.position_for_slot(10, visible, 2, 1), 4000);
});

run_test("a drop lands next to the visible card even when others are hidden", () => {
    // Card 2 is hidden by a filter, so the screen shows 1 and 3.
    load([
        make_task({id: 1, position: 1000}),
        make_task({id: 2, position: 2000}),
        make_task({id: 3, position: 3000}),
    ]);

    // Above card 3, the card goes between hidden card 2 and card 3.
    assert.equal(task_board_data.position_for_slot(10, [1, 3], 1, 99), 2500);
    // Below card 1, it goes between card 1 and hidden card 2.
    assert.equal(task_board_data.position_for_slot(10, [1], 1, 99), 1500);
    // With nothing on screen it goes after every hidden card.
    assert.equal(task_board_data.position_for_slot(10, [], 0, 99), 4000);
});

run_test("a move to the top or bottom lands past the card at that end", () => {
    load([
        make_task({id: 1, position: 1000}),
        make_task({id: 2, position: 2000}),
        make_task({id: 3, column_id: 11, position: 500}),
    ]);

    assert.equal(task_board_data.position_for_edge(11, "top", 1), 500 - 1000);
    assert.equal(task_board_data.position_for_edge(11, "bottom", 1), 500 + 1000);
    // An empty column gives the card a position of its own.
    assert.equal(task_board_data.position_for_edge(12, "top", 1), 1000);
    assert.equal(task_board_data.position_for_edge(12, "bottom", 1), 1000);
});

run_test("a move within a column ignores the card that moves", () => {
    load([
        make_task({id: 1, position: 1000}),
        make_task({id: 2, position: 2000}),
        make_task({id: 3, position: 3000}),
    ]);

    assert.equal(task_board_data.position_for_edge(10, "top", 3), 0);
    assert.equal(task_board_data.position_for_edge(10, "bottom", 1), 4000);
    // The top card sent to the top keeps the position past the next one.
    assert.equal(task_board_data.position_for_edge(10, "top", 1), 1000);
});

run_test("saving the spot a card already has is a no-op", () => {
    load([
        make_task({id: 1, position: 1000}),
        make_task({id: 2, position: 2000}),
        make_task({id: 3, position: 3000}),
    ]);
    const get = (id) => task_board_data.get_task(id);

    assert.ok(task_board_data.move_to_edge_is_noop(get(1), 10, "top"));
    assert.ok(!task_board_data.move_to_edge_is_noop(get(1), 10, "bottom"));
    assert.ok(task_board_data.move_to_edge_is_noop(get(3), 10, "bottom"));
    assert.ok(!task_board_data.move_to_edge_is_noop(get(2), 10, "top"));
    assert.ok(!task_board_data.move_to_edge_is_noop(get(2), 10, "bottom"));
    // Another column is always a change.
    assert.ok(!task_board_data.move_to_edge_is_noop(get(1), 11, "top"));
});

run_test("a column reports when it reaches its work limit", () => {
    load([make_task({id: 1, column_id: 11}), make_task({id: 2, column_id: 11})]);
    assert.ok(task_board_data.column_is_over_work_limit(11));

    load([make_task({id: 1, column_id: 11})]);
    assert.ok(!task_board_data.column_is_over_work_limit(11));

    // A column without a limit never warns.
    load([make_task({id: 1}), make_task({id: 2}), make_task({id: 3})]);
    assert.ok(!task_board_data.column_is_over_work_limit(10));
});

run_test("a folded card is hidden until the column is unfolded", () => {
    load([make_task({id: 1, column_id: 12}), make_task({id: 2, column_id: 12})], [2]);

    assert.deepEqual(
        task_board_data.visible_tasks_in_column(12).map((task) => task.id),
        [1],
    );
    assert.equal(task_board_data.folded_count_in_column(12), 1);

    task_board_data.unfold_all();
    assert.deepEqual(
        task_board_data.visible_tasks_in_column(12).map((task) => task.id),
        [1, 2],
    );
});

run_test("an updated card comes back into view", () => {
    load([make_task({id: 1, column_id: 12})], [1]);
    assert.ok(task_board_data.is_folded(1));

    task_board_data.add_or_update_task(make_task({id: 1, column_id: 10}));
    assert.ok(!task_board_data.is_folded(1));
    assert.equal(task_board_data.get_task(1).column_id, 10);
});

run_test("a card from another board is ignored", () => {
    load([]);
    task_board_data.add_or_update_task(make_task({id: 9, board_id: 2}));
    assert.equal(task_board_data.get_task(9), undefined);
    assert.equal(task_board_data.total_task_count(), 0);
});

run_test("removing a card drops it from the board", () => {
    load([make_task({id: 1}), make_task({id: 2})]);
    task_board_data.remove_task(1);

    assert.equal(task_board_data.get_task(1), undefined);
    assert.equal(task_board_data.total_task_count(), 1);
});

run_test("the summary lists each origin channel once", () => {
    load([
        make_task({id: 1, stream_id: 5}),
        make_task({id: 2, stream_id: 5}),
        make_task({id: 3, stream_id: 7}),
        make_task({id: 4}),
    ]);

    assert.deepEqual(task_board_data.origin_stream_ids().toSorted(), [5, 7]);
});

run_test("renaming the board keeps the other board untouched", () => {
    load([]);
    task_board_data.set_board_name({id: 2, name: "Another board", date_created: 100});
    assert.equal(task_board_data.get_board().name, "October release");

    task_board_data.set_board_name({id: 1, name: "November release", date_created: 100});
    assert.equal(task_board_data.get_board().name, "November release");
});

run_test("clearing drops everything", () => {
    load([make_task({id: 1})]);
    task_board_data.clear();

    assert.equal(task_board_data.get_board(), undefined);
    assert.deepEqual(task_board_data.get_columns(), []);
    assert.equal(task_board_data.total_task_count(), 0);
});

run_test("the board hash names a filter, one card, or the personal list", () => {
    assert.deepEqual(task_board_data.parse_hash_args([]), {
        filter: "all",
        task_id: undefined,
        view: "board",
    });
    assert.deepEqual(task_board_data.parse_hash_args(["mine"]), {
        filter: "mine",
        task_id: undefined,
        view: "board",
    });
    assert.deepEqual(task_board_data.parse_hash_args(["review"]), {
        filter: "review",
        task_id: undefined,
        view: "board",
    });
    assert.deepEqual(task_board_data.parse_hash_args(["mine", "list"]), {
        filter: "mine",
        task_id: undefined,
        view: "list",
    });
    // The list belongs to My tasks; other filters stay on the board.
    assert.equal(task_board_data.parse_hash_args(["review", "list"]).view, "board");
    assert.deepEqual(task_board_data.parse_hash_args(["42"]), {
        filter: "all",
        task_id: 42,
        view: "board",
    });
    // Anything else is not a card, so the board opens as it always did.
    assert.deepEqual(task_board_data.parse_hash_args(["42abc"]), {
        filter: "all",
        task_id: undefined,
        view: "board",
    });
    assert.deepEqual(task_board_data.parse_hash_args(["-1"]), {
        filter: "all",
        task_id: undefined,
        view: "board",
    });
});

run_test("the high priority filter keeps only high priority cards", () => {
    load([
        make_task({id: 1, priority: 3}),
        make_task({id: 2, priority: 2}),
        make_task({id: 3, priority: 0}),
        make_task({id: 4, priority: 3, column_id: 11}),
    ]);
    task_board_data.set_filter("high_priority");
    const ids = (column_id) =>
        task_board_data.visible_tasks_in_column(column_id, 5).map((task) => task.id);
    assert.deepEqual(ids(10), [1]);
    assert.deepEqual(ids(11), [4]);
    assert.equal(task_board_data.parse_filter("high_priority"), "high_priority");

    task_board_data.set_filter("all");
    assert.deepEqual(ids(10), [1, 2, 3]);
});

run_test("adding a label trims it and ignores blanks and repeats", () => {
    const labels = ["ops"];
    assert.deepEqual(task_board_data.add_label(labels, "  q4  "), {
        labels: ["ops", "q4"],
        error: undefined,
    });
    assert.equal(task_board_data.add_label(labels, "   ").labels, labels);
    assert.equal(task_board_data.add_label(labels, "ops").labels, labels);
    assert.equal(task_board_data.add_label(labels, "ops").error, undefined);
});

run_test("a label past the limits is refused with the reason", () => {
    const labels = ["ops"];
    assert.deepEqual(task_board_data.add_label(labels, "x".repeat(41)), {
        labels,
        error: "too_long",
    });
    assert.equal(task_board_data.add_label(labels, "x".repeat(40)).error, undefined);

    const full = Array.from({length: 10}, (_unused, index) => `label ${index}`);
    assert.deepEqual(task_board_data.add_label(full, "one more"), {
        labels: full,
        error: "too_many",
    });
});

run_test("removing a label keeps the others in order", () => {
    assert.deepEqual(task_board_data.remove_label(["a", "b", "c"], "b"), ["a", "c"]);
    assert.deepEqual(task_board_data.remove_label(["a"], "z"), ["a"]);
});

const JAKARTA = "Asia/Jakarta";
const LOS_ANGELES = "America/Los_Angeles";

function seconds(iso) {
    return Date.parse(iso) / 1000;
}

function sections_of(tasks, now_iso, time_zone) {
    const result = {};
    for (const group of task_board_data.group_by_due(tasks, Date.parse(now_iso), time_zone)) {
        result[group.section] = group.tasks.map((task) => task.id);
    }
    return result;
}

run_test("cards are grouped by how soon they are due", () => {
    // Friday 16 Oct 2026, 10:00 in Jakarta.
    const now = "2026-10-16T03:00:00Z";
    // The end of that day in Jakarta.
    const day = (date) => seconds(`${date}T16:59:59Z`);
    const tasks = [
        make_task({id: 1, due_at: day("2026-10-14")}),
        make_task({id: 2, due_at: day("2026-10-16")}),
        make_task({id: 3, due_at: day("2026-10-17")}),
        make_task({id: 4, due_at: day("2026-10-18")}),
        make_task({id: 5, due_at: day("2026-10-23")}),
        make_task({id: 6, due_at: day("2026-10-24")}),
        make_task({id: 7, due_at: null}),
    ];
    assert.deepEqual(sections_of(tasks, now, JAKARTA), {
        overdue: [1],
        today: [2],
        tomorrow: [3],
        next_7_days: [4, 5],
        later: [6],
        no_due_date: [7],
    });
    assert.deepEqual(
        task_board_data.group_by_due(tasks, Date.parse(now), JAKARTA).map((group) => group.section),
        ["overdue", "today", "tomorrow", "next_7_days", "later", "no_due_date"],
    );
});

run_test("the day boundary follows the reader's time zone", () => {
    // 00:30 on Saturday 17 Oct in Jakarta is still Friday 16 Oct in UTC
    // and Los Angeles.
    const now = "2026-10-16T17:30:00Z";
    const due_end_of_friday_jakarta = seconds("2026-10-16T16:59:59Z");
    const tasks = [make_task({id: 1, due_at: due_end_of_friday_jakarta})];

    assert.equal(sections_of(tasks, now, JAKARTA).overdue.length, 1);
    assert.equal(sections_of(tasks, now, "UTC").today.length, 1);
    assert.equal(sections_of(tasks, now, LOS_ANGELES).today.length, 1);

    // One second before and after midnight in Jakarta.
    const just_before = "2026-10-16T16:59:59Z";
    const just_after = "2026-10-16T17:00:00Z";
    const due_saturday = [make_task({id: 2, due_at: seconds("2026-10-17T16:59:59Z")})];
    assert.equal(sections_of(due_saturday, just_before, JAKARTA).tomorrow.length, 1);
    assert.equal(sections_of(due_saturday, just_after, JAKARTA).today.length, 1);
});

run_test("grouping counts calendar days across a daylight saving change", () => {
    // Los Angeles ends daylight saving on 1 Nov 2026, so that day has
    // 25 hours. Saturday 31 Oct 22:00 PDT is Sunday 1 Nov 05:00 UTC.
    const now = "2026-11-01T05:00:00Z";
    const due = (date) => task_board_data.end_of_day_timestamp(date, LOS_ANGELES);
    const tasks = [
        make_task({id: 1, due_at: due("2026-10-31")}),
        make_task({id: 2, due_at: due("2026-11-01")}),
        make_task({id: 3, due_at: due("2026-11-02")}),
        make_task({id: 4, due_at: due("2026-11-07")}),
        make_task({id: 5, due_at: due("2026-11-08")}),
    ];
    assert.deepEqual(sections_of(tasks, now, LOS_ANGELES), {
        overdue: [],
        today: [1],
        tomorrow: [2],
        next_7_days: [3, 4],
        later: [5],
        no_due_date: [],
    });
});

run_test("inside a section the earliest deadline comes first, then the higher priority", () => {
    const now = "2026-10-16T03:00:00Z";
    const tasks = [
        make_task({id: 1, due_at: seconds("2026-10-20T16:59:59Z"), priority: 3}),
        make_task({id: 2, due_at: seconds("2026-10-19T16:59:59Z"), priority: 0}),
        make_task({id: 3, due_at: seconds("2026-10-19T16:59:59Z"), priority: 2}),
        make_task({id: 4, due_at: null, priority: 1, position: 2000}),
        make_task({id: 5, due_at: null, priority: 3, position: 3000}),
        make_task({id: 6, due_at: null, priority: 1, position: 1000}),
    ];
    const sections = sections_of(tasks, now, JAKARTA);
    assert.deepEqual(sections.next_7_days, [3, 2, 1]);
    assert.deepEqual(sections.no_due_date, [5, 6, 4]);
});

run_test("a due date is stored as the end of the chosen day and read back as that day", () => {
    assert.equal(
        task_board_data.end_of_day_timestamp("2026-10-17", JAKARTA),
        seconds("2026-10-17T16:59:59Z"),
    );
    assert.equal(
        task_board_data.end_of_day_timestamp("2026-10-17", LOS_ANGELES),
        seconds("2026-10-18T06:59:59Z"),
    );
    for (const time_zone of [JAKARTA, LOS_ANGELES, "UTC"]) {
        for (const date of ["2026-01-01", "2026-03-08", "2026-10-17", "2026-11-01", "2026-12-31"]) {
            const due_at = task_board_data.end_of_day_timestamp(date, time_zone);
            assert.equal(task_board_data.due_date_input_value(due_at, time_zone), date);
        }
    }
    assert.equal(task_board_data.due_date_input_value(null, JAKARTA), "");
});

run_test("the personal list holds only the reader's open cards", () => {
    load([
        make_task({id: 1, assignee_id: 5}),
        make_task({id: 2, assignee_id: 6}),
        make_task({id: 3, assignee_id: null}),
        make_task({id: 4, assignee_id: 5, column_id: 12, completed_at: 200}),
        make_task({id: 5, assignee_id: 5, column_id: 11}),
    ]);
    assert.deepEqual(
        task_board_data
            .open_tasks_assigned_to(5)
            .map((task) => task.id)
            .toSorted(),
        [1, 5],
    );
    assert.deepEqual(task_board_data.open_tasks_assigned_to(9), []);

    // A card handed to someone else, or finished, leaves the list.
    task_board_data.add_or_update_task(make_task({id: 1, assignee_id: 6}));
    task_board_data.add_or_update_task(
        make_task({id: 5, assignee_id: 5, column_id: 12, completed_at: 300}),
    );
    assert.deepEqual(task_board_data.open_tasks_assigned_to(5), []);
});

run_test("the board remembers whether it shows the board or the personal list", () => {
    assert.equal(task_board_data.get_view(), "board");
    task_board_data.set_view("list");
    assert.equal(task_board_data.get_view(), "list");
    task_board_data.set_view("board");
    assert.equal(task_board_data.get_view(), "board");
});

// 09:00 on 16 Oct 2026 in Jakarta.
const NOW_MS = Date.UTC(2026, 9, 16, 2, 0);
const TODAY = "2026-10-16";
const YESTERDAY = "2026-10-15";

function due_in_jakarta(day_offset, hour = 18) {
    return Math.floor(Date.UTC(2026, 9, 16 + day_offset, hour - 7) / 1000);
}

function plan(date, ...task_ids) {
    task_board_data.set_plan(
        date,
        task_ids.map((task_id, index) => ({task_id, date, position: (index + 1) * 1000})),
    );
}

run_test("a plan day is the reader's own calendar day", () => {
    // 20:00 UTC on 15 Oct is already 16 Oct in Jakarta, and still 15 Oct in New York.
    const moment = Date.UTC(2026, 9, 15, 20, 0);
    assert.equal(task_board_data.local_date(moment, JAKARTA), "2026-10-16");
    assert.equal(task_board_data.local_date(moment, "America/New_York"), "2026-10-15");
    assert.equal(task_board_data.shift_date("2026-10-01", -1), "2026-09-30");
    assert.equal(task_board_data.shift_date("2026-12-31", 1), "2027-01-01");
});

run_test("today's plan keeps the person's order and drops cards that are gone or done", () => {
    load([
        make_task({id: 1}),
        make_task({id: 2}),
        make_task({id: 3, column_id: 12, completed_at: 300}),
    ]);
    // Card 9 was deleted or became unreadable, so the board never sent it.
    task_board_data.set_plan(TODAY, [
        {task_id: 2, date: TODAY, position: 1000},
        {task_id: 9, date: TODAY, position: 1500},
        {task_id: 3, date: TODAY, position: 1800},
        {task_id: 1, date: TODAY, position: 2000},
    ]);

    const today = task_board_data.todays_plan(5, NOW_MS, JAKARTA);

    assert.deepEqual(
        today.planned.map((task) => task.id),
        [2, 1],
    );
});

run_test("overdue and due-today cards of the reader show up on their own", () => {
    load([
        make_task({id: 1, assignee_id: 5, due_at: due_in_jakarta(-2)}),
        make_task({id: 2, assignee_id: 5, due_at: due_in_jakarta(0)}),
        make_task({id: 3, assignee_id: 5, due_at: due_in_jakarta(1)}),
        make_task({id: 4, assignee_id: 5, due_at: null}),
        make_task({id: 5, assignee_id: 6, due_at: due_in_jakarta(0)}),
        make_task({id: 6, assignee_id: 5, due_at: due_in_jakarta(-1), completed_at: 300}),
        make_task({id: 7, assignee_id: 5, due_at: due_in_jakarta(0), priority: 3}),
    ]);
    plan(TODAY);

    const today = task_board_data.todays_plan(5, NOW_MS, JAKARTA);

    assert.deepEqual(
        today.auto.map((task) => task.id),
        [1, 7, 2],
    );
    assert.deepEqual(today.planned, []);
});

run_test("a planned card is not repeated in the automatic part", () => {
    load([
        make_task({id: 1, assignee_id: 5, due_at: due_in_jakarta(0)}),
        make_task({id: 2, assignee_id: 5, due_at: due_in_jakarta(-1)}),
    ]);
    plan(TODAY, 1);

    const today = task_board_data.todays_plan(5, NOW_MS, JAKARTA);

    assert.deepEqual(
        today.planned.map((task) => task.id),
        [1],
    );
    assert.deepEqual(
        today.auto.map((task) => task.id),
        [2],
    );
});

run_test("yesterday's unfinished cards are offered, not moved", () => {
    load([
        make_task({id: 1}),
        make_task({id: 2}),
        make_task({id: 3, column_id: 12, completed_at: 300}),
        make_task({id: 4}),
    ]);
    plan(YESTERDAY, 1, 2, 3, 4);
    plan(TODAY, 2);

    const today = task_board_data.todays_plan(5, NOW_MS, JAKARTA);

    assert.deepEqual(
        today.carry_over.map((task) => task.id),
        [1, 4],
    );
    // Offering does not touch either plan.
    assert.deepEqual(
        task_board_data.plan_entries(TODAY).map((entry) => entry.task_id),
        [2],
    );
    assert.equal(task_board_data.plan_entries(YESTERDAY).length, 4);

    // Once a card is added for today it is no longer offered.
    task_board_data.add_plan_entry({task_id: 1, date: TODAY, position: 3000});
    assert.deepEqual(
        task_board_data.todays_plan(5, NOW_MS, JAKARTA).carry_over.map((task) => task.id),
        [4],
    );
});

run_test("plan events add, reorder and remove entries of a loaded day only", () => {
    plan(TODAY, 1, 2);

    task_board_data.add_plan_entry({task_id: 3, date: TODAY, position: 3000});
    task_board_data.add_plan_entry({task_id: 3, date: TODAY, position: 3500});
    assert.deepEqual(
        task_board_data.plan_entries(TODAY).map((entry) => [entry.task_id, entry.position]),
        [
            [1, 1000],
            [2, 2000],
            [3, 3500],
        ],
    );

    task_board_data.reorder_plan_entry(3, TODAY, 500);
    task_board_data.remove_plan_entry(1, TODAY);
    assert.deepEqual(
        task_board_data.plan_entries(TODAY).map((entry) => entry.task_id),
        [3, 2],
    );
    assert.ok(task_board_data.is_planned(3, TODAY));
    assert.ok(!task_board_data.is_planned(1, TODAY));

    // A day that was never fetched stays unknown.
    task_board_data.add_plan_entry({task_id: 1, date: "2026-11-01", position: 1000});
    assert.ok(!task_board_data.has_plan_for("2026-11-01"));
    assert.deepEqual(task_board_data.plan_entries("2026-11-01"), []);

    task_board_data.clear();
    assert.ok(!task_board_data.has_plan_for(TODAY));
});

run_test("a planned card lands between the neighbors it was moved against", () => {
    plan(TODAY, 1, 2, 3);

    // Top, middle, and bottom of the visible list, with card 3 moving.
    assert.equal(task_board_data.plan_position_for_slot(TODAY, [1, 2], 0, 3), 0);
    assert.equal(task_board_data.plan_position_for_slot(TODAY, [1, 2], 1, 3), 1500);
    assert.equal(task_board_data.plan_position_for_slot(TODAY, [1, 2], 2, 3), 3000);

    // Hidden planned cards (card 2 is done) keep their place: moving card 3
    // below card 1 puts it between 1 and the hidden 2.
    assert.equal(task_board_data.plan_position_for_slot(TODAY, [1], 1, 3), 1500);

    plan(TODAY);
    assert.equal(task_board_data.plan_position_for_slot(TODAY, [], 0, 1), 1000);
});

run_test("a new card is due three working days after today", () => {
    const at = (iso) => Date.parse(iso);
    // Mon 19 Oct 2026 -> Thu 22 Oct.
    assert.equal(task_board_data.default_due_date(at("2026-10-19T05:00:00Z"), "UTC"), "2026-10-22");
    // Wed 21 Oct -> Mon 26 Oct: Saturday and Sunday do not count.
    assert.equal(task_board_data.default_due_date(at("2026-10-21T05:00:00Z"), "UTC"), "2026-10-26");
    // Fri 23 Oct -> Wed 28 Oct.
    assert.equal(task_board_data.default_due_date(at("2026-10-23T05:00:00Z"), "UTC"), "2026-10-28");
    // Sat 24 Oct and Sun 25 Oct -> Wed 28 Oct.
    assert.equal(task_board_data.default_due_date(at("2026-10-24T05:00:00Z"), "UTC"), "2026-10-28");
    assert.equal(task_board_data.default_due_date(at("2026-10-25T05:00:00Z"), "UTC"), "2026-10-28");
    // The first working day after New Year across a year boundary.
    assert.equal(task_board_data.default_due_date(at("2026-12-30T05:00:00Z"), "UTC"), "2027-01-04");
});

run_test("today is the workspace's day, not the browser's", () => {
    // 23:00 UTC on Fri 23 Oct is already Sat 24 Oct in Jakarta, so the
    // three working days run from Saturday.
    const now_ms = Date.parse("2026-10-23T23:00:00Z");
    assert.equal(task_board_data.default_due_date(now_ms, "UTC"), "2026-10-28");
    assert.equal(task_board_data.default_due_date(now_ms, "Asia/Jakarta"), "2026-10-28");
    // 23:00 UTC on Wed 21 Oct is Thu 22 Oct in Jakarta: Mon 26 versus Tue 27.
    const wednesday = Date.parse("2026-10-21T23:00:00Z");
    assert.equal(task_board_data.default_due_date(wednesday, "UTC"), "2026-10-26");
    assert.equal(task_board_data.default_due_date(wednesday, "Asia/Jakarta"), "2026-10-27");
});

run_test("the board tells the workspace time zone", () => {
    task_board_data.set_board({
        board,
        columns,
        tasks: [],
        folded_task_ids: [],
        workspace_timezone: "Asia/Makassar",
    });
    assert.equal(task_board_data.get_workspace_timezone(), "Asia/Makassar");
    task_board_data.clear();
    assert.equal(task_board_data.get_workspace_timezone(), undefined);
});
