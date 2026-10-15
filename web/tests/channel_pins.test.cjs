"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let requests = [];
mock_esm("../src/channel", {
    get(opts) {
        requests.push({method: "GET", ...opts});
    },
    post(opts) {
        requests.push({method: "POST", ...opts});
    },
    del(opts) {
        requests.push({method: "DELETE", ...opts});
    },
});

const rerendered = [];
const list = {view: {rerender_messages: (messages) => rerendered.push(messages)}};
mock_esm("../src/message_lists", {
    all_rendered_message_lists: () => [list],
});
const messages_in_store = new Map();
mock_esm("../src/message_store", {
    get: (message_id) => messages_in_store.get(message_id),
});

let can_post = true;
mock_esm("../src/stream_data", {
    get_sub_by_id: (stream_id) => (stream_id === 7 ? {stream_id} : undefined),
    can_post_messages_in_stream: () => can_post,
});

const reported = [];
mock_esm("../src/ui_report", {
    error: (message_html, xhr) => reported.push([message_html, xhr]),
});

const channel_pins = zrequire("channel_pins");
const channel_pins_data = zrequire("channel_pins_data");

function make_pin(message_id) {
    return {
        message_id,
        stream_id: 7,
        pinned_by_id: 5,
        date_pinned: 100,
        sender_id: 6,
        sender_full_name: "Cordelia",
        topic: "",
        date_sent: 90,
        rendered_content: "<p>x</p>",
    };
}

function reset() {
    requests = [];
    rerendered.length = 0;
    reported.length = 0;
    messages_in_store.clear();
    channel_pins_data.clear();
    can_post = true;
}

run_test("loading the pins keeps them and draws the changed messages again", () => {
    reset();
    const pinned_message = {id: 3};
    messages_in_store.set(3, pinned_message);
    const changed = [];
    const stop = channel_pins.on_change((stream_id) => changed.push(stream_id));

    channel_pins.load(7);
    assert.equal(requests[0].url, "/json/streams/7/pins");
    requests[0].success({result: "success", pins: [make_pin(3), make_pin(4)]});

    assert.ok(channel_pins_data.is_pinned(7, 3));
    // Message 4 is not in the message store, so only message 3 is drawn again.
    assert.deepEqual(rerendered, [[pinned_message]]);
    assert.deepEqual(changed, [7]);

    stop();
    channel_pins.load(7);
    requests[1].success({result: "success", pins: []});
    assert.deepEqual(changed, [7]);
});

run_test("the newest request wins", () => {
    reset();
    let failed = false;
    channel_pins.load(7, () => {
        failed = true;
    });
    channel_pins.load(7);

    requests[0].success({result: "success", pins: [make_pin(3)]});
    assert.equal(channel_pins_data.is_loaded(7), false);
    requests[0].error();
    assert.equal(failed, false);

    requests[1].success({result: "success", pins: [make_pin(4)]});
    assert.ok(channel_pins_data.is_pinned(7, 4));
});

run_test("a failed load tells the caller", () => {
    reset();
    let failed = false;
    channel_pins.load(7, () => {
        failed = true;
    });
    requests[0].error();
    assert.equal(failed, true);
});

run_test("pins of a channel are fetched once for a menu", () => {
    reset();
    channel_pins.ensure_loaded(8);
    channel_pins.ensure_loaded(8);
    assert.equal(requests.length, 1);
});

run_test("only a user who can post may change pins", () => {
    reset();
    assert.equal(channel_pins.can_change_pins(7), true);
    assert.equal(channel_pins.can_change_pins(99), false);
    can_post = false;
    assert.equal(channel_pins.can_change_pins(7), false);
});

run_test("pin and unpin send the request and report a failure", () => {
    reset();
    channel_pins.pin_message(3);
    channel_pins.unpin_message(3);

    assert.deepEqual(
        requests.map((request) => [request.method, request.url]),
        [
            ["POST", "/json/messages/3/pin"],
            ["DELETE", "/json/messages/3/pin"],
        ],
    );
    const xhr = {status: 400};
    requests[0].error(xhr);
    requests[1].error(xhr);
    assert.deepEqual(reported, [
        ["translated: Could not pin the message", xhr],
        ["translated: Could not unpin the message", xhr],
    ]);
});

run_test("a pin event loads the list, an unpin event removes the pin", () => {
    reset();
    channel_pins_data.set_pins(7, [make_pin(3)]);
    messages_in_store.set(3, {id: 3});

    channel_pins.handle_event({op: "add", stream_id: 7, message_id: 4});
    assert.equal(requests[0].url, "/json/streams/7/pins");

    channel_pins.handle_event({op: "remove", stream_id: 7, message_id: 3});
    assert.equal(channel_pins_data.is_pinned(7, 3), false);
    assert.equal(rerendered.length, 1);

    // The pin was already gone, so nothing is drawn again.
    channel_pins.handle_event({op: "remove", stream_id: 7, message_id: 3});
    assert.equal(rerendered.length, 1);
});

run_test("a deleted message leaves the pins and open lists draw again", () => {
    reset();
    channel_pins_data.set_pins(7, [make_pin(3)]);
    channel_pins.load(7);
    const changed = [];
    const stop = channel_pins.on_change((stream_id) => changed.push(stream_id));

    channel_pins.handle_deleted_messages([3]);

    assert.equal(channel_pins_data.is_pinned(7, 3), false);
    assert.ok(changed.includes(7));
    stop();
});
