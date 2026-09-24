"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const {JSDOM} = require("jsdom");
const ts = require("typescript");

// Builds a fresh jsdom + vm harness for the real agent_task_composer module,
// wired to the real agent_ui_state module the same way the sibling
// agent_job_handlers.regression.cjs harness does.
function build_harness(api) {
    Reflect.deleteProperty(require.cache, require.resolve("jquery"));
    const dom = new JSDOM("<body></body>", {url: "https://realm.test"});
    global.window = dom.window;
    global.document = dom.window.document;
    // jsdom recognizes <dialog> as HTMLDialogElement but does not implement
    // its imperative methods, so this harness supplies the minimum behavior
    // the composer depends on: the open attribute and a close event.
    dom.window.HTMLDialogElement.prototype.showModal = function () {
        this.setAttribute("open", "");
    };
    dom.window.HTMLDialogElement.prototype.close = function () {
        this.removeAttribute("open");
        this.dispatchEvent(new dom.window.Event("close"));
    };
    const $ = require("jquery");
    const transpile = (source) =>
        ts.transpileModule(source, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                esModuleInterop: true,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText;
    // A minimal $t stand-in that substitutes {placeholder} values the same
    // way FormatJS does, since this suite asserts on the substituted text.
    const $t = (descriptor, values) => {
        let text = descriptor.defaultMessage;
        for (const [key, value] of Object.entries(values ?? {})) {
            text = text.replaceAll(`{${key}}`, String(value));
        }
        return text;
    };
    const state = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_ui_state.ts"), "utf8")),
        {
            exports: state,
            crypto: require("node:crypto").webcrypto,
            require(name) {
                if (name === "./i18n.ts") {
                    return {$t};
                }
                throw new Error(name);
            },
        },
    );
    const messages = new Map();
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
                    return {by_conversation_and_time_url: () => "#narrow/near/1"};
                }
                if (name === "./message_lists.ts") {
                    return {current: undefined};
                }
                if (name === "./message_store.ts") {
                    return {get: (id) => messages.get(id)};
                }
                if (name === "./settings_agents.ts") {
                    return {register_create_task_handler: () => () => {}};
                }
                if (name === "./state_data.ts") {
                    return {
                        current_user: {user_id: 1},
                        realm: {realm_url: "https://realm.test"},
                    };
                }
                if (name === "./i18n.ts") {
                    return {$t};
                }
                throw new Error(name);
            },
            window: dom.window,
            document: dom.window.document,
            // The composer checks `instanceof HTMLDialogElement` and
            // `instanceof HTMLElement` at runtime, so the sandbox needs
            // both DOM constructors as bare globals.
            HTMLDialogElement: dom.window.HTMLDialogElement,
            HTMLElement: dom.window.HTMLElement,
            console,
        },
    );
    const flush = async () => {
        for (let index = 0; index < 20; index += 1) {
            await Promise.resolve();
        }
    };
    return {dom, $, out, messages, flush};
}

// jQuery's trigger("submit") calls the native, unimplemented-in-jsdom
// form.submit() method instead of dispatching a "submit" event, since the
// composer's own submit listener is a plain addEventListener, not a
// jQuery-bound one. Dispatch the event directly so that listener fires.
function submit_form(dom) {
    dom.window.document
        .querySelector("#agent-task-form")
        .dispatchEvent(new dom.window.Event("submit", {cancelable: true, bubbles: true}));
}

function profile(id, name, owner, runner_name, default_mode = "answer", extra = {}) {
    return {
        id,
        name,
        owner: {id: 1, name: owner},
        runner: runner_name
            ? {
                  name: runner_name,
                  host_kind: extra.host_kind ?? "workstation",
                  observed_presence: extra.observed_presence ?? "online",
              }
            : null,
        provider: extra.provider ?? null,
        default_mode,
    };
}

