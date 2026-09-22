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
                    // These fixed strings carry no interpolation values, so
                    // returning the default message matches the real $t().
                    return {$t: (descriptor) => descriptor.defaultMessage};
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
                if (name === "./overlays.ts") {
                    return {open_overlay: ({$overlay}) => $overlay.addClass("show")};
                }
                if (name === "./browser_history.ts") {
                    return {exit_overlay() {}};
                }
                if (name === "./i18n.ts") {
                    // These fixed strings carry no interpolation values, so
                    // returning the default message matches the real $t().
                    return {$t: (descriptor) => descriptor.defaultMessage};
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
function build_input_retention_harness(api) {
    delete require.cache[require.resolve("jquery")];
    const dom = new JSDOM("<body></body>", {url: "https://realm.test"});
    global.window = dom.window;
    global.document = dom.window.document;
    const $ = require("jquery");
    let timer;
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
                    // These fixed strings carry no interpolation values, so
                    // returning the default message matches the real $t().
                    return {$t: (descriptor) => descriptor.defaultMessage};
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
                if (name === "./overlays.ts") {
                    return {open_overlay: ({$overlay}) => $overlay.addClass("show")};
                }
                if (name === "./browser_history.ts") {
                    return {exit_overlay() {}};
                }
                if (name === "./i18n.ts") {
                    // These fixed strings carry no interpolation values, so
                    // returning the default message matches the real $t().
                    return {$t: (descriptor) => descriptor.defaultMessage};
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
        job_action: async (_id, _action, payload) => {
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
        job_action: async (_id, _action, payload) => {
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

void main()
    .then(() => retains_unresolved_input_across_visit())
    .then(() => accepted_input_does_not_return_on_next_visit())
    .then(() => process.stdout.write("Agent job delegated-handler regressions passed.\n"))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
