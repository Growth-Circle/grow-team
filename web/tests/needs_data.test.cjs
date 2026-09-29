"use strict";

const assert = require("node:assert/strict");

const {clock, mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let next_response;
let last_call;
mock_esm("../src/channel", {
    get(options) {
        last_call = {method: "GET", ...options};
        return Promise.resolve(next_response);
    },
    post(options) {
        last_call = {method: "POST", ...options};
        return Promise.resolve(next_response);
    },
});

const needs_data = zrequire("needs_data");

function make_item(overrides) {
    return {
        id: "approval:1",
        kind: "approval",
        actor: {
            type: "agent",
            id: 7,
            name: "Ramma",
            agent_role: "worker",
            shape: "circle",
            color: "#ff6a3d",
            initials: "",
        },
        stream_id: null,
        topic: null,
        time: "2026-09-27T00:00:00Z",
        text: "Deploy the release.",
        attachment: null,
        actions: ["approve"],
        expires_at: "2026-09-27T01:00:00Z",
        expired: false,
        resolved_at: null,
        resolved_action: null,
        job_id: "job-1",
        approval_version: 3,
        operation_hash: "hash-1",
        nonce: "nonce-1",
        job_version: null,
        ...overrides,
    };
}

run_test("a tab shows only its own kind, and all shows every kind", () => {
    const items = [
        make_item({id: "approval:1", kind: "approval"}),
        make_item({id: "mention:2", kind: "mention"}),
        make_item({id: "decision:3", kind: "decision"}),
    ];
    assert.deepEqual(
        needs_data.items_for_tab(items, "approval").map((item) => item.id),
        ["approval:1"],
    );
    assert.deepEqual(
        needs_data.items_for_tab(items, "mention").map((item) => item.id),
        ["mention:2"],
    );
    assert.equal(needs_data.items_for_tab(items, "all").length, 3);
});

run_test("each tab count matches the items that tab shows", () => {
    const items = [
        make_item({id: "approval:1", kind: "approval"}),
        make_item({id: "approval:2", kind: "approval"}),
        make_item({id: "mention:3", kind: "mention"}),
    ];
    assert.equal(needs_data.count_for_tab(items, "approval"), 2);
    assert.equal(needs_data.count_for_tab(items, "mention"), 1);
    assert.equal(needs_data.count_for_tab(items, "decision"), 0);
    assert.equal(needs_data.count_for_tab(items, "all"), 3);

    // The person handles one item: its tab and the "all" tab go down at once.
    items.splice(0, 1);
    assert.equal(needs_data.count_for_tab(items, "approval"), 1);
    assert.equal(needs_data.count_for_tab(items, "all"), 2);
});

run_test("a decision offers the choices the agent gave, and nothing else", () => {
    const with_choices = make_item({kind: "decision", actions: ["Version A", " ", "Version B"]});
    assert.deepEqual(needs_data.decision_options(with_choices), ["Version A", "Version B"]);

    // An open question has no choices. The person answers in the room.
    const open_question = make_item({kind: "decision", actions: []});
    assert.deepEqual(needs_data.decision_options(open_question), []);
});

run_test("the raw id of an item is what follows its kind", () => {
    assert.equal(needs_data.raw_id("mention:42"), "42");
    assert.equal(needs_data.raw_id("approval:6f1c-77"), "6f1c-77");
});

run_test("the open list is fetched from the needs endpoint", async () => {
    next_response = {
        items: [make_item()],
        counts: {approval: 1, mention: 0, decision: 0, all: 1},
    };
    const result = await needs_data.fetch_open();
    assert.equal(last_call.method, "GET");
    assert.equal(last_call.url, "/json/needs");
    assert.equal(result.items.length, 1);
    assert.equal(result.counts.all, 1);
});

run_test("today's done list asks for resolved items since today", async () => {
    next_response = {items: [], counts: {approval: 0, mention: 0, decision: 0, all: 0}};
    await needs_data.fetch_done_today();
    assert.equal(last_call.url, "/json/needs?status=resolved&since=today");
});

run_test("undoing before the delay cancels the action", async () => {
    let commits = 0;
    let settled;
    needs_data.schedule_pending(
        "approval:1",
        () => {
            commits += 1;
            return Promise.resolve();
        },
        (error) => {
            settled = error;
        },
    );
    assert.ok(needs_data.is_pending("approval:1"));
    assert.ok(needs_data.cancel_pending("approval:1"));
    await clock.runAllAsync();
    assert.equal(commits, 0);
    assert.equal(settled, undefined);
    assert.ok(!needs_data.is_pending("approval:1"));
    // Undoing twice is a no-op, not an error.
    assert.ok(!needs_data.cancel_pending("approval:1"));
});

run_test("undoing one item leaves the other pending items alone", async () => {
    const commits = [];
    needs_data.schedule_pending(
        "approval:10",
        () => {
            commits.push("approval:10");
            return Promise.resolve();
        },
        () => {},
    );
    needs_data.schedule_pending(
        "approval:11",
        () => {
            commits.push("approval:11");
            return Promise.resolve();
        },
        () => {},
    );
    assert.ok(needs_data.cancel_pending("approval:10"));
    assert.ok(needs_data.is_pending("approval:11"));
    await clock.tickAsync(needs_data.UNDO_DELAY_MS);
    assert.deepEqual(commits, ["approval:11"]);
});

run_test("scheduling an item again sends its action once", async () => {
    let commits = 0;
    const commit = () => {
        commits += 1;
        return Promise.resolve();
    };
    needs_data.schedule_pending("approval:12", commit, () => {});
    needs_data.schedule_pending("approval:12", commit, () => {});
    await clock.tickAsync(needs_data.UNDO_DELAY_MS * 2);
    assert.equal(commits, 1);
});

run_test("the action commits once the undo window passes", async () => {
    let commits = 0;
    let settled = "not yet called";
    needs_data.schedule_pending(
        "approval:2",
        () => {
            commits += 1;
            return Promise.resolve();
        },
        (error) => {
            settled = error;
        },
    );
    await clock.tickAsync(needs_data.UNDO_DELAY_MS);
    assert.equal(commits, 1);
    assert.equal(settled, undefined);
    assert.ok(!needs_data.is_pending("approval:2"));
});

run_test("a commit that fails reports its error instead of throwing", async () => {
    const failure = new Error("network down");
    let settled;
    needs_data.schedule_pending(
        "approval:3",
        () => Promise.reject(failure),
        (error) => {
            settled = error;
        },
    );
    await clock.tickAsync(needs_data.UNDO_DELAY_MS);
    assert.equal(settled, failure);
});

run_test("approving sends the operation hash and nonce for that approval", async () => {
    next_response = {schema_version: 1, approval_id: "1", decision: "approved", version: 4};
    await needs_data.commit_approve(make_item({id: "approval:1", approval_version: 3}));
    assert.equal(last_call.url, "/json/agent/approvals/1/decision");
    assert.deepEqual(JSON.parse(last_call.data.payload), {
        schema_version: 1,
        expected_version: 3,
        operation_hash: "hash-1",
        nonce: "nonce-1",
        decision: "approved",
    });
});

run_test("choosing a decision option answers that job", async () => {
    next_response = {schema_version: 1};
    await needs_data.commit_decision(
        make_item({id: "decision:9", kind: "decision", job_id: "9", job_version: 2}),
        "Pilih A",
    );
    assert.equal(last_call.url, "/json/agent/jobs/9/inputs");
    const payload = JSON.parse(last_call.data.payload);
    assert.equal(payload.expected_version, 2);
    assert.equal(payload.text, "Pilih A");
    assert.equal(payload.input_type, "answer");
    assert.equal(typeof payload.client_key, "string");
});

run_test("a decision item without a job id cannot be answered", async () => {
    await assert.rejects(
        async () =>
            needs_data.commit_decision(
                make_item({id: "decision:9", kind: "decision", job_id: null}),
                "Pilih A",
            ),
        /job id/,
    );
});

run_test("marking a mention done resolves that message", async () => {
    next_response = {};
    await needs_data.commit_mention_done(make_item({id: "mention:42", kind: "mention"}));
    assert.equal(last_call.method, "POST");
    assert.equal(last_call.url, "/json/needs/mentions/42/resolve");
});
