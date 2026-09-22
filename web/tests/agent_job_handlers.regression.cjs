"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const handlebars = require("handlebars");
const {JSDOM} = require("jsdom");
const ts = require("typescript");

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
        {exports: state, crypto: require("node:crypto").webcrypto},
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

void main()
    .then(() => process.stdout.write("Agent job delegated-handler regressions passed.\n"))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
