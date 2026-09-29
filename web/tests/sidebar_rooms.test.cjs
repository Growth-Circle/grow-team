"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

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
