"use strict";

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

set_global("document", {hasFocus: () => true});
const message_flags = mock_esm("../src/message_flags");
const unread_ui = mock_esm("../src/unread_ui");
const unread = zrequire("unread");
const unread_ops = zrequire("unread_ops");

run_test("get_message_count_text", () => {
    unread.set_old_unreads_missing_for_tests(true);
    assert.equal(
        unread_ops.get_message_count_text(5),
        "translated: 5+ messages will be marked as read.",
    );
    assert.equal(
        unread_ops.get_message_count_text(1),
        "translated: 1+ messages will be marked as read.",
    );

    unread.set_old_unreads_missing_for_tests(false);
    assert.equal(
        unread_ops.get_message_count_text(5),
        "translated: 5 messages will be marked as read.",
    );
    assert.equal(
        unread_ops.get_message_count_text(1),
        "translated: 1 message will be marked as read.",
    );
});

run_test("notify_server_message_ids_read", ({override}) => {
    unread.set_old_unreads_missing_for_tests(false);
    for (const id of [101, 102]) {
        unread.process_unread_message({
            id,
            type: "stream",
            stream_id: 7,
            topic: "standup",
            mentioned: false,
            mentioned_me_directly: false,
            unread: true,
        });
    }
    assert.deepEqual(unread.get_unread_message_ids([101, 102, 103]), [101, 102]);

    const flag_updates = [];
    override(message_flags, "mark_as_read", (message_ids) => {
        flag_updates.push(message_ids);
    });
    let count_updates = 0;
    override(unread_ui, "update_unread_counts", () => {
        count_updates += 1;
    });

    // Only unread messages are sent to the server and counted as read.
    unread_ops.notify_server_message_ids_read([101, 103]);
    assert.deepEqual(flag_updates, [[101]]);
    assert.equal(count_updates, 1);
    assert.deepEqual(unread.get_unread_message_ids([101, 102]), [102]);
    assert.equal(unread.num_unread_for_topic(7, "standup"), 1);

    // Nothing is left to mark: no request and no update of the counts.
    unread_ops.notify_server_message_ids_read([101, 103]);
    assert.deepEqual(flag_updates, [[101]]);
    assert.equal(count_updates, 1);
});
