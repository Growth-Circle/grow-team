"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const handlebars = require("handlebars");
const {JSDOM} = require("jsdom");
const ts = require("typescript");

// job_panel.hbs now carries {{t}} translation tags. This test compiles
// the template directly with the plain handlebars package, so it needs
// the same stand-in helper the sibling regression file registers.
handlebars.registerHelper("t", (item) => item);

// A stand-in for the real $t(): it substitutes each {placeholder} with
// its value, so a rendered sentence can be matched in full.
function format_t(descriptor, values) {
    let text = descriptor.defaultMessage;
    if (values) {
        for (const [key, value] of Object.entries(values)) {
            text = text.replaceAll(`{${key}}`, String(value));
        }
    }
    return text;
}

async function main() {
    const dom = new JSDOM("<body></body>", {url: "https://realm.test"});
    global.window = dom.window;
    global.document = dom.window.document;
    const $ = require("jquery");
    let timer;
    let version = 1;
    let approved = false;
    let permitted = true;
    const requests = [];
    const job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const attempt_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const approval_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const operation = (index) => ({
        operation_id: `operation-${index}`,
        operation_hash: `hash-${index}`,
        version: 1,
        status: approved ? "approved" : "proposed",
        attempt_id,
        action: "git.push",
        approval_id: index === 0 ? approval_id : null,
        approval_version: 1,
        nonce: "nonce",
        can_decide: index === 0 && !approved && permitted,
        approval_decision: approved ? "approved" : "pending",
    });
    const api = {
        async get_job(_id, offset) {
            requests.push(offset);
            return {
                job: {
                    id: job_id,
                    version,
                    status: "running",
                    phase: "editing",
                    job_kind: "coding",
                    request: "Example",
                    allowed_actions: [],
                },
                attempts: [
                    {
                        id: attempt_id,
                        number: 1,
                        active: true,
                        process_state: "active",
                        tree_hash: "tree",
                        base_commit: "base",
                    },
                ],
                required_checks: [],
                operations:
                    offset === 0
                        ? Array.from({length: 100}, (_, index) => operation(index))
                        : [operation(100)],
                artifacts: [],
                operations_cursor: {
                    offset,
                    next_offset: offset === 0 ? 100 : 101,
                    truncated: offset === 0,
                },
                artifacts_cursor: {offset: 0, next_offset: 0, truncated: false},
            };
        },
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        job_action: async () => ({}),
    };
    const transpile = (source) =>
        ts.transpileModule(source, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                esModuleInterop: true,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText;
    const state = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_ui_state.ts"), "utf8")),
        {
            exports: state,
            crypto: require("node:crypto").webcrypto,
            require(name) {
                if (name === "./i18n.ts") {
                    return {$t: format_t};
                }
                throw new Error(name);
            },
        },
    );
    const out = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_job_panel.ts"), "utf8")),
        {
            exports: out,
            require(name) {
                if (name === "jquery") {
                    return $;
                }
                if (name === "./agent_api.ts") {
                    return api;
                }
                if (name === "./agent_ui_state.ts") {
                    return state;
                }
                if (name === "./state_data.ts") {
                    return {current_user: {user_id: 1}};
                }
                if (name === "./people.ts") {
                    return {maybe_get_user_by_id: () => ({full_name: "Requester"})};
                }
                if (name === "./overlays.ts") {
                    return {open_overlay: ({$overlay}) => $overlay.addClass("show")};
                }
                if (name === "./browser_history.ts") {
                    return {exit_overlay() {}};
                }
                if (name === "./agent_task_composer.ts") {
                    return {open_for_followup() {}};
                }
                if (name === "./i18n.ts") {
                    return {$t: format_t};
                }
                if (name.endsWith(".hbs")) {
                    return () =>
                        handlebars.compile(
                            fs.readFileSync(
                                path.join(__dirname, "../templates/agent/job_panel.hbs"),
                                "utf8",
                            ),
                        )({});
                }
                throw new Error(name);
            },
            window: dom.window,
            document: dom.window.document,
            setTimeout(fn) {
                timer = fn;
                return 1;
            },
            clearTimeout() {
                timer = undefined;
            },
            console,
        },
    );
    const flush = async () => {
        for (let index = 0; index < 25; index += 1) {
            await Promise.resolve();
        }
    };
    try {
        out.open(job_id);
        await flush();
        assert.equal($("[data-job-action='approve']").length, 1);
        $("#agent-job-more-operations").trigger("click");
        await flush();
        assert.deepEqual(requests, [0, 100]);
        assert.equal($("[data-job-action='approve']").length, 0);
        approved = true;
        version = 2;
        timer();
        await flush();
        assert.deepEqual(requests, [0, 100, 100]);
        assert.equal($("[data-job-action='approve']").length, 0);
        assert.doesNotMatch($("#agent-job-operations").text(), /Approval: pending/);
        $("#agent-job-first-operations").trigger("click");
        await flush();
        assert.equal(requests.at(-1), 0);
        assert.match($("#agent-job-operations").text(), /Approval: approved/);
        approved = false;
        permitted = true;
        version = 3;
        timer();
        await flush();
        assert.equal($("[data-job-action='approve']").length, 1);
        $("#agent-job-more-operations").trigger("click");
        await flush();
        permitted = false;
        timer();
        await flush();
        assert.equal($("[data-job-action='approve']").length, 0);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// Builds a fresh jsdom + vm harness for the real agent_job_panel module,
// isolated from main()'s. The real "jquery" package binds to whatever
// global.document exists the moment it is first required and then caches
// that binding, so each harness must bust that cache before requiring it
// again for a new jsdom window.
function build_input_retention_harness(api, composer = {}) {
    Reflect.deleteProperty(require.cache, require.resolve("jquery"));
    const dom = new JSDOM("<body></body>", {url: "https://realm.test"});
    global.window = dom.window;
    global.document = dom.window.document;
    const $ = require("jquery");
    const transpile = (source) =>
        ts.transpileModule(source, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                esModuleInterop: true,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText;
    const state = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_ui_state.ts"), "utf8")),
        {
            exports: state,
            crypto: require("node:crypto").webcrypto,
            require(name) {
                if (name === "./i18n.ts") {
                    return {$t: format_t};
                }
                throw new Error(name);
            },
        },
    );
    const out = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_job_panel.ts"), "utf8")),
        {
            exports: out,
            require(name) {
                if (name === "jquery") {
                    return $;
                }
                if (name === "./agent_api.ts") {
                    return api;
                }
                if (name === "./agent_ui_state.ts") {
                    return state;
                }
                if (name === "./state_data.ts") {
                    return {current_user: {user_id: 1}};
                }
                if (name === "./people.ts") {
                    return {maybe_get_user_by_id: () => ({full_name: "Requester"})};
                }
                if (name === "./overlays.ts") {
                    return {open_overlay: ({$overlay}) => $overlay.addClass("show")};
                }
                if (name === "./browser_history.ts") {
                    return {exit_overlay() {}};
                }
                if (name === "./agent_task_composer.ts") {
                    return {open_for_followup: composer.open_for_followup ?? (() => {})};
                }
                if (name === "./i18n.ts") {
                    return {$t: format_t};
                }
                if (name.endsWith(".hbs")) {
                    return () =>
                        handlebars.compile(
                            fs.readFileSync(
                                path.join(__dirname, "../templates/agent/job_panel.hbs"),
                                "utf8",
                            ),
                        )({});
                }
                throw new Error(name);
            },
            window: dom.window,
            document: dom.window.document,
            // This harness never fires the poll timer manually, unlike
            // main()'s, so it only needs to satisfy the panel's calls.
            setTimeout() {
                return 1;
            },
            clearTimeout() {},
            console,
        },
    );
    const flush = async () => {
        for (let index = 0; index < 25; index += 1) {
            await Promise.resolve();
        }
    };
    return {dom, $, out, flush};
}

