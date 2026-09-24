"use strict";

const assert = require("node:assert/strict");

const {clock, mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let next_response;
let last_call;
let get_calls = 0;
let get_failures = [];
mock_esm("../src/channel", {
    get(options) {
        last_call = {method: "GET", ...options};
        get_calls += 1;
        if (get_failures.length > 0) {
            return Promise.reject(get_failures.shift());
        }
        return Promise.resolve(next_response);
    },
    post(options) {
        last_call = {method: "POST", ...options};
        return Promise.resolve(next_response);
    },
    patch(options) {
        last_call = {method: "PATCH", ...options};
        return Promise.resolve(next_response);
    },
});
const api = zrequire("agent_api");

run_test("shared provider profile payload uses a versioned network snapshot", () => {
    const provider = {id: "provider", owner_id: 1, config_version: 3};
    const hidden_profile = {
        provider_id: "provider",
        configuration: {network_retained: true, policy: {network: null}},
    };
    const fallback = {targets: []};
    assert.deepEqual(api.profile_network_choice("provider", provider, undefined, 2, fallback), {
        provider_network_version: 3,
    });
    assert.deepEqual(
        api.profile_network_choice("provider", provider, hidden_profile, 2, fallback),
        {
            retain_network: true,
        },
    );
    assert.deepEqual(
        api.profile_network_choice(
            "provider",
            provider,
            {
                ...hidden_profile,
                provider_id: "old-provider",
            },
            2,
            fallback,
        ),
        {provider_network_version: 3},
    );
    assert.deepEqual(api.profile_network_choice("", undefined, hidden_profile, 2, fallback), {
        network: fallback,
    });
});

run_test("directory requests one bounded filtered page", async () => {
    next_response = {schema_version: 1, count: 0, profiles: []};
    const result = await api.list_profiles({
        offset: 20,
        limit: 20,
        ownership: "shared",
        access: "complete",
        host_kind: "server",
        search: "builder",
    });
    assert.equal(result.count, 0);
    assert.equal(last_call.method, "GET");
    assert.match(last_call.url, /offset=20/);
    assert.match(last_call.url, /ownership=shared/);
    assert.match(last_call.url, /host_kind=server/);
    assert.match(last_call.url, /search=builder/);
});

run_test("malformed directory row cannot reach renderer", async () => {
    next_response = {schema_version: 1, count: 1, profiles: [{id: "profile", name: "unsafe"}]};
    await assert.rejects(api.list_profiles(), /Invalid input|expected/i);
});

run_test("profile detail accepts a safe named channel attachment", async () => {
    const profile = {
        id: "profile",
        name: "Agent",
        description: "",
        runner_id: null,
        provider_id: null,
        repository_id: null,
        state: "draft",
        desired_state: "draft",
        readiness_state: "unchecked",
        revision: 1,
        metadata_revision: 1,
        bot_user_id: 12,
        default_mode: "answer",
        capabilities: {},
        owner_id: 1,
        mode: "acp",
        adapter_id: "grow",
        adapter_version: "1",
        enabled_revision: null,
        readiness_revision: null,
        allowed_actions: ["edit"],
        owner: {id: 1, name: "Owner"},
        runner: null,
        provider: null,
        repository: null,
        access: {complete: true, runner: true, provider: true, repository: true},
        configuration: null,
    };
    next_response = {
        schema_version: 1,
        profile,
        setup: null,
        attachments: [{stream_id: 42, name: "Denmark", bot_member: true}],
    };
    const result = await api.get_profile("profile");
    assert.deepEqual(result.attachments, [{stream_id: 42, name: "Denmark", bot_member: true}]);
    next_response.attachments = [{stream_id: 42, name: 42, bot_member: true}];
    await assert.rejects(api.get_profile("profile"), /Invalid input|expected/i);
});

run_test("selection sends versioned form payload and preserves explicit identity", async () => {
    next_response = {
        schema_version: 1,
        selection: {
            selection_source: "explicit",
            profile_id: "chosen",
            profile_revision: 3,
            selection_revision: null,
            selection_state: "explicit",
            eligible: true,
            queue_permitted: false,
            reason: "runner_offline",
        },
    };
    const result = await api.resolve_selection({
        source_message_id: 41,
        job_kind: "answer",
        explicit_profile_id: "chosen",
        selection_state: "explicit",
    });
    assert.equal(result.profile_id, "chosen");
    assert.equal(result.queue_permitted, false);
    assert.deepEqual(JSON.parse(last_call.data.payload), {
        schema_version: 1,
        source_message_id: 41,
        job_kind: "answer",
        explicit_profile_id: "chosen",
        selection_state: "explicit",
    });
});

run_test("a Coding selection carries the resolved repository and base ref", async () => {
    next_response = {
        schema_version: 1,
        selection: {
            selection_source: "explicit",
            profile_id: "chosen",
            profile_revision: 3,
            selection_revision: null,
            selection_state: "explicit",
            eligible: true,
            queue_permitted: true,
            reason: "available",
            repository: {id: "repo-1", alias: "app", base_ref: "main"},
        },
    };
    const result = await api.resolve_selection({
        source_message_id: 41,
        job_kind: "code",
        explicit_profile_id: "chosen",
        selection_state: "explicit",
    });
    assert.deepEqual(result.repository, {id: "repo-1", alias: "app", base_ref: "main"});
});

run_test("sharing a profile sends exactly one principal and both toggles", async () => {
    next_response = {
        schema_version: 1,
        grants: [],
        skipped: [{target_kind: "runner", reason: "not_owner"}],
    };
    const result = await api.share_profile("profile", {
        principal_user_id: 7,
        allow_job_control: true,
        allow_job_review: false,
    });
    assert.deepEqual(JSON.parse(last_call.data.payload), {
        schema_version: 1,
        principal_user_id: 7,
        allow_job_control: true,
        allow_job_review: false,
    });
    assert.deepEqual(result.skipped, [{target_kind: "runner", reason: "not_owner"}]);
    next_response = {schema_version: 1};
    await api.unshare_profile("profile", {principal_group_id: 3});
    assert.equal(last_call.url, "/json/agent/profiles/profile/unshare");
});

run_test("job evidence requires attempt identity", async () => {
    next_response = {
        schema_version: 1,
        job: {
            id: "job",
            profile_id: "profile",
            requester_id: 1,
            source_message_id: null,
            status: "completed",
            phase: "deliver",
            version: 2,
            request: "task",
            job_kind: "answer",
            delivery_target: "answer",
            blocked_reason: null,
            result: null,
            allowed_actions: [],
        },
        attempts: [],
        operations: [],
        artifacts: [
            {
                id: "artifact",
                kind: "diff",
                filename: "safe.patch",
                size: 1,
                checksum: "a",
                media_type: "text/plain",
            },
        ],
        required_checks: [],
        operations_cursor: {offset: 0, next_offset: 0, truncated: false},
        artifacts_cursor: {offset: 0, next_offset: 1, truncated: false},
    };
    await assert.rejects(api.get_job("job"), /Invalid input|expected/i);
    next_response.artifacts[0].attempt_id = "attempt";
    const result = await api.get_job("job");
    assert.equal(result.artifacts[0].attempt_id, "attempt");
});

run_test("one message keeps separate target receipts", async () => {
    next_response = {
        schema_version: 1,
        source_message_id: 81,
        dispatch_receipts: [
            {
                profile_id: "a",
                decision: "accepted",
                reason: "",
                job_id: "job-a",
                job_status: "queued",
            },
            {
                profile_id: "b",
                decision: "rejected",
                reason: "access_denied",
                job_id: null,
                job_status: null,
            },
        ],
    };
    const result = await api.message_dispatch(81);
    assert.equal(result.dispatch_receipts.length, 2);
    assert.equal(result.dispatch_receipts[0].job_id, "job-a");
    assert.equal(result.dispatch_receipts[1].decision, "rejected");
    assert.equal(last_call.url, "/json/agent/messages/81/dispatch");
});

run_test("lost acknowledgement resolves the original send key and tombstone", async () => {
    next_response = {schema_version: 1, source_message_id: 81, deleted: false};
    assert.equal((await api.recover_send_intent("stable-key")).source_message_id, 81);
    assert.equal(last_call.url, "/json/agent/send-intents/stable-key");
    next_response.deleted = true;
    assert.equal((await api.recover_send_intent("stable-key")).deleted, true);
});

run_test("team instructions are read and saved with an expected revision", async () => {
    next_response = {
        schema_version: 1,
        team_instructions: {text: "Reply in English.", revision: 3, allowed_actions: ["edit"]},
    };
    const read = await api.get_team_instructions();
    assert.equal(last_call.method, "GET");
    assert.equal(read.team_instructions.revision, 3);
    next_response = {
        schema_version: 1,
        team_instructions: {text: "Reply in English.", revision: 4, allowed_actions: ["edit"]},
    };
    const saved = await api.update_team_instructions({
        expected_revision: 3,
        text: "Reply in English.",
    });
    assert.equal(last_call.method, "PATCH");
    assert.deepEqual(JSON.parse(last_call.data.payload), {
        schema_version: 1,
        expected_revision: 3,
        text: "Reply in English.",
    });
    assert.equal(saved.team_instructions.revision, 4);
});

run_test("a pairing preview never approves the pairing", async () => {
    next_response = {
        schema_version: 1,
        pairing: {
            device_name: "Laptop",
            fingerprint_prefix: "abcd1234abcd1234",
            realm_name: "Acme",
            expires_at: "2026-01-01T00:00:00Z",
        },
    };
    const result = await api.preview_pairing("pairing-1", "123456");
    assert.equal(last_call.url, "/json/agent/pairings/preview");
    assert.deepEqual(JSON.parse(last_call.data.payload), {
        schema_version: 1,
        pairing_id: "pairing-1",
        user_code: "123456",
    });
    assert.equal(result.pairing.device_name, "Laptop");
});

run_test("a test task is sent with an idempotency key", async () => {
    next_response = {
        schema_version: 1,
        job: {
            id: "job",
            profile_id: "profile",
            requester_id: 1,
            source_message_id: null,
            status: "queued",
            phase: "run",
            version: 1,
            request: "Test task: reply with one short sentence.",
            job_kind: "answer",
            delivery_target: "answer",
            blocked_reason: null,
            result: null,
            allowed_actions: [],
        },
    };
    const result = await api.send_test_task("profile", {idempotency_key: "key-1"});
    assert.equal(last_call.url, "/json/agent/profiles/profile/test-task");
    assert.equal(result.job.id, "job");
});

run_test("the agent error code reads a rejected mutation's JSON body", () => {
    assert.equal(
        api.agent_error_code({responseJSON: {schema_version: 1, code: "instructions_rejected"}}),
        "instructions_rejected",
    );
    assert.equal(api.agent_error_code(new Error("network failure")), undefined);
    assert.equal(api.agent_error_code(undefined), undefined);
});

run_test("a busy server answer to a read waits and asks again", async () => {
    const busy = {status: 503, getResponseHeader: () => "0.001"};
    get_calls = 0;
    get_failures = [busy, busy];
    next_response = {schema_version: 1, count: 0, inputs: []};
    const request = api.get_job_inputs("job");
    // The test harness installs fake timers, so the retry wait needs the clock.
    await clock.runAllAsync();
    const result = await request;
    assert.equal(result.count, 0);
    assert.equal(get_calls, 3);

    get_calls = 0;
    get_failures = [{status: 400, getResponseHeader: () => null}];
    await assert.rejects(api.get_job_inputs("job"));
    assert.equal(get_calls, 1);
    get_failures = [];
});
