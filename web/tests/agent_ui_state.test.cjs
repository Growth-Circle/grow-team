"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const {
    accepts_auxiliary_response,
    accepts_detail_response,
    agent_activity_label,
    agent_job_status_sentence,
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
    assert.match(job_reason_sentence("stop_unconfirmed", true), /has not confirmed it stopped/);
    assert.match(job_reason_sentence("start_failed", true), /device could not start this task/);
    assert.match(job_reason_sentence("result_invalid", true), /no usable result/);
    assert.match(job_reason_sentence("verification_failed", true), /required checks failed/);
    assert.match(job_reason_sentence("budget_exhausted", true), /used its full budget/);
    assert.match(job_reason_sentence("lease_lost", true), /stopped responding/);
    assert.equal(job_reason_sentence("mystery_code", true), undefined);
    assert.equal(job_reason_sentence(null, true), undefined);
    assert.equal(job_reason_sentence(undefined, true), undefined);
});

// Contract 12.2: every reason code that offers Resume when it is available
// drops the word "Resume"/"resume" from its sentence when it is not (RL-5).
run_test("contract 12.2: resume-gated reason codes never say Resume when unavailable", () => {
    const cases = {
        stop_unconfirmed: {
            available:
                "The stop request went to the device, but the device has not confirmed it stopped. Wait for the device to come back online, then resume or create a new task.",
            unavailable:
                "The stop request went to the device, but the device has not confirmed it stopped. Wait for the device to come back online.",
        },
        start_failed: {
            available:
                "The device could not start this task. Resume the task, or create a new task.",
            unavailable: "The device could not start this task. Create a new task.",
        },
        runtime_stopped: {
            available:
                "This task stopped before it finished. Resume the task, or create a new task.",
            unavailable: "This task stopped before it finished. Create a new task.",
        },
        lease_lost: {
            available: "The device stopped responding. Resume the task, or create a new task.",
            unavailable: "The device stopped responding. Create a new task.",
        },
        start_deadline_expired: {
            available:
                "This task did not start before its start deadline. Resume the task to queue it again, or create a new task.",
            unavailable: "This task did not start before its start deadline. Create a new task.",
        },
        approval_expired: {
            available:
                "No one decided on the requested action within 15 minutes, so the task stopped. Resume the task, or create a new task.",
            unavailable:
                "No one decided on the requested action within 15 minutes, so the task stopped. Create a new task.",
        },
    };
    for (const [code, sentences] of Object.entries(cases)) {
        assert.equal(job_reason_sentence(code, true), `translated: ${sentences.available}`);
        assert.equal(job_reason_sentence(code, false), `translated: ${sentences.unavailable}`);
        assert.doesNotMatch(job_reason_sentence(code, false), /[Rr]esume/);
    }
});

// The remaining new r25 codes read the same regardless of resume_available,
// and never mention Resume either (RL-5).
run_test("contract 12.2: fixed reason codes ignore resume_available and never say Resume", () => {
    const cases = {
        approval_rejected:
            "The requested action was rejected, so the task stopped. Create a new task to try another way.",
        authority_changed:
            "Access to this agent or its resources changed, so the task stopped. Ask the agent owner to check access.",
        profile_needs_action:
            "This agent needs a fix before it can start. Ask its owner to check the agent.",
        publication_blocked:
            "The result is saved, but it cannot be posted to the conversation yet.",
        audience_changed:
            "The result is saved, but it was not posted because the conversation changed. You can read it below.",
    };
    for (const [code, sentence] of Object.entries(cases)) {
        const expected = `translated: ${sentence}`;
        assert.equal(job_reason_sentence(code, true), expected);
        assert.equal(job_reason_sentence(code, false), expected);
        assert.doesNotMatch(expected, /[Rr]esume/);
    }
});

// Contract 12.2: agent_job_status_sentence's own resume- and extra-driven
// cases (the interrupted default, a queued start_deadline, and a completed
// coding job's delivery target).
run_test("contract 12.2: agent_job_status_sentence resume and extra cases", () => {
    assert.equal(
        agent_job_status_sentence("interrupted", true),
        "translated: This task stopped before it finished. Resume the task, or create a new task.",
    );
    assert.equal(
        agent_job_status_sentence("interrupted", false),
        "translated: This task stopped before it finished. Create a new task.",
    );
    assert.doesNotMatch(agent_job_status_sentence("interrupted", false), /[Rr]esume/);

    assert.equal(
        agent_job_status_sentence("queued", true),
        "translated: This task waits for a free runner.",
    );
    assert.match(
        agent_job_status_sentence("queued", true, {start_deadline: "2026-01-01T10:00:00Z"}),
        /This task waits for the agent's device\. If it does not start by .+, it stops waiting\./,
    );

    assert.equal(
        agent_job_status_sentence("completed", true, {job_kind: "code", delivery_target: "patch"}),
        "translated: Done. The diff is ready for review.",
    );
    assert.equal(
        agent_job_status_sentence("completed", true, {
            job_kind: "code",
            delivery_target: "draft_pr",
        }),
        "translated: Done. The draft pull request is ready for review.",
    );
    assert.equal(
        agent_job_status_sentence("completed", true, {job_kind: "answer"}),
        "translated: This task finished. Read the artifacts and diff above.",
    );
});

// Contract 12.3: every activity event type maps to its plain-language label,
// and an unrecognized type keeps the safe fallback instead of leaking it.
run_test("contract 12.3: every activity label", () => {
    const labels = {
        "job.queued": "Task queued",
        "attempt.starting": "Preparing the task",
        "workspace.prepared": "Code checked out",
        "attempt.started": "Agent started",
        "input.received": "Your input was saved",
        "input.applied": "The agent used your input",
        "input.delivery_uncertain": "Input delivery is not confirmed",
        "input.requested": "The agent asked for your input",
        "tool.started": "Step started",
        "tool.finished": "Step finished",
        "verification.finished": "Check finished",
        "approval.requested": "Approval requested",
        "approval.resolved": "Approval decided",
        "team.executed": "Team action done",
        "attempt.stop_requested": "Stop requested",
        "attempt.stopped": "Agent stopped",
        "attempt.interrupted": "Agent interrupted",
        "result.prepared": "Result ready for checks",
        "result.published": "Result posted",
        "publication.blocked": "Result not posted",
        "job.completed": "Task finished",
    };
    for (const [event_type, label] of Object.entries(labels)) {
        assert.equal(agent_activity_label(event_type), `translated: ${label}`);
    }
    assert.equal(agent_activity_label("some.unknown.event"), "translated: Other activity");
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
