"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

mock_esm("../src/channel");
mock_esm("../src/browser_history", {go_to_location() {}});
mock_esm("../src/feedback_widget", {show_toast() {}});
mock_esm("../src/message_store");
mock_esm("../src/widgetize");

const agent_job_widget = zrequire("agent_job_widget");
const agent_job_labels = zrequire("agent_job_labels");
const submessage = zrequire("submessage");

const JOB_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

// In these tests $t marks every message with "translated: ".
function t(message) {
    return `translated: ${message}`;
}

function card(overrides = {}) {
    return {
        job_id: JOB_ID,
        status: "queued",
        title: "Screenshot mobile PR #214",
        step_label: "tool.started",
        progress: 0.05,
        artifacts: [],
        reason_code: null,
        can_retry: false,
        ...overrides,
    };
}

// RM-37, RM-41, RM-44: the pill, the bar, and the footer text of each look.
run_test("the pill, the bar, and the step text follow the status", () => {
    const cases = [
        [
            {status: "queued"},
            {
                bucket: "queued",
                pill_text: t("QUEUED"),
                show_bar: true,
                bar_percent: 5,
                step_text: t("Waiting in line"),
            },
        ],
        [
            {status: "running", progress: 0.55},
            {
                bucket: "working",
                pill_text: t("WORKING"),
                show_bar: true,
                bar_percent: 55,
                step_text: t("Step started"),
            },
        ],
        [
            {status: "verifying", progress: 0.75},
            {bucket: "working", pill_text: t("WORKING"), show_bar: true, bar_percent: 75},
        ],
        [
            {status: "waiting_for_approval", progress: 0.9},
            {
                bucket: "review",
                pill_text: t("WAITING FOR APPROVAL"),
                show_bar: false,
                step_text: t("Waiting for approval"),
            },
        ],
        [
            {status: "waiting_for_input", progress: 0.3},
            {
                bucket: "review",
                pill_text: t("WAITING FOR A DECISION"),
                show_bar: false,
                step_text: t("Waiting for a decision"),
            },
        ],
        [
            {status: "completed", progress: 1},
            {bucket: "done", pill_text: t("DONE"), show_bar: false, step_text: t("Finished")},
        ],
        [
            {status: "failed", reason_code: "tool_error"},
            {
                bucket: "stopped",
                pill_text: t("STOPPED"),
                show_bar: false,
                step_text: t("Stopped because a step failed."),
            },
        ],
    ];
    for (const [overrides, expected] of cases) {
        const data = agent_job_widget.build_template_data(card(overrides));
        for (const [key, value] of Object.entries(expected)) {
            assert.equal(data[key], value, `${overrides.status} ${key}`);
        }
        assert.equal(data.job_id, JOB_ID);
        assert.equal(data.title, "Screenshot mobile PR #214");
    }
});

// The bar never leaves 0..100, even for a bad number.
run_test("the bar width stays inside the bar", () => {
    assert.equal(agent_job_widget.build_template_data(card({progress: 1.7})).bar_percent, 100);
    assert.equal(agent_job_widget.build_template_data(card({progress: -1})).bar_percent, 0);
});

// PLAN.md 3.5 and P-28: the reason names no device and no internal code.
run_test("a stopped job gives its reason in plain words", () => {
    const expected = new Map([
        ["runner_offline", t("Stopped because the owner's device is offline.")],
        ["budget_exceeded", t("Stopped because the budget ran out.")],
        ["tool_error", t("Stopped because a step failed.")],
        ["timeout", t("Stopped because no one approved in time.")],
        ["grant_revoked", t("Stopped because access to the agent changed.")],
        ["verification_failed", t("Stopped because a required check failed.")],
        ["approval_rejected", t("Stopped because the action was rejected.")],
        ["profile_needs_action", t("Stopped because the agent needs a fix first.")],
        ["profile_paused", t("Stopped because the agent is paused.")],
        ["publication_blocked", t("Stopped. The result is saved but not posted here.")],
        ["audience_changed", t("Stopped because the people in this chat changed.")],
        ["some_new_code", t("Stopped.")],
        [null, t("Stopped.")],
    ]);
    for (const [reason_code, text] of expected) {
        const data = agent_job_widget.build_template_data(
            card({status: "interrupted", reason_code}),
        );
        assert.equal(data.step_text, text);
        assert.doesNotMatch(String(data.step_text).replace("translated: ", ""), /_|LAPTOP|runner/i);
    }
    // A job that a person paused has no code. The card sends the reader
    // to the details, where "Resume job" is.
    assert.equal(
        agent_job_labels.stopped_reason_text(null, "cancelled"),
        t("Paused. Open the details to continue."),
    );
});

