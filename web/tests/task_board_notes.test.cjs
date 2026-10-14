"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const task_board_notes = zrequire("task_board_notes");

function make_note(overrides) {
    return {
        id: 1,
        task_id: 7,
        author_id: 5,
        content: "Looks good.",
        rendered_content: "<p>Looks good.</p>",
        date_created: 100,
        ...overrides,
    };
}

function ids(notes) {
    return notes.map((note) => note.id);
}

run_test("a new note goes after the older ones", () => {
    const notes = [make_note({id: 1}), make_note({id: 3})];
    const updated = task_board_notes.add_note(notes, make_note({id: 4}));
    assert.deepEqual(ids(updated), [1, 3, 4]);
    assert.deepEqual(ids(notes), [1, 3]);
});

run_test("a note that arrives late takes its place in order", () => {
    const notes = [make_note({id: 1}), make_note({id: 4})];
    assert.deepEqual(ids(task_board_notes.add_note(notes, make_note({id: 3}))), [1, 3, 4]);
});

run_test("the writer's own note is kept once when the event repeats it", () => {
    const first = make_note({id: 2});
    const notes = task_board_notes.add_note([make_note({id: 1})], first);
    const repeated = task_board_notes.add_note(notes, make_note({id: 2}));
    assert.deepEqual(ids(repeated), [1, 2]);
});

run_test("removing a note leaves the others and ignores an unknown id", () => {
    const notes = [make_note({id: 1}), make_note({id: 2})];
    assert.deepEqual(ids(task_board_notes.remove_note(notes, 1)), [2]);
    assert.deepEqual(ids(task_board_notes.remove_note(notes, 9)), [1, 2]);
});

run_test("only the author or an administrator can delete a note", () => {
    const note = make_note({author_id: 5});
    assert.ok(task_board_notes.can_delete_note(note, {user_id: 5, is_admin: false}));
    assert.ok(task_board_notes.can_delete_note(note, {user_id: 6, is_admin: true}));
    assert.ok(!task_board_notes.can_delete_note(note, {user_id: 6, is_admin: false}));
});

run_test("the response of a new note names who was not told", () => {
    const parsed = task_board_notes.add_note_response_schema.parse({
        note: make_note({id: 8}),
        ignored_mentions: [11, 12],
    });
    assert.deepEqual(parsed.ignored_mentions, [11, 12]);
    assert.equal(parsed.note.id, 8);
});
