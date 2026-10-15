"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const channel_pins_data = zrequire("channel_pins_data");

function make_pin(message_id, overrides) {
    return {
        message_id,
        stream_id: 7,
        pinned_by_id: 5,
        date_pinned: 100,
        sender_id: 6,
        sender_full_name: "Cordelia",
        topic: "Deploys",
        date_sent: 90,
        rendered_content: "<p>Checklist</p>",
        ...overrides,
    };
}

function message_ids(stream_id) {
    return channel_pins_data.get_pins(stream_id).map((pin) => pin.message_id);
}

run_test("pins are unknown until they are loaded", () => {
    channel_pins_data.clear();
    assert.equal(channel_pins_data.get_pins(7), undefined);
    assert.equal(channel_pins_data.is_loaded(7), false);
    assert.equal(channel_pins_data.is_pinned(7, 1), false);

    channel_pins_data.set_pins(7, []);
    assert.equal(channel_pins_data.is_loaded(7), true);
    assert.deepEqual(channel_pins_data.get_pins(7), []);
});

run_test("setting the pins names the messages whose pin changed", () => {
    channel_pins_data.clear();

    assert.deepEqual(channel_pins_data.set_pins(7, [make_pin(3), make_pin(1)]).toSorted(), [1, 3]);
    assert.deepEqual(message_ids(7), [3, 1]);
    assert.ok(channel_pins_data.is_pinned(7, 3));
    assert.ok(!channel_pins_data.is_pinned(7, 2));
    // The same message in another channel is not pinned.
    assert.ok(!channel_pins_data.is_pinned(8, 3));

    // Message 3 stays, 1 goes, 2 comes.
    assert.deepEqual(channel_pins_data.set_pins(7, [make_pin(2), make_pin(3)]).toSorted(), [1, 2]);
    assert.deepEqual(channel_pins_data.set_pins(7, [make_pin(2), make_pin(3)]), []);
});

run_test("removing a pin tells whether it was in the list", () => {
    channel_pins_data.clear();
    channel_pins_data.set_pins(7, [make_pin(3), make_pin(1)]);

    assert.equal(channel_pins_data.remove_pin(7, 3), true);
    assert.deepEqual(message_ids(7), [1]);
    assert.equal(channel_pins_data.remove_pin(7, 3), false);
    // A channel whose pins were never loaded has nothing to remove.
    assert.equal(channel_pins_data.remove_pin(9, 1), false);
    assert.equal(channel_pins_data.is_loaded(9), false);
});

run_test("deleted messages leave every list", () => {
    channel_pins_data.clear();
    channel_pins_data.set_pins(7, [make_pin(3), make_pin(1)]);
    channel_pins_data.set_pins(8, [make_pin(2, {stream_id: 8}), make_pin(4, {stream_id: 8})]);

    channel_pins_data.remove_pins_of_messages([1, 2]);

    assert.deepEqual(message_ids(7), [3]);
    assert.deepEqual(message_ids(8), [4]);
});

run_test("the server answer is parsed to the pins the lists keep", () => {
    const {pins} = channel_pins_data.channel_pins_response_schema.parse({
        result: "success",
        pins: [make_pin(3, {pinned_by_id: null})],
    });
    assert.deepEqual(pins, [make_pin(3, {pinned_by_id: null})]);
});
