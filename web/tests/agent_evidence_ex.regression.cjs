"use strict";

// Evidence tests for release 23 EX acceptance criteria that the audit
// marked "code_done_needs_evidence": the job panel and task composer code
// already does this, but no test asserted it before this file.
//
// This file drives the real web/src/agent_job_panel.ts and
// web/src/agent_task_composer.ts modules through a jsdom + vm harness, the
// same technique web/tests/agent_job_handlers.regression.cjs already uses.
// No provider, browser, container, or second machine is required.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const handlebars = require("handlebars");
const {JSDOM} = require("jsdom");
const ts = require("typescript");

const transpile = (source) =>
    ts.transpileModule(source, {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            esModuleInterop: true,
            target: ts.ScriptTarget.ES2022,
        },
    }).outputText;

// A minimal stand-in for $t(): real i18n substitutes "{name}" placeholders
// from its second argument. The stub modules below need the same behavior
// for the labels this file asserts against.
function format_message(descriptor, values) {
    let text = descriptor.defaultMessage;
    for (const [key, value] of Object.entries(values ?? {})) {
        text = text.replaceAll(`{${key}}`, String(value));
    }
    return text;
}

handlebars.registerHelper("t", (item) => item);

// ---------------------------------------------------------------------
// EX-54: a cancel that the agent has not confirmed yet, combined with a
// failed status poll, must keep saying so -- never "cancelled" or
// "completed" -- using web/src/agent_job_panel.ts.
// ---------------------------------------------------------------------