// Fix for the message "the device is offline": the queued job waits.
run_test("a queued job with an offline device says so", () => {
    const data = agent_job_widget.build_template_data(
        card({status: "queued", reason_code: "runner_offline"}),
    );
    assert.equal(data.bucket, "queued");
    assert.equal(
        data.step_text,
        t("The owner's device is offline. The job continues when the device turns on."),
    );
});

// RM-47: "Try again" shows only for a stopped job that can start again.
run_test("the retry button needs a stopped job that can retry", () => {
    const show = (overrides) => agent_job_widget.build_template_data(card(overrides)).show_retry;
    assert.equal(show({status: "failed", can_retry: true}), true);
    assert.equal(show({status: "failed", can_retry: false}), false);
    assert.equal(show({status: "completed", can_retry: true}), false);
    assert.equal(show({status: "running", can_retry: true}), false);
});

// RM-46: a chip opens a file, a pull request, or a task. Only a done or
// waiting job shows chips.
run_test("chips open a file, a page, or a task", () => {
    const artifacts = [
        {kind: "pr", label: "PR #214", url: "https://github.com/x/y/pull/214", task_id: null},
        {kind: "file", label: "notes.md", url: "/json/agent/artifacts/abc", task_id: null},
        {kind: "task", label: "Copy harga", url: null, task_id: 7},
        {kind: "check", label: "tests ✓", url: "/json/agent/artifacts/def", task_id: null},
        {kind: "draft", label: "Draft ready", url: null, task_id: null},
        // eslint-disable-next-line no-script-url
        {kind: "file", label: "unsafe", url: "javascript:alert(1)", task_id: null},
    ];
    const data = agent_job_widget.build_template_data(card({status: "completed", artifacts}));
    assert.deepEqual(data.chips, [
        {
            label: "PR #214",
            href: "https://github.com/x/y/pull/214",
            external: true,
            tint: "ayame",
        },
        {label: "notes.md", href: "/json/agent/artifacts/abc", external: false, tint: "ayame"},
        {label: "Copy harga", href: "#tasks/7", external: false, tint: "kuning"},
        {label: "tests ✓", href: "/json/agent/artifacts/def", external: false, tint: "matcha"},
        {label: "Draft ready", href: undefined, external: false, tint: "kuning"},
        {label: "unsafe", href: undefined, external: false, tint: "ayame"},
    ]);
    for (const status of ["queued", "running", "failed"]) {
        assert.deepEqual(agent_job_widget.build_template_data(card({status, artifacts})).chips, []);
    }
    assert.equal(
        agent_job_widget.build_template_data(card({status: "waiting_for_approval", artifacts}))
            .chips.length,
        6,
    );
});

// 13-A1: each later submessage is a full snapshot that replaces the card.
run_test("a later snapshot replaces the card", () => {
    const {inbound_events_handler, widget_data} = agent_job_widget.activate({
        message: {},
        any_data: {widget_type: "agent_job", extra_data: card()},
    });
    assert.equal(widget_data.widget_type, "agent_job");
    assert.equal(widget_data.data.status, "queued");

    inbound_events_handler([
        {sender_id: 5, data: card({status: "running", progress: 0.3})},
        // A card of another job, and data that is not a card, change nothing.
        {sender_id: 5, data: card({job_id: "other", status: "failed"})},
        {sender_id: 5, data: {status: "failed"}},
    ]);
    assert.equal(widget_data.data.status, "running");
    assert.equal(widget_data.data.progress, 0.3);

    inbound_events_handler([{sender_id: 5, data: card({status: "completed", progress: 1})}]);
    assert.equal(widget_data.data.status, "completed");
});

