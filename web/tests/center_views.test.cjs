"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const center_views = zrequire("center_views");

function make_view(id) {
    const view = {
        id,
        hash: id,
        visible: false,
        hide_calls: 0,
        shown_args: undefined,
        title_during_show: undefined,
        show(args) {
            view.visible = true;
            view.shown_args = args;
            view.title_during_show = center_views.current_title();
        },
        hide() {
            view.hide_calls += 1;
            view.visible = false;
        },
        title: () => `Title for ${id}`,
    };
    return view;
}

const today = make_view("today");
const needs = make_view("needs");
center_views.register(today);
center_views.register(needs);

run_test("nothing_is_visible_before_the_first_show", () => {
    assert.equal(center_views.is_any_visible(), false);
    assert.equal(center_views.current(), undefined);
    assert.equal(center_views.current_title(), undefined);
});

run_test("show_passes_args_and_records_the_view_first", () => {
    center_views.show("today", ["a", "b"]);

    assert.equal(today.visible, true);
    assert.deepEqual(today.shown_args, ["a", "b"]);
    // The view's own show() can already read its title, which it
    // needs to set the browser tab title.
    assert.equal(today.title_during_show, "Title for today");
    assert.equal(center_views.current(), "today");
    assert.equal(center_views.is_any_visible(), true);
});

run_test("only_one_view_is_visible_at_a_time", () => {
    center_views.show("today", []);
    center_views.show("needs", []);

    assert.equal(today.visible, false);
    assert.equal(needs.visible, true);
    assert.equal(center_views.current(), "needs");
    assert.equal(center_views.current_title(), "Title for needs");
});

run_test("showing_the_visible_view_again_does_not_hide_it", () => {
    center_views.show("needs", []);
    const hide_calls = needs.hide_calls;

    center_views.show("needs", ["5", "confirm"]);

    assert.equal(needs.hide_calls, hide_calls);
    assert.equal(needs.visible, true);
    assert.deepEqual(needs.shown_args, ["5", "confirm"]);
});

run_test("hide_others_keeps_the_named_view", () => {
    center_views.show("today", []);

    center_views.hide_others("needs");

    assert.equal(today.visible, false);
    assert.equal(center_views.current(), "needs");
});

run_test("hide_all_hides_every_view", () => {
    center_views.show("today", []);

    center_views.hide_all();

    assert.equal(today.visible, false);
    assert.equal(needs.visible, false);
    assert.equal(center_views.current(), undefined);
    assert.equal(center_views.is_any_visible(), false);
    assert.equal(center_views.current_title(), undefined);
});

run_test("show_default_args", () => {
    center_views.show("today");
    assert.deepEqual(today.shown_args, []);
});

run_test("show_unknown_id_throws", () => {
    assert.throws(() => {
        center_views.show("does-not-exist", []);
    });
});

run_test("register_duplicate_id_throws", () => {
    assert.throws(() => {
        center_views.register(make_view("today"));
    });
});
