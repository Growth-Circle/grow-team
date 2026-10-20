"use strict";

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

mock_esm("../src/resize", {
    resize_stream_filters_container() {},
});

const {Filter} = zrequire("../src/filter");
const left_sidebar_navigation_area = zrequire("left_sidebar_navigation_area");
const scheduled_messages = zrequire("scheduled_messages");
const message_reminder = zrequire("message_reminder");
const nav_counts = zrequire("nav_counts");
const {set_current_user} = zrequire("state_data");

run_test("narrowing", ({override_rewire}) => {
    override_rewire(
        left_sidebar_navigation_area,
        "select_top_left_corner_item",
        (narrow_to_activate) => {
            const targets = [
                ".top_left_mentions",
                ".top_left_starred_messages",
                ".top_left_all_messages",
                ".top_left_recent_view",
                ".top_left_inbox",
            ];
            for (const target of targets) {
                $(target).removeClass("top-left-active-filter");
            }
            if (narrow_to_activate !== "") {
                $(narrow_to_activate).addClass("top-left-active-filter");
            }
        },
    );

    let filter = new Filter([{operator: "is", operand: "mentioned"}]);

    // activating narrow

    left_sidebar_navigation_area.handle_narrow_activated(filter);
    assert.ok($(".top_left_mentions").hasClass("top-left-active-filter"));

    filter = new Filter([{operator: "is", operand: "starred"}]);
    left_sidebar_navigation_area.handle_narrow_activated(filter);
    assert.ok($(".top_left_starred_messages").hasClass("top-left-active-filter"));

    filter = new Filter([{operator: "in", operand: "home"}]);
    left_sidebar_navigation_area.handle_narrow_activated(filter);
    assert.ok($(".top_left_all_messages").hasClass("top-left-active-filter"));

    // deactivating narrow

    left_sidebar_navigation_area.handle_narrow_activated(new Filter([]));

    assert.ok(!$(".top_left_all_messages").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_mentions").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_starred_messages").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_recent_view").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_inbox").hasClass("top-left-active-filter"));

    set_global("setTimeout", (f) => {
        f();
    });
    left_sidebar_navigation_area.highlight_recent_view();
    assert.ok(!$(".top_left_all_messages").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_mentions").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_starred_messages").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_inbox").hasClass("top-left-active-filter"));
    assert.ok($(".top_left_recent_view").hasClass("top-left-active-filter"));

    left_sidebar_navigation_area.handle_narrow_activated(new Filter([]));
    left_sidebar_navigation_area.highlight_inbox_view();
    assert.ok(!$(".top_left_all_messages").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_mentions").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_starred_messages").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_recent_view").hasClass("top-left-active-filter"));
    assert.ok($(".top_left_inbox").hasClass("top-left-active-filter"));

    left_sidebar_navigation_area.highlight_all_messages_view();
    assert.ok(!$(".top_left_mentions").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_starred_messages").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_recent_view").hasClass("top-left-active-filter"));
    assert.ok(!$(".top_left_inbox").hasClass("top-left-active-filter"));
    assert.ok($(".top_left_all_messages").hasClass("top-left-active-filter"));
});

run_test("update_count_in_dom", () => {
    function make_elem($elem, count_selector) {
        const $count = $(count_selector);
        $elem.set_find_results(".unread_count", $count);
        $count.set_parent($elem);

        return $elem;
    }

    const counts = {
        mentioned_message_count: 222,
        home_unread_messages: 333,
        stream_unread_messages: 666,
        stream_count: new Map(),
    };
    message_reminder.set_reminders_by_id_for_testing(new Map([[1, {id: 1}]]));
    scheduled_messages.set_scheduled_messages_by_id_for_testing(
        new Map([
            [1, {id: 1}],
            [2, {id: 2}],
        ]),
    );

    $(".selected-home-view").set_find_results(".sidebar-menu-icon", $("<menu-icon>"));

    make_elem($(".top_left_mentions"), "<mentioned-count>");

    make_elem($(".top_left_inbox"), "<home-count>");

    make_elem($(".selected-home-view"), "<home-count>");

    make_elem($(".top_left_condensed_unread_marker"), "<condensed-unread-count>");

    make_elem($(".top_left_starred_messages"), "<starred-count>");

    make_elem($(".top_left_scheduled_messages"), "<scheduled-count>");

    make_elem($(".top_left_reminders"), "<reminders-count>");

    left_sidebar_navigation_area.update_dom_with_unread_counts(counts, false);
    left_sidebar_navigation_area.update_starred_count(444, false);
    left_sidebar_navigation_area.update_scheduled_messages_row();
    left_sidebar_navigation_area.update_reminders_row();

    assert.equal($("<mentioned-count>").text(), "222");
    assert.equal($("<home-count>").text(), "333");
    assert.equal($("<condensed-unread-count>").text(), "333");
    assert.equal($("<starred-count>").text(), "444");
    assert.equal($("<scheduled-count>").text(), "2");
    assert.equal($("<reminders-count>").text(), "1");
    assert.ok(!$(".top_left_scheduled_messages").hasClass("hidden-by-filters"));
    assert.ok(!$(".top_left_reminders").hasClass("hidden-by-filters"));

    counts.mentioned_message_count = 0;
    message_reminder.set_reminders_by_id_for_testing(new Map());
    scheduled_messages.set_scheduled_messages_by_id_for_testing(new Map());

    left_sidebar_navigation_area.update_dom_with_unread_counts(counts, false);
    // Starred count is hidden.
    left_sidebar_navigation_area.update_starred_count(444, true);
    left_sidebar_navigation_area.update_scheduled_messages_row();
    left_sidebar_navigation_area.update_reminders_row();

    assert.ok(!$("<mentioned-count>").visible());
    assert.equal($("<mentioned-count>").text(), "");
    assert.equal($("<starred-count>").text(), "444");
    assert.ok($(".top_left_starred_messages").hasClass("hide_starred_message_count"));
    assert.ok($(".top_left_scheduled_messages").hasClass("hidden-by-filters"));
    assert.ok($(".top_left_reminders").hasClass("hidden-by-filters"));
});

