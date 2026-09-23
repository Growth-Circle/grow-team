"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const {
    accepts_auxiliary_response,
    accepts_detail_response,
    agent_selection_label,
    clears_input_on_ack,
    derived_budget_defaults,
    dispatch_receipt_reason_label,
    input_intent_for_draft,
    input_delivery_label,
    job_reason_sentence,
    merge_event_sequences,
    new_client_key,
    resume_unavailable_sentence,
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

run_test("input delivery does not confuse receipt with application", () => {
    assert.match(input_delivery_label("pending"), /Pending/);
    assert.equal(
        input_delivery_label("delivered"),
        "translated: The agent received it and has not used it yet.",
    );
    assert.equal(input_delivery_label("applied"), "translated: Applied by the agent");
    assert.equal(
        input_delivery_label("delivery_uncertain"),
        "translated: Delivery uncertain; status updates automatically",
    );
    assert.equal(input_delivery_label("unrecognized"), "translated: Delivery status unknown");
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

run_test("a hidden or paused default never claims the team has none", () => {
    const label = agent_selection_label("no_eligible_default");
    assert.match(label, /No default agent is available for this task/);
    assert.doesNotMatch(label, /Your team has no default agent/);
});

run_test("a job reason code maps to its own sentence and an unknown code defers", () => {
    assert.match(job_reason_sentence("stop_unconfirmed"), /has not confirmed it stopped/);
    assert.match(job_reason_sentence("start_failed"), /before the agent started work/);
    assert.match(job_reason_sentence("result_invalid"), /no usable result/);
    assert.match(job_reason_sentence("verification_failed"), /required checks failed/);
    assert.match(job_reason_sentence("budget_exhausted"), /used its full budget/);
    assert.match(job_reason_sentence("lease_lost"), /stopped responding/);
    assert.equal(job_reason_sentence("mystery_code"), undefined);
    assert.equal(job_reason_sentence(null), undefined);
    assert.equal(job_reason_sentence(undefined), undefined);
});

run_test("a resume_unavailable_reason maps to why Resume is hidden", () => {
    assert.match(resume_unavailable_sentence("attempt_active"), /still active on its device/);
    assert.match(resume_unavailable_sentence("runner_offline"), /device for this task is offline/);
    assert.match(resume_unavailable_sentence("runner_revoked"), /device for this task was removed/);
    assert.match(resume_unavailable_sentence("mystery_code"), /cannot resume right now/);
    assert.match(resume_unavailable_sentence(null), /cannot resume right now/);
});

run_test("a dispatch receipt reason maps to a sentence, or defers when unknown", () => {
    assert.match(dispatch_receipt_reason_label("queue_full"), /too many tasks that wait/);
    assert.match(dispatch_receipt_reason_label("not_shared"), /Ask its owner to share/);
    assert.match(dispatch_receipt_reason_label("runner_offline"), /runner is offline/);
    assert.match(dispatch_receipt_reason_label("runner_unknown"), /runner is offline/);
    assert.match(
        dispatch_receipt_reason_label("command_not_allowed"),
        /Ask an organization administrator/,
    );
    assert.equal(dispatch_receipt_reason_label(""), undefined);
    assert.equal(dispatch_receipt_reason_label("runner_busy"), undefined);
});

run_test("the token budget scales with the model connection's limits", () => {
    assert.deepEqual(derived_budget_defaults(undefined), {
        input_tokens: 400000,
        output_tokens: 16000,
    });
    // A small model still gets the floor of each range.
    assert.deepEqual(
        derived_budget_defaults({context_window_tokens: 1000, max_output_tokens: 100}),
        {input_tokens: 200000, output_tokens: 16000},
    );
    // A large model is capped at the ceiling of each range.
    assert.deepEqual(
        derived_budget_defaults({context_window_tokens: 10_000_000, max_output_tokens: 1_000_000}),
        {input_tokens: 4000000, output_tokens: 256000},
    );
    // A mid-range model scales linearly inside the range.
    assert.deepEqual(
        derived_budget_defaults({context_window_tokens: 100000, max_output_tokens: 8000}),
        {input_tokens: 1000000, output_tokens: 32000},
    );
});

run_test("client intent key is a version four UUID", () => {
    assert.match(
        new_client_key(),
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
});
