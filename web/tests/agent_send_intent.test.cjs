"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const state = zrequire("agent_send_intent");

function deferred() {
    let resolve;
    const promise = new Promise((done) => {
        resolve = done;
    });
    return {promise, resolve};
}

run_test("a return visit cannot publish the original send", async () => {
    const original = state.capture("draft-a", "channel-a", "same text");
    const pending = deferred();
    let published = 0;
    const continuation = pending.promise.then(() => {
        if (state.is_current(original, "channel-a", "same text")) {
            published += 1;
        }
    });
    state.change_visit();
    state.change_visit();
    pending.resolve();
    await continuation;
    assert.equal(published, 0);
});

run_test("a deliberately cleared draft rejects an old preflight response", async () => {
    const original = state.capture("draft-b", "channel-b", "task text");
    const pending = deferred();
    const continuation = pending.promise.then(() => state.is_current(original, "channel-b", ""));
    state.change_draft();
    pending.resolve();
    assert.equal(await continuation, false);
    assert.equal(state.is_current(original, "channel-b", "task text"), false);
});

run_test("a new draft has a new send identity", () => {
    const first = state.capture("draft-c", "channel-c", "first");
    state.change_draft();
    const second = state.capture("draft-c", "channel-c", "second");
    assert.notEqual(first.send_key, second.send_key);
    assert.equal(state.is_current(second, "channel-c", "second"), true);
});
