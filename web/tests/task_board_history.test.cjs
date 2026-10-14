"use strict";

const assert = require("node:assert/strict");

const {make_user} = require("./lib/example_user.cjs");
const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const people = zrequire("people");
const {history_text} = zrequire("task_board_history");

const alice = make_user({email: "alice@zulip.com", full_name: "Alice Adams", user_id: 1});
const bruno = make_user({email: "bruno@zulip.com", full_name: "Bruno Basuki", user_id: 2});
const gone = make_user({email: "gone@zulip.com", full_name: "Gita Gone", user_id: 3});

people.add_active_user(alice);
people.add_active_user(bruno);
people.add_active_user(gone);
people.deactivate(gone);

// $t marks every message it renders with "translated: ".
function translated(text) {
    return `translated: ${text}`;
}

function entry(kind, acting_user_id, extra_data = {}) {
    return {id: 1, task_id: 1, acting_user_id, kind, event_time: 100, extra_data};
}

run_test("assigned names the new responsible member", () => {
    assert.equal(
        history_text(entry("assigned", 1, {assignee_id: 2})),
        translated("Alice Adams made Bruno Basuki responsible"),
    );
});

run_test("assigned with no assignee says the member was removed", () => {
    assert.equal(
        history_text(entry("assigned", 1, {assignee_id: null})),
        translated("Alice Adams removed the responsible member"),
    );
});

run_test("assigned names a deactivated member", () => {
    assert.equal(
        history_text(entry("assigned", 1, {assignee_id: 3})),
        translated("Alice Adams made Gita Gone responsible"),
    );
});

run_test("assigned to a user the client does not know uses a generic name", () => {
    const text = history_text(entry("assigned", 1, {assignee_id: 999}));
    assert.equal(text, translated(`Alice Adams made ${translated("a former member")} responsible`));
    assert.ok(!text.includes("undefined"));
});

run_test("an unknown or missing actor reads as Someone", () => {
    assert.equal(
        history_text(entry("assigned", null, {assignee_id: 2})),
        translated(`${translated("Someone")} made Bruno Basuki responsible`),
    );
    assert.equal(
        history_text(entry("created", 999)),
        translated(`${translated("Someone")} created the card`),
    );
});

run_test("the other kinds keep their text", () => {
    assert.equal(history_text(entry("created", 1)), translated("Alice Adams created the card"));
    assert.equal(
        history_text(entry("moved", 1, {to_column_name: "Done"})),
        translated("Alice Adams moved the card to Done"),
    );
    assert.equal(history_text(entry("edited", 2)), translated("Bruno Basuki edited the card"));
});