async function test_ex_54_unconfirmed_cancel_survives_a_failed_poll() {
    const dom = new JSDOM("<body></body>", {url: "https://realm.test"});
    global.window = dom.window;
    global.document = dom.window.document;
    const $ = require("jquery");
    let timer;
    const job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const attempt_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    let poll_should_fail = false;
    const detail = () => ({
        job: {
            id: job_id,
            version: 1,
            status: "cancel_requested",
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
        operations: [],
        artifacts: [],
        operations_cursor: {offset: 0, next_offset: 0, truncated: false},
        artifacts_cursor: {offset: 0, next_offset: 0, truncated: false},
    });
    const api = {
        async get_job() {
            if (poll_should_fail) {
                throw new Error("job status endpoint failed");
            }
            return detail();
        },
        get_job_events: async () => ({events: []}),
        get_job_inputs: async () => ({inputs: [], count: 0}),
        decide_approval: async () => ({}),
        job_action: async () => ({}),
    };
    const state = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_ui_state.ts"), "utf8")),
        {
            exports: state,
            crypto: require("node:crypto").webcrypto,
            require(name) {
                if (name === "./i18n.ts") {
                    return {$t: format_message};
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
                    return {$t: format_message};
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
        assert.match(
            $("#agent-job-attempt").text(),
            /Requested\. The agent has not confirmed it\./,
        );
        assert.doesNotMatch($("#agent-job-attempt").text(), /cancelled|completed/i);

        poll_should_fail = true;
        timer();
        await flush();
        assert.equal($("#agent-job-status").text(), "Job status is unknown. Retry this panel.");
        assert.match(
            $("#agent-job-attempt").text(),
            /Requested\. The agent has not confirmed it\./,
            "the last known attempt state must survive a failed poll",
        );
        assert.doesNotMatch(
            $("#agent-job-attempt").text() + $("#agent-job-status").text(),
            /cancelled|completed/i,
        );
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// ---------------------------------------------------------------------
// EX-55: a team default that changes while a draft is open must not
// change what that open draft submits, using
// web/src/agent_task_composer.ts. Its underlying mechanism is the same
// one AS-20 proves for the settings side of this spec pair.
// ---------------------------------------------------------------------

function build_composer_harness(api, current_user) {
    // The real "jquery" package binds to whatever global.document exists the
    // moment it is first required and then caches that binding, so each
    // harness must bust that cache before requiring it again for a new
    // jsdom window.
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete -- clearing one specific module's require cache entry, not an arbitrary key.
    delete require.cache[require.resolve("jquery")];
    const dom = new JSDOM("<body></body>", {url: "https://realm.test"});
    global.window = dom.window;
    global.document = dom.window.document;
    const $ = require("jquery");
    // jsdom's native ":open" pseudo-class support for <dialog> is not
    // guaranteed across versions, and the composer relies on it to know
    // whether its dialog is still the one on screen. Registering it
    // directly makes the check deterministic here.
    $.expr.pseudos.open = (elem) => elem.hasAttribute("open");
    dom.window.HTMLDialogElement.prototype.showModal = function () {
        this.setAttribute("open", "");
    };
    dom.window.HTMLDialogElement.prototype.close = function () {
        this.removeAttribute("open");
        this.dispatchEvent(new dom.window.Event("close"));
    };
    const state = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_ui_state.ts"), "utf8")),
        {
            exports: state,
            crypto: require("node:crypto").webcrypto,
            require(name) {
                if (name === "./i18n.ts") {
                    return {$t: format_message};
                }
                throw new Error(name);
            },
        },
    );
    const message_store_data = new Map();
    const out = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_task_composer.ts"), "utf8")),
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
                if (name === "./hash_util.ts") {
                    return {by_conversation_and_time_url: () => "#narrow/test"};
                }
                if (name === "./i18n.ts") {
                    return {$t: format_message};
                }
                if (name === "./message_lists.ts") {
                    return {current: undefined};
                }
                if (name === "./message_store.ts") {
                    return {get: (id) => message_store_data.get(id)};
                }
                if (name === "./settings_agents.ts") {
                    return {register_create_task_handler: () => () => {}};
                }
                if (name === "./state_data.ts") {
                    return {current_user, realm: {realm_url: "https://realm.test"}};
                }
                throw new Error(name);
            },
            window: dom.window,
            document: dom.window.document,
            // The composer checks `instanceof HTMLElement` in
            // render_runner_summary's details_box() helper, so the sandbox
            // needs the real constructor, the same as
            // agent_task_composer.regression.cjs's own harness.
            HTMLDialogElement: dom.window.HTMLDialogElement,
            HTMLElement: dom.window.HTMLElement,
            console,
        },
    );
    const flush = async () => {
        for (let i = 0; i < 25; i += 1) {
            await Promise.resolve();
        }
    };
    return {dom, $, out, flush, message_store_data};
}

function composer_profile(id, default_mode = "answer") {
    // The real AgentProfile always carries an owner identity (see
    // profile_schema in agent_api.ts); the option-list renderer in
    // agent_task_composer.ts reads owner.name unconditionally.
    return {id, name: `Composer ${id}`, default_mode, owner: {id: 1, name: "Owner"}};
}

async function test_ex_55_team_default_change_does_not_change_an_open_draft() {
    const current_user = {user_id: 1};
    let current_default = "A";
    let submitted;
    const api = {
        list_profiles: async () => ({
            profiles: [composer_profile("A"), composer_profile("B")],
            count: 2,
        }),
        async resolve_selection(args) {
            const chosen =
                args.selection_state === "explicit" ? args.explicit_profile_id : current_default;
            return {
                selection_source: args.selection_state === "explicit" ? "explicit" : "team_default",
                profile_id: chosen,
                profile_revision: 1,
                selection_revision: 1,
                selection_state: args.selection_state,
                eligible: true,
                queue_permitted: true,
                reason: "available",
            };
        },
        async create_job(payload) {
            submitted = payload;
            return {job: {id: "job-ex-55", status: "queued"}};
        },
    };
    const {dom, $, out, flush, message_store_data} = build_composer_harness(api, current_user);
    try {
        message_store_data.set(80, {id: 80, locally_echoed: false});
        out.open_for_message(80, "");
        await flush();
        assert.equal($("#agent-task-profile").val(), "A", "the draft opens on the current default");

        // The admin changes the team default from A to B while this draft,
        // already resolved to A, stays open.
        current_default = "B";

        $("#agent-task-request").val("Finish the draft that started on A").trigger("input");
        // A plain jQuery .trigger("submit") also invokes the native
        // HTMLFormElement.prototype.submit() as a compatibility fallback,
        // which jsdom does not implement. Dispatching the event directly
        // still reaches the composer's real addEventListener("submit")
        // handler without that fallback.
        $("#agent-task-form")[0].dispatchEvent(new dom.window.Event("submit", {cancelable: true}));
        await flush();
        assert.equal(
            submitted.profile_id,
            "A",
            "an open draft must submit the agent it resolved to, not a newer default",
        );

        message_store_data.set(81, {id: 81, locally_echoed: false});
        out.open_for_message(81, "");
        await flush();
        assert.equal(
            $("#agent-task-profile").val(),
            "B",
            "a dialog opened after the change must pick up the new default",
        );
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

void test_ex_54_unconfirmed_cancel_survives_a_failed_poll()
    .then(() => test_ex_55_team_default_change_does_not_change_an_open_draft())
    .then(() => process.stdout.write("Agent EX evidence regressions passed.\n"))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
