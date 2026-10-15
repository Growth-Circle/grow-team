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

run_test("the board hash names a filter or one card", () => {
    assert.deepEqual(task_board_data.parse_hash_args([]), {filter: "all", task_id: undefined});
    assert.deepEqual(task_board_data.parse_hash_args(["mine"]), {
        filter: "mine",
        task_id: undefined,
    });
    assert.deepEqual(task_board_data.parse_hash_args(["review"]), {
        filter: "review",
        task_id: undefined,
    });
    assert.deepEqual(task_board_data.parse_hash_args(["42"]), {filter: "all", task_id: 42});
    // Anything else is not a card, so the board opens as it always did.
    assert.deepEqual(task_board_data.parse_hash_args(["42abc"]), {
        filter: "all",
        task_id: undefined,
    });
    assert.deepEqual(task_board_data.parse_hash_args(["-1"]), {
        filter: "all",
        task_id: undefined,
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