function queued_job_detail(id) {
    return {
        job: {
            id,
            version: 1,
            status: "queued",
            phase: "editing",
            job_kind: "coding",
            request: "Example",
            allowed_actions: ["input"],
        },
        attempts: [],
        required_checks: [],
        operations: [],
        artifacts: [],
        operations_cursor: {offset: 0, next_offset: 0, truncated: false},
        artifacts_cursor: {offset: 0, next_offset: 0, truncated: false},
    };
}

async function retains_unresolved_input_across_visit() {
    const job_a = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const job_b = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
    const submitted_keys = [];
    const api = {
        get_job: async (id) => queued_job_detail(id),
        get_job_events: async () => ({events: []}),
        // An empty input list leaves the submitted intent unreconciled, so
        // the panel must keep treating it as unresolved.
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        async job_action(_id, _action, payload) {
            submitted_keys.push(payload.client_key);
            throw new Error("Input delivery is unreachable in this test.");
        },
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(job_a);
        await flush();
        $("#agent-job-input").val("hello");
        $("#agent-job-input-form").trigger("submit");
        await flush();
        assert.equal(submitted_keys.length, 1);

        out.change_target(job_b);
        await flush();
        assert.equal($("#agent-job-input").val(), "");

        out.change_target(job_a);
        await flush();
        assert.equal($("#agent-job-input").val(), "hello");

        $("#agent-job-input-form").trigger("submit");
        await flush();
        assert.equal(submitted_keys.length, 2);
        assert.equal(submitted_keys[1], submitted_keys[0]);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function accepted_input_does_not_return_on_next_visit() {
    const job_a = "ffffffff-ffff-4fff-8fff-ffffffffffff";
    const job_b = "11111111-1111-4111-8111-111111111111";
    const submitted_keys = [];
    const api = {
        get_job: async (id) => queued_job_detail(id),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        async job_action(_id, _action, payload) {
            submitted_keys.push(payload.client_key);
            return {};
        },
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(job_a);
        await flush();
        $("#agent-job-input").val("hello");
        $("#agent-job-input-form").trigger("submit");
        await flush();
        assert.equal(submitted_keys.length, 1);
        assert.equal($("#agent-job-input").val(), "");

        out.change_target(job_b);
        await flush();
        out.change_target(job_a);
        await flush();
        assert.equal($("#agent-job-input").val(), "");

        $("#agent-job-input").val("hello");
        $("#agent-job-input-form").trigger("submit");
        await flush();
        assert.equal(submitted_keys.length, 2);
        assert.notEqual(submitted_keys[1], submitted_keys[0]);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function shows_reason_sentence_and_gates_resume() {
    const interrupted_id = "22222222-2222-4222-8222-222222222222";
    const resumable_id = "33333333-3333-4333-8333-333333333333";
    const detail = (id, overrides) => ({
        job: {
            id,
            version: 1,
            status: "interrupted",
            phase: "editing",
            job_kind: "answer",
            request: "Example",
            reason_code: "stop_unconfirmed",
            resume_available: false,
            resume_unavailable_reason: "runner_offline",
            allowed_actions: ["resume"],
            ...overrides,
        },
        attempts: [],
        required_checks: [],
        operations: [],
        artifacts: [],
        operations_cursor: {offset: 0, next_offset: 0, truncated: false},
        artifacts_cursor: {offset: 0, next_offset: 0, truncated: false},
    });
    const api = {
        get_job: async (id) => detail(id, id === resumable_id ? {resume_available: true} : {}),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        job_action: async () => ({}),
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(interrupted_id);
        await flush();
        // job_kind "answer" shows the note that this agent only answers.
        assert.equal($("#agent-job-answer-note").prop("hidden"), false);
        // The reason_code sentence replaces the generic interrupted text.
        assert.match($("#agent-job-status").text(), /has not confirmed it stopped/);
        // resume_available false hides the button and explains why.
        assert.equal($("[data-job-action='resume']").length, 0);
        assert.match($("#agent-job-controls").text(), /device for this task is offline/);

        out.change_target(resumable_id);
        await flush();
        // resume_available true shows the button again for a fresh job.
        assert.equal($("[data-job-action='resume']").length, 1);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function shows_team_manage_operation_card() {
    const decidable_id = "44444444-4444-4444-8444-444444444444";
    const waiting_id = "55555555-5555-4555-8555-555555555555";
    const uncertain_id = "66666666-6666-4666-8666-666666666666";
    const summary = "Create private channel launch-q4 and subscribe Budi.";
    const operation = (overrides) => ({
        operation_id: "operation-1",
        operation_hash: "0123456789abcdef",
        version: 1,
        status: "proposed",
        attempt_id: "attempt-1",
        action: "team.manage",
        approval_id: "approval-1",
        approval_version: 1,
        nonce: "nonce-1",
        can_decide: false,
        approval_decision: "pending",
        summary,
        ...overrides,
    });
    const detail = (id, op) => ({
        job: {
            id,
            version: 1,
            status: "waiting_for_approval",
            phase: "editing",
            job_kind: "manage",
            request: "Set up the launch channel",
            requester_id: 9,
            allowed_actions: [],
        },
        attempts: [{id: "attempt-1", number: 1, active: true, process_state: "active"}],
        required_checks: [],
        operations: [op],
        artifacts: [],
        operations_cursor: {offset: 0, next_offset: 0, truncated: false},
        artifacts_cursor: {offset: 0, next_offset: 0, truncated: false},
    });
    const api = {
        async get_job(id) {
            if (id === decidable_id) {
                return detail(id, operation({can_decide: true}));
            }
            if (id === waiting_id) {
                return detail(id, operation({can_decide: false}));
            }
            return detail(
                id,
                operation({
                    status: "started",
                    approval_id: null,
                    approval_decision: undefined,
                    summary: undefined,
                }),
            );
        },
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        job_action: async () => ({}),
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(decidable_id);
        await flush();
        // A manage job answers by taking team actions, so it never shows
        // the "this agent only answers" note, and its task type reads
        // "Team management" rather than falling back to "Answer".
        assert.equal($("#agent-job-answer-note").prop("hidden"), true);
        assert.match($("#agent-job-summary").text(), /Team management/);
        const card_text = $("#agent-job-operations").text();
        assert.ok(card_text.includes(summary));
        // The card never shows the raw action ID or the operation hash.
        assert.ok(!card_text.includes("team.manage"));
        assert.ok(!card_text.includes("0123456789abcdef"));
        assert.equal($("[data-job-action='approve']").length, 1);
        assert.equal($("[data-job-action='reject']").length, 1);

        out.change_target(waiting_id);
        await flush();
        assert.equal($("[data-job-action='approve']").length, 0);
        assert.match($("#agent-job-operations").text(), /Waiting for Requester to approve\./);

        out.change_target(uncertain_id);
        await flush();
        assert.equal($("[data-job-action='approve']").length, 0);
        assert.match(
            $("#agent-job-operations").text(),
            /This step may have finished\. Check the channel before you try again\./,
        );
        // A step with no summary falls back to a plain label, never the
        // internal action ID.
        assert.match($("#agent-job-operations").text(), /Team management step/);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

function base_detail(id, overrides) {
    return {
        job: {
            id,
            version: 1,
            status: "running",
            phase: "editing",
            job_kind: "answer",
            request: "Example",
            allowed_actions: [],
            ...overrides,
        },
        attempts: [],
        required_checks: [],
        operations: [],
        artifacts: [],
        operations_cursor: {offset: 0, next_offset: 0, truncated: false},
        artifacts_cursor: {offset: 0, next_offset: 0, truncated: false},
    };
}

// RL-5: the "interrupted" default sentence (no reason_code) drops the word
// Resume from its text when the job cannot resume, the same as every
// reason-code sentence contract 12.2 gates on resume_available.
async function interrupted_default_sentence_gated_by_resume() {
    const no_resume_id = "aaaaaaaa-1111-4111-8111-111111111111";
    const resumable_id = "bbbbbbbb-2222-4222-8222-222222222222";
    const api = {
        get_job: async (id) =>
            base_detail(id, {
                status: "interrupted",
                resume_available: id === resumable_id,
                resume_unavailable_reason: "runner_revoked",
                allowed_actions: ["resume"],
            }),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        job_action: async () => ({}),
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(no_resume_id);
        await flush();
        assert.equal(
            $("#agent-job-status").text(),
            "This task stopped before it finished. Create a new task.",
        );
        assert.doesNotMatch($("#agent-job-status").text(), /[Rr]esume/);
        assert.equal($("[data-job-action='resume']").length, 0);

        out.change_target(resumable_id);
        await flush();
        assert.equal(
            $("#agent-job-status").text(),
            "This task stopped before it finished. Resume the task, or create a new task.",
        );
        assert.equal($("[data-job-action='resume']").length, 1);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// AT-23: a verifying job held by an audience change shows the held
// sentence and its private-delivery button, and that button calls
// deliver-privately exactly once.
async function delivers_result_privately_once() {
    const job_id = "cccccccc-3333-4333-8333-333333333333";
    let calls = 0;
    let delivered = false;
    const api = {
        get_job: async (id) =>
            base_detail(id, {
                status: delivered ? "completed" : "verifying",
                reason_code: delivered ? null : "audience_changed",
                allowed_actions: delivered ? [] : ["deliver_privately"],
            }),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        async job_action(id, action, payload) {
            calls += 1;
            assert.equal(id, job_id);
            assert.equal(action, "deliver-privately");
            // payload crosses the vm sandbox boundary: compare its one field
            // by value, not the whole object (its prototype belongs to the
            // sandbox realm, so assert.deepEqual on the object itself fails).
            assert.equal(payload.expected_version, 1);
            delivered = true;
            return {};
        },
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(job_id);
        await flush();
        assert.equal(
            $("#agent-job-status").text(),
            "The result is saved, but it was not posted because the conversation changed. You can read it below.",
        );
        assert.equal($("[data-job-action='deliver-privately']").length, 1);
        $("[data-job-action='deliver-privately']").trigger("click");
        await flush();
        assert.equal(calls, 1);
        assert.equal(
            $("#agent-job-status").text(),
            "The result was sent to you in a direct message.",
        );
        assert.equal($("[data-job-action='deliver-privately']").length, 0);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// Finds the <dd> for a given fact <dt> label, the way a reader would: by
// its visible label, not by DOM position.
function fact_value($, $container, label) {
    return $container
        .find("dt")
        .filter((_index, element) => $(element).text() === label)
        .next("dd");
}

// Contract 13.1: the drawer shows each new fact only when the job has it,
// including a working link to the task this one follows.
async function shows_new_job_facts() {
    const job_id = "dddddddd-4444-4444-8444-444444444444";
    const follows_id = "eeeeeeee-5555-4555-8555-555555555555";
    const api = {
        get_job: async (id) =>
            base_detail(id, {
                job_kind: "code",
                repository: {id: "repo-1", alias: "grow-team"},
                base_ref: "main",
                budget: {
                    active_seconds: 900,
                    tool_rounds: 40,
                    input_tokens: 100000,
                    output_tokens: 8000,
                },
                instructions: {team_revision: 3, profile_revision: 7},
                follows_job_id: follows_id,
            }),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        job_action: async () => ({}),
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(job_id);
        await flush();
        const $summary = $("#agent-job-summary");
        assert.equal(fact_value($, $summary, "Repository").text(), "grow-team");
        assert.equal(fact_value($, $summary, "Base branch").text(), "main");
        assert.equal(fact_value($, $summary, "Budget").text(), "15 min · 40 tool steps");
        assert.equal(fact_value($, $summary, "Team instructions").text(), "Revision 3");
        assert.equal(fact_value($, $summary, "Agent instructions").text(), "Revision 7");
        const follows = fact_value($, $summary, "Follows task");
        assert.equal(follows.text(), `#${follows_id.slice(0, 8)}`);
        assert.equal(follows.find("a").attr("href"), `#agent-jobs/${follows_id}`);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// Contract 13.1: git.push and git.draft_pr operation cards read in plain
// words from `arguments`, and keep the hash and state inside a Technical
// details disclosure instead of the main card text.
async function shows_git_operation_cards() {
    const job_id = "ffffffff-6666-4666-8666-666666666666";
    const commit = "0123456789abcdef0123456789abcdef01234567";
    const push_op = {
        operation_id: "op-push",
        operation_hash: "0123456789abcdef",
        version: 1,
        status: "outcome_unknown",
        attempt_id: "attempt-1",
        action: "git.push",
        approval_id: null,
        approval_version: null,
        nonce: null,
        can_decide: false,
        arguments: {
            action: "git.push",
            repository_id: "repo-1",
            remote: "origin",
            branch: "r25/web-tasks",
            commit,
        },
    };
    const pr_op = {
        operation_id: "op-pr",
        operation_hash: "fedcba9876543210",
        version: 1,
        status: "approved",
        attempt_id: "attempt-1",
        action: "git.draft_pr",
        approval_id: null,
        approval_version: null,
        nonce: null,
        can_decide: false,
        arguments: {
            action: "git.draft_pr",
            repository_id: "repo-1",
            remote: "origin",
            base: "main",
            head: "r25/web-tasks",
            commit,
            title: "Add the task drawer copy",
            body: "This closes the review leftovers.",
        },
    };
    const api = {
        get_job: async (id) => ({
            ...base_detail(id, {job_kind: "code"}),
            attempts: [{id: "attempt-1", number: 1, active: true, process_state: "active"}],
            operations: [push_op, pr_op],
        }),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        job_action: async () => ({}),
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(job_id);
        await flush();
        const text = $("#agent-job-operations").text();
        assert.match(text, /Push commit 0123456 to branch r25\/web-tasks on origin/);
        assert.match(text, /Open a draft pull request from r25\/web-tasks into main on origin/);
        assert.match(text, /Title: Add the task drawer copy/);
        assert.match(
            text,
            /This action may have finished\. Check the repository before you try again\./,
        );
        const $technical = $("#agent-job-operations details").filter(
            (_index, element) => $(element).find("summary").text() === "Technical details",
        );
        assert.equal($technical.length, 2);
        assert.match($($technical[0]).text(), /Operation hash: 0123456789ab/);
        assert.match($($technical[1]).text(), /Operation hash: fedcba987654/);
        const $pr_text = $("#agent-job-operations details").filter(
            (_index, element) => $(element).find("summary").text() === "Pull request text",
        );
        assert.equal($pr_text.length, 1);
        assert.match($pr_text.text(), /This closes the review leftovers\./);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// Contract 13.1: the follow-up button hands the job to the composer's
// follow-up mode, with the result message preferred over the source
// message and the job's own ID and profile carried along.
async function opens_the_composer_in_followup_mode() {
    const job_id = "77777777-7777-4777-8777-777777777777";
    const followed = [];
    const api = {
        get_job: async (id) =>
            base_detail(id, {
                status: "completed",
                profile_id: "profile-x",
                source_message_id: 42,
                result: {message_id: 99},
                allowed_actions: ["follow_up"],
            }),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        job_action: async () => ({}),
    };
    const {dom, $, out, flush} = build_input_retention_harness(api, {
        open_for_followup(job) {
            followed.push(job);
        },
    });
    try {
        out.open(job_id);
        await flush();
        assert.equal($("[data-job-action='follow-up']").length, 1);
        $("[data-job-action='follow-up']").trigger("click");
        assert.equal(followed.length, 1);
        // The object open_for_followup receives is built inside the vm
        // sandbox, so its prototype belongs to that realm; compare its
        // fields, not the object itself.
        assert.equal(followed[0].id, job_id);
        assert.equal(followed[0].profile_id, "profile-x");
        assert.equal(followed[0].source_message_id, 42);
        assert.deepEqual(followed[0].result, {message_id: 99});
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// AF-24: an input submission captures its job ID at submit time, so a
// response that arrives after the drawer has moved on to another job never
// touches that other job's own input box or posts a second time.
async function input_from_job_a_never_posts_to_job_b() {
    const job_a = "88888888-8888-4888-8888-888888888888";
    const job_b = "99999999-9999-4999-8999-999999999999";
    const posted_to = [];
    let resolve_a;
    const pending_a = new Promise((resolve) => {
        resolve_a = resolve;
    });
    const api = {
        get_job: async (id) => queued_job_detail(id),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        async job_action(id) {
            posted_to.push(id);
            if (id === job_a) {
                await pending_a;
            }
            return {};
        },
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(job_a);
        await flush();
        $("#agent-job-input").val("hello from A");
        $("#agent-job-input-form").trigger("submit");
        await flush();
        assert.deepEqual(posted_to, [job_a]);

        out.change_target(job_b);
        await flush();
        $("#agent-job-input").val("hello from B");

        resolve_a();
        await flush();

        // Job A's late response must never touch job B's own input box or
        // post a second time.
        assert.deepEqual(posted_to, [job_a]);
        assert.equal($("#agent-job-input").val(), "hello from B");
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// AF-30: once a stop request is sent, a step that was waiting for approval
// stops offering Approve, and the live status reads the stopping sentence.
async function cancel_removes_approve_and_shows_stopping_sentence() {
    const job_id = "12121212-1212-4212-8212-121212121212";
    let status_value = "waiting_for_approval";
    let decidable = true;
    const detail = () => ({
        job: {
            id: job_id,
            version: 1,
            status: status_value,
            phase: "editing",
            job_kind: "code",
            request: "Push the fix",
            allowed_actions: status_value === "waiting_for_approval" ? ["cancel"] : [],
        },
        attempts: [{id: "attempt-1", number: 1, active: true, process_state: "active"}],
        required_checks: [],
        operations: [
            {
                operation_id: "operation-1",
                operation_hash: "0123456789abcdef",
                version: 1,
                status: "proposed",
                attempt_id: "attempt-1",
                action: "git.push",
                approval_id: "approval-1",
                approval_version: 1,
                nonce: "nonce-1",
                can_decide: decidable,
                approval_decision: "pending",
                arguments: {commit: "abcdef0", branch: "main", remote: "origin"},
            },
        ],
        artifacts: [],
        operations_cursor: {offset: 0, next_offset: 0, truncated: false},
        artifacts_cursor: {offset: 0, next_offset: 0, truncated: false},
    });
    const api = {
        get_job: async () => detail(),
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        async job_action(_id, action) {
            if (action === "cancel") {
                status_value = "cancel_requested";
                decidable = false;
            }
            return {};
        },
    };
    const {dom, $, out, flush} = build_input_retention_harness(api);
    try {
        out.open(job_id);
        await flush();
        assert.equal($("[data-job-action='approve']").length, 1);

        $("[data-job-action='cancel']").trigger("click");
        await flush();

        assert.equal($("[data-job-action='approve']").length, 0);
        assert.equal($("#agent-job-status").text(), "Your stop request went to the agent.");
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

void main()
    .then(() => retains_unresolved_input_across_visit())
    .then(() => accepted_input_does_not_return_on_next_visit())
    .then(() => shows_reason_sentence_and_gates_resume())
    .then(() => shows_team_manage_operation_card())
    .then(() => interrupted_default_sentence_gated_by_resume())
    .then(() => delivers_result_privately_once())
    .then(() => shows_new_job_facts())
    .then(() => shows_git_operation_cards())
    .then(() => opens_the_composer_in_followup_mode())
    .then(() => input_from_job_a_never_posts_to_job_b())
    .then(() => cancel_removes_approve_and_shows_stopping_sentence())
    .then(() => process.stdout.write("Agent job delegated-handler regressions passed.\n"))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