// The bot writes the message text "{name} · {state}" for clients that
// draw no card. A real answer stays.
run_test("the fallback line is hidden and an answer is not", () => {
    assert.equal(agent_job_widget.is_fallback_line("Ayame · Queued", "Ayame"), true);
    assert.equal(agent_job_widget.is_fallback_line("Ayame · Antre\n", "Ayame"), true);
    assert.equal(
        agent_job_widget.is_fallback_line("Hero and three sections are ready.", "Ayame"),
        false,
    );
    assert.equal(agent_job_widget.is_fallback_line("Ayame · Done\nMore text", "Ayame"), false);
    assert.equal(agent_job_widget.is_fallback_line("Kaki · Done", "Ayame"), false);
});

// A plain chat answer shows its reply alone, unless it has a problem.
run_test("the card of a plain answer is hidden unless it has a problem", () => {
    const hides = (overrides) => agent_job_widget.hides_card(card(overrides));
    for (const status of ["queued", "running", "completed"]) {
        assert.equal(hides({kind: "answer", status}), true, status);
        assert.equal(hides({kind: "code", status}), false, status);
    }
    assert.equal(hides({status: "running"}), false);
    for (const status of ["failed", "stopped", "blocked", "cancelled", "interrupted"]) {
        assert.equal(hides({kind: "answer", status}), false, status);
    }
});

// A message with a card and later snapshots must still load. Those
// snapshots have no "type" field, and a reload drew no card without this.
run_test("submessages of a card with later snapshots parse", () => {
    const message = {
        sender_id: 9,
        submessages: [
            {
                id: 1,
                sender_id: 9,
                content: JSON.stringify({widget_type: "agent_job", extra_data: card()}),
            },
            {id: 2, sender_id: 9, content: JSON.stringify(card({status: "running"}))},
            {id: 3, sender_id: 9, content: JSON.stringify(card({status: "completed"}))},
        ],
    };
    const events = submessage.get_message_events(message);
    assert.ok(events, "the events parse");
    assert.equal(events.length, 3);
    assert.equal(events[0].data.widget_type, "agent_job");
    assert.equal(events[2].data.status, "completed");
});

// DR-58..61: the step from the progress. 64% is step 2, 82% is step 3,
// 35% is step 1, and 8% is step 0. A finished job has passed all five.
run_test("the active step follows the progress", () => {
    const step = agent_job_labels.drawer_step_index;
    assert.equal(step(0.64, "running"), 2);
    assert.equal(step(0.82, "verifying"), 3);
    assert.equal(step(0.35, "running"), 1);
    assert.equal(step(0.08, "queued"), 0);
    assert.equal(step(0.95, "running"), 4);
    assert.equal(step(1, "completed"), 5);
    assert.deepEqual(
        agent_job_labels.drawer_step_labels(),
        [
            "Reading the brief and the room",
            "Reading the related Drive files",
            "Doing the work",
            "Send to Matcha for a check",
            "Ask a person to approve",
        ].map((label) => t(label)),
    );
});

// The drawer has no card data from the list, so it derives the progress
// from the phase, with the same table as the server.
run_test("progress comes from the status and the phase", () => {
    const progress = agent_job_labels.job_progress;
    assert.equal(progress("queued", "inspect"), 0.05);
    assert.equal(progress("draft", ""), 0.05);
    assert.equal(progress("completed", "deliver"), 1);
    assert.equal(progress("running", "inspect"), 0.15);
    assert.equal(progress("running", "plan"), 0.3);
    assert.equal(progress("running", "edit"), 0.55);
    assert.equal(progress("verifying", "verify"), 0.75);
    assert.equal(progress("waiting_for_approval", "review"), 0.9);
    assert.equal(progress("running", "deliver"), 0.95);
    assert.equal(progress("running", "unknown phase"), 0.15);
});
