"use strict";

const assert = require("node:assert/strict");

const {make_stream} = require("./lib/example_stream.cjs");
const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

set_global("document", {});

const center_views = mock_esm("../src/center_views", {
    current_title: () => undefined,
});
const inbox_util = mock_esm("../src/inbox_util", {
    is_visible: () => false,
});
const recent_view_util = mock_esm("../src/recent_view_util", {
    is_visible: () => false,
});
const task_board_data = mock_esm("../src/task_board_data", {
    is_visible: () => false,
});

const {Filter} = zrequire("../src/filter");
const narrow_title = zrequire("narrow_title");
const stream_data = zrequire("stream_data");

const devel = make_stream({name: "devel", stream_id: 100, subscribed: true});
stream_data.add_sub_for_tests(devel);

run_test("recent_task_board_and_inbox_still_win_first", ({override}) => {
    override(recent_view_util, "is_visible", () => true);
    assert.equal(narrow_title.compute_narrow_title(), "translated: Recent conversations");

    override(recent_view_util, "is_visible", () => false);
    override(task_board_data, "is_visible", () => true);
    assert.equal(narrow_title.compute_narrow_title(), "translated: Task board");

    override(task_board_data, "is_visible", () => false);
    override(inbox_util, "is_visible", () => true);
    assert.equal(narrow_title.compute_narrow_title(), "translated: Inbox");
});

run_test("center_view_title_used_when_a_center_view_is_visible", ({override}) => {
    // The center view's title wins before inbox_util.is_visible() is
    // ever read, so this test does not override it.
    override(center_views, "current_title", () => "Needs you");

    assert.equal(narrow_title.compute_narrow_title(), "Needs you");
});

run_test("no_center_view_falls_through_to_inbox", ({override}) => {
    override(inbox_util, "is_visible", () => true);

    assert.equal(narrow_title.compute_narrow_title(), "translated: Inbox");
});

run_test("channel_narrow_uses_filter_title", () => {
    const filter = new Filter([{operator: "channel", operand: devel.stream_id.toString()}]);
    assert.equal(narrow_title.compute_narrow_title(filter), "#devel");
});