const GROUP_ITEMS = ["agents", "routines", "playbooks", "mcp", "runners"];

run_test("get_nav_group_state", () => {
    const state = (opts) =>
        left_sidebar_navigation_area.get_nav_group_state({
            item_ids: GROUP_ITEMS,
            shown_ids: new Set(["routines", "mcp"]),
            active_id: undefined,
            saved_expanded: false,
            attention_count: 0,
            ...opts,
        });

    // AC-S1-001: the saved choice opens and closes the group.
    assert.deepEqual(state({}), {visible: true, expanded: false, attention_count: 0});
    assert.equal(state({saved_expanded: true}).expanded, true);

    // AC-S1-002: a page inside the group keeps it open, saved or not.
    assert.equal(state({active_id: "mcp"}).expanded, true);
    // A page outside the group, or a hidden item of it, does not.
    assert.equal(state({active_id: "tasks"}).expanded, false);
    assert.equal(state({active_id: "runners"}).expanded, false);

    // AC-S1-004: no item shown, no group, whatever the saved choice is.
    const hidden = state({shown_ids: new Set(["tasks"]), saved_expanded: true});
    assert.equal(hidden.visible, false);
    assert.equal(hidden.expanded, false);
});

run_test("automation_group_in_dom", () => {
    const win = {location: {hash: "#inbox"}};
    set_global("window", win);
    set_current_user({is_owner: false, is_admin: false, is_guest: false});

    for (const id of [...GROUP_ITEMS, "home", "needs", "tasks", "my_tasks", "drive", "settings"]) {
        $(`.sanji-nav-item[data-nav-id="${id}"]`).set_find_results("a", $(`<a-${id}>`));
    }
    const $group = $('.sanji-nav-group[data-nav-group="automation"]');
    const $toggle = $("<group-toggle>");
    const $list = $("<group-list>");
    const $dot = $("<group-dot>");
    $group.set_find_results(".sanji-nav-group-toggle", $toggle);
    $group.set_find_results(".sanji-nav-group-list", $list);
    $group.set_find_results(".sanji-nav-group-attention", $dot);
    $dot.addClass("hide");
    $list.prop("hidden", true);

    left_sidebar_navigation_area.update_nav_targets();
    // Agents and Runners have no page yet, so three items are shown.
    assert.ok(!$group.hasClass("hidden"));
    assert.ok($(`.sanji-nav-item[data-nav-id="agents"]`).hasClass("hidden"));
    assert.ok(!$(`.sanji-nav-item[data-nav-id="mcp"]`).hasClass("hidden"));
    assert.equal($toggle.attr("aria-expanded"), "false");
    assert.equal($list.prop("hidden"), true);
    assert.ok($dot.hasClass("hide"));

    // AC-S1-003: a count in a closed group shows as a labelled dot.
    nav_counts.set_count("mcp", 2);
    left_sidebar_navigation_area.update_automation_group();
    assert.ok(!$dot.hasClass("hide"));
    assert.equal($dot.attr("aria-label"), "translated: Needs attention (2)");

    // AC-S1-002: opening a page inside the group shows it, and the dot goes.
    win.location.hash = "#mcp";
    left_sidebar_navigation_area.update_sanji_nav_active_state();
    assert.equal($toggle.attr("aria-expanded"), "true");
    assert.equal($list.prop("hidden"), false);
    assert.ok($dot.hasClass("hide"));
    assert.equal(localStorage.getItem("ls__1__left_sidebar_automation_group_expanded"), undefined);

    // AC-S1-001: a click saves the choice, and the group follows it.
    win.location.hash = "#inbox";
    left_sidebar_navigation_area.update_automation_group();
    assert.equal($toggle.attr("aria-expanded"), "false");
    left_sidebar_navigation_area.toggle_automation_group();
    assert.equal($toggle.attr("aria-expanded"), "true");
    assert.equal($list.prop("hidden"), false);
    left_sidebar_navigation_area.toggle_automation_group();
    assert.equal($toggle.attr("aria-expanded"), "false");
    assert.equal($list.prop("hidden"), true);
    assert.ok(!$dot.hasClass("hide"));

    nav_counts.set_count("mcp", 0);
});
