"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const {
    accepts_auxiliary_response,
    accepts_detail_response,
    clears_input_on_ack,
    input_intent_for_draft,
    merge_event_sequences,
    new_client_key,
} = zrequire("agent_ui_state");

function deferred() {
    let resolve_signal;
    const promise = new Promise((resolve) => {
        resolve_signal = resolve;
    });
    return {promise, resolve: resolve_signal};
}

run_test("newer job poll cannot be overwritten by an older deferred attempt", async () => {
    const older = deferred();
    const newer = deferred();
    let accepted_request = 0;
    let version;
    let attempt;
    const receive = async (pending, request) => {
        const response = await pending.promise;
        if (
            accepts_detail_response({
                active: true,
                request,
                accepted_request,
                requested_operation_offset: 0,
                requested_artifact_offset: 0,
                current_operation_offset: 0,
                current_artifact_offset: 0,
                incoming_version: response.version,
                current_version: version,
            })
        ) {
            accepted_request = request;
            version = response.version;
            attempt = response.attempt;
        }
    };
    const first = receive(older, 1);
    const second = receive(newer, 2);
    newer.resolve({version: 4, attempt: "second"});
    await second;
    older.resolve({version: 3, attempt: "first"});
    await first;
    assert.deepEqual({version, attempt}, {version: 4, attempt: "second"});
});

run_test("old pagination and older version cannot replace current job evidence", () => {
    const fence = {
        active: true,
        request: 4,
        accepted_request: 3,
        requested_operation_offset: 0,
        requested_artifact_offset: 0,
        current_operation_offset: 50,
        current_artifact_offset: 0,
        incoming_version: 5,
        current_version: 5,
    };
    assert.equal(accepts_detail_response(fence), false);
    assert.equal(
        accepts_detail_response({...fence, requested_operation_offset: 50, incoming_version: 4}),
        false,
    );
    assert.equal(
        accepts_detail_response({...fence, requested_operation_offset: 50, active: false}),
        false,
    );
});

run_test("ambiguous input retry keeps its key and a newer draft gets another key", () => {
    let keys = 0;
    const next_key = () => {
        keys += 1;
        return `key-${keys}`;
    };
    const first = input_intent_for_draft(undefined, "job-1", "first", 3, 1, next_key);
    const retry = input_intent_for_draft(first, "job-1", "first", 4, 1, next_key);
    assert.equal(retry, first);
    const newer = input_intent_for_draft(retry, "job-1", "changed", 4, 2, next_key);
    assert.equal(newer.key, "key-2");
    assert.equal(first.expected_version, 3);
    const same_text_after_edit = input_intent_for_draft(first, "job-1", "first", 4, 3, next_key);
    assert.notEqual(same_text_after_edit.key, first.key);
    assert.notEqual(first.draft_revision, same_text_after_edit.draft_revision);
    const other_job = input_intent_for_draft(newer, "job-2", "changed", 5, 2, next_key);
    assert.equal(other_job.key, "key-4");
});

run_test("deferred input status cannot overwrite a newer delivered state", async () => {
    const older = deferred();
    const newer = deferred();
    let status = "pending";
    const receive = async (pending, request, latest_request) => {
        const response = await pending.promise;
        if (
            accepts_auxiliary_response({
                active: true,
                request,
                latest_request,
                attempt_id: "attempt-2",
                current_attempt_id: "attempt-2",
                requested_job_version: 8,
                current_job_version: 8,
            })
        ) {
            status = response;
        }
    };
    const old_read = receive(older, 1, 2);
    const new_read = receive(newer, 2, 2);
    newer.resolve("delivered");
    await new_read;
    older.resolve("pending");
    await old_read;
    assert.equal(status, "delivered");
    assert.deepEqual(
        merge_event_sequences(
            [{sequence: 1, type: "old"}],
            [
                {sequence: 1, type: "old"},
                {sequence: 2, type: "new"},
            ],
        ),
        [
            {sequence: 1, type: "old"},
            {sequence: 2, type: "new"},
        ],
    );
});

run_test("A to B to A edits survive an older deferred input acknowledgement", async () => {
    const ack = deferred();
    const sent = input_intent_for_draft(undefined, "job-1", "A", 3, 1, () => "first-key");
    let draft = "A";
    let revision = 1;
    const completion = (async () => {
        await ack.promise;
        if (clears_input_on_ack(sent, revision)) {
            draft = "";
        }
    })();
    draft = "B";
    revision += 1;
    draft = "A";
    revision += 1;
    ack.resolve();
    await completion;
    assert.equal(draft, "A");
    assert.equal(clears_input_on_ack(sent, revision), false);
});

run_test("client intent key is a version four UUID", () => {
    assert.match(
        new_client_key(),
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
});
