"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const chat_drawer_data = zrequire("chat_drawer_data");

function make_message(overrides) {
    return {
        id: 1,
        sender_id: 5,
        sender_full_name: "Hamlet",
        content: "<p>Hello</p>",
        timestamp: 100,
        ...overrides,
    };
}

function ids(messages) {
    return messages.map((message) => message.id);
}

run_test("a message that comes in twice shows once", () => {
    const existing = [make_message({id: 1}), make_message({id: 3})];
    const merged = chat_drawer_data.merge_messages(existing, [
        make_message({id: 3}),
        make_message({id: 2}),
    ]);
    assert.deepEqual(ids(merged), [1, 2, 3]);
    assert.deepEqual(ids(existing), [1, 3]);
});

run_test("a later copy of a message replaces the earlier one", () => {
    const merged = chat_drawer_data.merge_messages(
        [make_message({id: 1, content: "<p>old</p>"})],
        [make_message({id: 1, content: "<p>edited</p>"})],
    );
    assert.equal(merged.length, 1);
    assert.equal(merged[0].content, "<p>edited</p>");
});

run_test("deleted messages leave the list", () => {
    const messages = [make_message({id: 1}), make_message({id: 2}), make_message({id: 3})];
    assert.deepEqual(ids(chat_drawer_data.remove_messages(messages, [1, 3])), [2]);
});

run_test("consecutive messages of one sender share one header", () => {
    const groups = chat_drawer_data.group_by_sender([
        make_message({id: 1, sender_id: 5, timestamp: 100}),
        make_message({id: 2, sender_id: 5, timestamp: 160}),
        make_message({id: 3, sender_id: 6, sender_full_name: "Cordelia", timestamp: 200}),
        make_message({id: 4, sender_id: 5, timestamp: 300}),
    ]);

    assert.deepEqual(
        groups.map((group) => [group.sender_id, ids(group.messages)]),
        [
            [5, [1, 2]],
            [6, [3]],
            [5, [4]],
        ],
    );
    assert.equal(groups[0].timestamp, 100);
    assert.equal(groups[1].sender_full_name, "Cordelia");
    assert.deepEqual(chat_drawer_data.group_by_sender([]), []);
});

run_test("a message belongs to the conversation of its channel and topic", () => {
    const conversation = {stream_id: 7, topic: "Deploys"};
    const in_it = {type: "stream", stream_id: 7, topic: "deploys"};

    assert.ok(chat_drawer_data.is_in_conversation(in_it, conversation));
    assert.ok(!chat_drawer_data.is_in_conversation({...in_it, stream_id: 8}, conversation));
    assert.ok(!chat_drawer_data.is_in_conversation({...in_it, topic: "other"}, conversation));
    assert.ok(!chat_drawer_data.is_in_conversation({type: "private"}, conversation));
});

run_test("the empty topic is the conversation of a channel without topics", () => {
    const conversation = {stream_id: 7, topic: ""};

    assert.ok(
        chat_drawer_data.is_in_conversation(
            {type: "stream", stream_id: 7, topic: ""},
            conversation,
        ),
    );
    assert.ok(
        !chat_drawer_data.is_in_conversation(
            {type: "stream", stream_id: 7, topic: "x"},
            conversation,
        ),
    );
});

run_test("the narrow names the channel by id and the topic", () => {
    assert.deepEqual(JSON.parse(chat_drawer_data.narrow_for({stream_id: 7, topic: ""})), [
        {operator: "channel", operand: 7},
        {operator: "topic", operand: ""},
    ]);
});

run_test("Enter follows the Enter sends setting", () => {
    const base = {shift_key: false, ctrl_key: false, meta_key: false, is_composing: false};

    // Enter sends: Enter sends, Shift+Enter is a new line.
    assert.ok(chat_drawer_data.should_send_on_enter({...base, enter_sends: true}));
    assert.ok(
        !chat_drawer_data.should_send_on_enter({...base, enter_sends: true, shift_key: true}),
    );
    assert.ok(chat_drawer_data.should_send_on_enter({...base, enter_sends: true, ctrl_key: true}));

    // Enter sends is off: Enter is a new line, Ctrl or Cmd with Enter sends.
    assert.ok(!chat_drawer_data.should_send_on_enter({...base, enter_sends: false}));
    assert.ok(
        !chat_drawer_data.should_send_on_enter({...base, enter_sends: false, shift_key: true}),
    );
    assert.ok(chat_drawer_data.should_send_on_enter({...base, enter_sends: false, ctrl_key: true}));
    assert.ok(chat_drawer_data.should_send_on_enter({...base, enter_sends: false, meta_key: true}));

    // Enter that confirms an input method composition never sends.
    assert.ok(
        !chat_drawer_data.should_send_on_enter({...base, enter_sends: true, is_composing: true}),
    );
});

run_test("the server messages are parsed to what the drawer shows", () => {
    const {messages} = chat_drawer_data.chat_messages_response_schema.parse({
        result: "success",
        messages: [{...make_message({id: 9}), type: "stream", subject: "x"}],
    });
    assert.deepEqual(messages, [make_message({id: 9})]);
});
