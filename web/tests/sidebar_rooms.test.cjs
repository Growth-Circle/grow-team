"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const sidebar_rooms = zrequire("sidebar_rooms");

const folders = [
    {id: 1, name: "Proyek"},
    {id: 2, name: "Klien"},
    {id: 3, name: "Tim"},
    {id: 4, name: "Finance"},
];

run_test("accent_for_section", () => {
    const accent = (id, folder_id) => sidebar_rooms.accent_for_section({id, folder_id}, folders);

    assert.equal(accent("pinned-streams", null), "var(--sj-kuning)");
    assert.equal(accent("1", 1), "var(--sj-kaki)");
    assert.equal(accent("2", 2), "var(--sj-ayame)");
    assert.equal(accent("3", 3), "var(--sj-matcha)");
    // A folder with another name, and rooms without a folder, get the muted dot.
    assert.equal(accent("4", 4), "var(--sj-sidebar-text-muted)");
    assert.equal(accent("normal-streams", null), "var(--sj-sidebar-text-muted)");
    // A folder that was deleted while the sidebar was open.
    assert.equal(accent("9", 9), "var(--sj-sidebar-text-muted)");
});

run_test("quiet_room_rows", () => {
    const now = 100 * 86400;
    const names = new Map([
        [10, "old-launch"],
        [11, "archive-2025"],
    ]);
    const rows = sidebar_rooms.quiet_room_rows(
        [
            {stream_id: 10, last_message_at: now - 42 * 86400 - 3600},
            // The person left this room, so the sidebar cannot name it.
            {stream_id: 99, last_message_at: now - 50 * 86400},
            {stream_id: 11, last_message_at: now - 31 * 86400},
        ],
        (stream_id) => names.get(stream_id),
        now,
    );
    assert.deepEqual(rows, [
        {name: "old-launch", idle_label: "translated: 42 days"},
        {name: "archive-2025", idle_label: "translated: 31 days"},
    ]);
    assert.deepEqual(
        sidebar_rooms.quiet_room_rows([], () => undefined, now),
        [],
    );
});

run_test("get_rooms_header_state", () => {
    const state = (opts) =>
        sidebar_rooms.get_rooms_header_state({
            saved_collapsed: true,
            search_active: false,
            unread_count: 3,
            ...opts,
        });

    // AC-S4-001: a closed block shows what it hides.
    assert.deepEqual(state({}), {collapsed: true, show_unread: true});
    assert.deepEqual(state({unread_count: 0}), {collapsed: true, show_unread: false});
    // An open block has the counts in its own rows.
    assert.deepEqual(state({saved_collapsed: false}), {collapsed: false, show_unread: false});
    // A search opens the block, so the rooms that match are in sight.
    assert.deepEqual(state({search_active: true}), {collapsed: false, show_unread: false});
});

run_test("rooms_header_in_dom", () => {
    const $search = $("input.left-sidebar-search-input");
    $search.val("");
    const $container = $("#left_sidebar_scroll_container");
    const $toggle = $("#sidebar-rooms-toggle");
    const $unread = $("#sidebar-rooms-header .sidebar-rooms-unread");
    $unread.addClass("hide");

    sidebar_rooms.update_rooms_unread_count(5);
    assert.ok(!$container.hasClass("rooms-collapsed"));
    assert.equal($toggle.attr("aria-expanded"), "true");
    assert.ok($unread.hasClass("hide"));

    // AC-S4-001: closing keeps the count on the header, and the choice is saved.
    sidebar_rooms.toggle_rooms_section();
    assert.ok($container.hasClass("rooms-collapsed"));
    assert.equal($toggle.attr("aria-expanded"), "false");
    assert.ok(!$unread.hasClass("hide"));
    assert.equal($unread.text(), "5");
    assert.equal(
        localStorage.getItem("ls__1__left_sidebar_rooms_collapsed"),
        JSON.stringify({data: true, __valid: true}),
    );

    // The count follows new messages, and goes when nothing is unread.
    sidebar_rooms.update_rooms_unread_count(7);
    assert.equal($unread.text(), "7");
    sidebar_rooms.update_rooms_unread_count(0);
    assert.ok($unread.hasClass("hide"));
    assert.equal($unread.text(), "");
    sidebar_rooms.update_rooms_unread_count(7);

    // A search opens the block without changing the saved choice.
    $search.val("ver");
    sidebar_rooms.update_rooms_collapsed_state();
    assert.ok(!$container.hasClass("rooms-collapsed"));
    assert.equal($toggle.attr("aria-expanded"), "true");
    $search.val("");
    sidebar_rooms.update_rooms_collapsed_state();
    assert.ok($container.hasClass("rooms-collapsed"));

    // Opening again saves that choice too.
    sidebar_rooms.toggle_rooms_section();
    assert.ok(!$container.hasClass("rooms-collapsed"));
    assert.equal($toggle.attr("aria-expanded"), "true");
    assert.ok($unread.hasClass("hide"));
});