async function labels_two_same_named_profiles_by_owner_and_device() {
    const api = {
        list_profiles: async () => ({
            profiles: [
                profile("profile-a", "Helper", "Ann", "ann-laptop"),
                profile("profile-b", "Helper", "Budi", "budi-server"),
            ],
            count: 2,
        }),
        resolve_selection: async () => ({
            selection_source: "explicit",
            profile_id: null,
            profile_revision: null,
            selection_revision: null,
            selection_state: "unset",
            eligible: false,
            queue_permitted: false,
            reason: "cleared",
            repository: null,
        }),
    };
    const {dom, $, out, messages, flush} = build_harness(api);
    try {
        messages.set(1, {
            id: 1,
            locally_echoed: false,
            type: "stream",
            display_recipient: "general",
            topic: "chat",
        });
        out.open_for_message(1);
        await flush();
        const texts = $("#agent-task-profile option")
            .get()
            .map((option) => option.textContent);
        assert.ok(texts.includes("Helper · Ann · ann-laptop"), texts.join(" | "));
        assert.ok(texts.includes("Helper · Budi · budi-server"), texts.join(" | "));
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function disables_coding_without_a_repository_and_reverts_the_choice() {
    let resolution = {
        selection_source: "explicit",
        profile_id: "profile-a",
        profile_revision: 1,
        selection_revision: 1,
        selection_state: "explicit",
        eligible: true,
        queue_permitted: true,
        reason: "available",
        repository: null,
    };
    const api = {
        list_profiles: async () => ({
            profiles: [profile("profile-a", "Helper", "Ann", "ann-laptop", "code")],
            count: 1,
        }),
        resolve_selection: async () => resolution,
    };
    const {dom, $, out, messages, flush} = build_harness(api);
    try {
        messages.set(1, {
            id: 1,
            locally_echoed: false,
            type: "stream",
            display_recipient: "general",
            topic: "chat",
        });
        out.open_for_message(1, "profile-a");
        await flush();
        // The profile's own default_mode "code" selects Coding, but no
        // repository is resolved for it, so the option must be disabled...
        assert.equal($("#agent-task-kind option[value='code']").prop("disabled"), true);
        // ...and the kind reverts to Answer rather than staying on a choice
        // that cannot submit.
        assert.equal($("#agent-task-kind").val(), "answer");
        assert.match($("#agent-task-status").text(), /no repository set up/);

        // Once the resolver reports a repository, Coding becomes available.
        resolution = {...resolution, repository: {id: "repo-1", alias: "app", base_ref: "main"}};
        $("#agent-task-profile").trigger("change");
        await flush();
        assert.equal($("#agent-task-kind option[value='code']").prop("disabled"), false);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function sends_patch_delivery_with_the_resolved_repository() {
    const resolution = {
        selection_source: "explicit",
        profile_id: "profile-a",
        profile_revision: 1,
        selection_revision: 1,
        selection_state: "explicit",
        eligible: true,
        queue_permitted: true,
        reason: "available",
        repository: {id: "repo-1", alias: "app", base_ref: "main"},
    };
    let created_payload;
    const api = {
        list_profiles: async () => ({
            profiles: [profile("profile-a", "Coder", "Ann", "ann-laptop", "code")],
            count: 1,
        }),
        resolve_selection: async () => resolution,
        async create_job(payload) {
            created_payload = payload;
            return {job: {id: "22222222-2222-4222-8222-222222222222", status: "queued"}};
        },
    };
    const {dom, $, out, messages, flush} = build_harness(api);
    try {
        messages.set(1, {
            id: 1,
            locally_echoed: false,
            type: "stream",
            display_recipient: "general",
            topic: "chat",
        });
        out.open_for_message(1, "profile-a");
        await flush();
        assert.equal($("#agent-task-kind").val(), "code");
        $("#agent-task-request").val("Fix the bug").trigger("input");
        submit_form(dom);
        await flush();
        assert.deepEqual(created_payload.delivery_target, "patch");
        assert.deepEqual(created_payload.repository_id, "repo-1");
        assert.deepEqual(created_payload.base_ref, "main");
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function shows_the_servers_rejection_reason_for_a_definite_failure() {
    const resolution = {
        selection_source: "explicit",
        profile_id: "profile-a",
        profile_revision: 1,
        selection_revision: 1,
        selection_state: "explicit",
        eligible: true,
        queue_permitted: true,
        reason: "available",
        repository: null,
    };
    const api = {
        list_profiles: async () => ({
            profiles: [profile("profile-a", "Helper", "Ann", "ann-laptop")],
            count: 1,
        }),
        resolve_selection: async () => resolution,
        async create_job() {
            const error = new Error("rejected");
            error.status = 400;
            error.responseJSON = {msg: "Agent queue is full."};
            throw error;
        },
    };
    const {dom, $, out, messages, flush} = build_harness(api);
    try {
        messages.set(1, {
            id: 1,
            locally_echoed: false,
            type: "stream",
            display_recipient: "general",
            topic: "chat",
        });
        out.open_for_message(1, "profile-a");
        await flush();
        $("#agent-task-request").val("Please help").trigger("input");
        submit_form(dom);
        await flush();
        assert.match($("#agent-task-status").text(), /Agent queue is full\./);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function keeps_the_idempotent_retry_message_for_an_unclear_failure() {
    const resolution = {
        selection_source: "explicit",
        profile_id: "profile-a",
        profile_revision: 1,
        selection_revision: 1,
        selection_state: "explicit",
        eligible: true,
        queue_permitted: true,
        reason: "available",
        repository: null,
    };
    const api = {
        list_profiles: async () => ({
            profiles: [profile("profile-a", "Helper", "Ann", "ann-laptop")],
            count: 1,
        }),
        resolve_selection: async () => resolution,
        async create_job() {
            const error = new Error("network");
            error.status = 503;
            throw error;
        },
    };
    const {dom, $, out, messages, flush} = build_harness(api);
    try {
        messages.set(1, {
            id: 1,
            locally_echoed: false,
            type: "stream",
            display_recipient: "general",
            topic: "chat",
        });
        out.open_for_message(1, "profile-a");
        await flush();
        $("#agent-task-request").val("Please help").trigger("input");
        submit_form(dom);
        await flush();
        assert.match($("#agent-task-status").text(), /will not create a second task/);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// Contract 13.3: the resolved runner's device, device type, and connection,
// the model connection's location, and the Coding repository each show as
// their own line, built from data the dialog already has.
async function shows_runner_model_and_repository_lines() {
    const resolution = {
        selection_source: "explicit",
        profile_id: "profile-a",
        profile_revision: 1,
        selection_revision: 1,
        selection_state: "explicit",
        eligible: true,
        queue_permitted: true,
        reason: "available",
        repository: {id: "repo-1", alias: "app", base_ref: "main"},
    };
    const api = {
        list_profiles: async () => ({
            profiles: [
                profile("profile-a", "Coder", "Ann", "ann-laptop", "code", {
                    host_kind: "server",
                    observed_presence: "online",
                    provider: {model_location: "private_network"},
                }),
            ],
            count: 1,
        }),
        resolve_selection: async () => resolution,
    };
    const {dom, $, out, messages, flush} = build_harness(api);
    try {
        messages.set(1, {
            id: 1,
            locally_echoed: false,
            type: "stream",
            display_recipient: "general",
            topic: "chat",
        });
        out.open_for_message(1, "profile-a");
        await flush();
        const details = $("#agent-task-details").text();
        assert.match(details, /Runs on ann-laptop · Server · Connected/);
        assert.match(details, /Model: private network/);
        assert.match(details, /Repository: app · base main/);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// Contract 13.3: the runner_offline selection label no longer promises the
// task "starts when the runner comes back"; it says the task waits until
// the device connects or its start deadline passes.
async function shows_the_offline_device_label() {
    const resolution = {
        selection_source: "explicit",
        profile_id: "profile-a",
        profile_revision: 1,
        selection_revision: 1,
        selection_state: "explicit",
        eligible: true,
        queue_permitted: true,
        reason: "runner_offline",
        repository: null,
    };
    const api = {
        list_profiles: async () => ({
            profiles: [profile("profile-a", "Helper", "Ann", "ann-laptop")],
            count: 1,
        }),
        resolve_selection: async () => resolution,
    };
    const {dom, $, out, messages, flush} = build_harness(api);
    try {
        messages.set(1, {
            id: 1,
            locally_echoed: false,
            type: "stream",
            display_recipient: "general",
            topic: "chat",
        });
        out.open_for_message(1, "profile-a");
        await flush();
        assert.match(
            $("#agent-task-status").text(),
            /device is offline.*waits until the device connects or until its start deadline passes/,
        );
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// Contract 13.3: with no source message, Create task stays disabled and the
// dialog explains why instead of letting the person submit a task that is
// guaranteed to fail.
async function disables_submit_and_explains_when_there_is_no_source() {
    const api = {
        list_profiles: async () => ({profiles: [], count: 0}),
        resolve_selection: async () => ({
            selection_source: "explicit",
            profile_id: null,
            profile_revision: null,
            selection_revision: null,
            selection_state: "unset",
            eligible: false,
            queue_permitted: false,
            reason: "cleared",
            repository: null,
        }),
    };
    const {dom, $, out, flush} = build_harness(api);
    try {
        out.open_for_message(-1);
        await flush();
        assert.equal($("#agent-task-submit").prop("disabled"), true);
        assert.match(
            $("#agent-task-source").text(),
            /Open a conversation and select a message first/,
        );
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// Contract 13.3: open_for_followup renames the dialog, notes which task it
// follows, preselects that task's agent, and sends follows_job_id.
async function opens_in_followup_mode_and_sends_follows_job_id() {
    const resolution = {
        selection_source: "explicit",
        profile_id: "profile-a",
        profile_revision: 1,
        selection_revision: 1,
        selection_state: "explicit",
        eligible: true,
        queue_permitted: true,
        reason: "available",
        repository: null,
    };
    let created_payload;
    const api = {
        list_profiles: async () => ({
            profiles: [profile("profile-a", "Helper", "Ann", "ann-laptop")],
            count: 1,
        }),
        resolve_selection: async () => resolution,
        async create_job(payload) {
            created_payload = payload;
            return {job: {id: "aaaaaaaa-0000-4000-8000-000000000000", status: "queued"}};
        },
    };
    const {dom, $, out, messages, flush} = build_harness(api);
    try {
        messages.set(7, {
            id: 7,
            locally_echoed: false,
            type: "stream",
            display_recipient: "general",
            topic: "chat",
        });
        out.open_for_followup({
            id: "ffffffff-1111-4111-8111-111111111111",
            profile_id: "profile-a",
            source_message_id: 7,
            result: null,
        });
        await flush();
        assert.equal($("#agent-task-heading").text(), "Create follow-up task");
        assert.match($("#agent-task-source").text(), /Follows task #ffffffff/);
        assert.equal($("#agent-task-profile").val(), "profile-a");

        $("#agent-task-request").val("Continue the work").trigger("input");
        submit_form(dom);
        await flush();
        assert.equal(created_payload.follows_job_id, "ffffffff-1111-4111-8111-111111111111");
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

void labels_two_same_named_profiles_by_owner_and_device()
    .then(() => disables_coding_without_a_repository_and_reverts_the_choice())
    .then(() => sends_patch_delivery_with_the_resolved_repository())
    .then(() => shows_the_servers_rejection_reason_for_a_definite_failure())
    .then(() => keeps_the_idempotent_retry_message_for_an_unclear_failure())
    .then(() => shows_runner_model_and_repository_lines())
    .then(() => shows_the_offline_device_label())
    .then(() => disables_submit_and_explains_when_there_is_no_source())
    .then(() => opens_in_followup_mode_and_sends_follows_job_id())
    .then(() => process.stdout.write("Agent task composer regressions passed.\n"))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
