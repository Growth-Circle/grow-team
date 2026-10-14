"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const stream_data = mock_esm("../src/stream_data");
const sub_store = mock_esm("../src/sub_store");

const {set_current_user} = zrequire("state_data");
const {can_edit_task} = zrequire("task_board_permissions");

const current_user = {user_id: 10, is_admin: false, is_guest: false};
set_current_user(current_user);

const origin_sub = {stream_id: 50, name: "design"};
const administered_stream_ids = new Set();

sub_store.get = (stream_id) => (stream_id === origin_sub.stream_id ? origin_sub : undefined);
stream_data.can_administer_channel = (sub) => administered_stream_ids.has(sub.stream_id);

function make_task(overrides) {
    return {
        creator_id: 1,
        assignee_id: 2,
        reviewer_id: 3,
        stream_id: origin_sub.stream_id,
        ...overrides,
    };
}

function reset() {
    current_user.user_id = 10;
    current_user.is_admin = false;
    current_user.is_guest = false;
    administered_stream_ids.clear();
}

run_test("a realm admin can edit any card", () => {
    reset();
    current_user.is_admin = true;
    assert.equal(can_edit_task(make_task({stream_id: null})), true);
});

run_test("creator, assignee and reviewer can edit", () => {
    reset();
    for (const role of ["creator_id", "assignee_id", "reviewer_id"]) {
        assert.equal(can_edit_task(make_task({[role]: 10})), true, role);
    }
});

run_test("an administrator of the origin channel can edit", () => {
    reset();
    administered_stream_ids.add(origin_sub.stream_id);
    assert.equal(can_edit_task(make_task()), true);
});

run_test("anyone else cannot edit", () => {
    reset();
    assert.equal(can_edit_task(make_task()), false);
});

run_test("a card without an origin channel gives no channel rights", () => {
    reset();
    administered_stream_ids.add(origin_sub.stream_id);
    assert.equal(can_edit_task(make_task({stream_id: null})), false);
});

run_test("a channel the client does not know gives no channel rights", () => {
    reset();
    administered_stream_ids.add(999);
    assert.equal(can_edit_task(make_task({stream_id: 999})), false);
});

run_test("a guest gets no channel rights but keeps card roles", () => {
    reset();
    current_user.is_guest = true;
    administered_stream_ids.add(origin_sub.stream_id);
    assert.equal(can_edit_task(make_task()), false);
    assert.equal(can_edit_task(make_task({assignee_id: 10})), true);
});

run_test("an unassigned card has no assignee to match", () => {
    reset();
    assert.equal(can_edit_task(make_task({assignee_id: null, reviewer_id: null})), false);
});
