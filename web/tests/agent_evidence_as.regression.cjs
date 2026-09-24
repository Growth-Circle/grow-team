"use strict";

// Evidence tests for release 23 AS acceptance criteria that the audit
// marked "code_done_needs_evidence": the settings panel and task composer
// code already does this, but no test asserted it before this file.
//
// This file drives the real web/src/settings_agents.ts and
// web/src/agent_task_composer.ts modules through a jsdom + vm harness, the
// same technique web/tests/agent_settings_handlers.regression.cjs and
// web/tests/agent_job_handlers.regression.cjs already use. No provider,
// browser, or second machine is required.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const handlebars = require("handlebars");
const {JSDOM} = require("jsdom");
const ts = require("typescript");

function deferred() {
    let resolve_call;
    let reject_call;
    const promise = new Promise((resolve, reject) => {
        resolve_call = resolve;
        reject_call = reject;
    });
    return {promise, resolve: resolve_call, reject: reject_call};
}

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

// ---------------------------------------------------------------------
// Settings panel harness (web/src/settings_agents.ts): AS-05, AS-06,
// AS-09, AS-12, AS-13, AS-25, AS-27 (settings side), AS-28.
// ---------------------------------------------------------------------

async function settings_scenarios() {
    handlebars.registerHelper("t", (item) => item);
    const html = handlebars.compile(
        fs.readFileSync(path.join(__dirname, "../templates/settings/agent_settings.hbs"), "utf8"),
    )({});
    const dom = new JSDOM(html, {url: "https://realm.test"});
    global.window = dom.window;
    global.document = dom.window.document;
    const $ = require("jquery");
    const timers = new Map();
    let timer_id = 0;
    const current_user = {user_id: 1};

    function runner_with_catalog(id, name, adapter_id, sandbox_alias, observed_presence) {
        return {
            id,
            name,
            owner_id: 1,
            host_kind: "workstation",
            observed_presence: observed_presence ?? "online",
            metadata_revision: 1,
            revision: 1,
            catalog_revision: 1,
            allowed_actions: ["edit"],
            catalog_summary: {
                revision: 1,
                reported_at: null,
                adapters: [{id: adapter_id, version: "1", auth_state: "ready"}],
                sandboxes: [{alias: sandbox_alias}],
            },
        };
    }
    const runner_a = runner_with_catalog("ra", "Runner A", "acp-a", "safe-a");
    const runner_b = runner_with_catalog("rb", "Runner B", "acp-b", "safe-b");
    const runner_unknown = runner_with_catalog("ru", "Runner U", "grow", "safe", "unknown");
    let runners = [runner_a, runner_b];

    function profile(id, overrides) {
        return {
            id,
            name: `Profile ${id}`,
            description: "Original description",
            runner_id: "ra",
            provider_id: null,
            repository_id: null,
            default_mode: "answer",
            mode: "acp",
            adapter_id: "acp-a",
            adapter_version: "1",
            revision: 1,
            metadata_revision: 1,
            owner: {id: 1, name: "Owner"},
            runner: runner_a,
            provider: null,
            repository: null,
            access: {complete: true, runner: true, provider: true, repository: true},
            desired_state: "enabled",
            readiness_state: "ready",
            readiness_revision: 1,
            allowed_actions: ["edit"],
            configuration: null,
            ...overrides,
        };
    }
    function provider(id, runner_id) {
        return {
            id,
            name: `Provider ${id}`,
            owner_id: 1,
            runner_id,
            allowed_actions: ["edit"],
            allowed_models: ["m"],
            model_id: "m",
            data_scope: ["synthetic"],
            context_window_tokens: 8192,
            max_output_tokens: 2048,
            api_mode: "chat_completions",
            base_url: "https://model.test",
            metadata_revision: 1,
            config_version: 1,
        };
    }
    function repository(id, runner_id) {
        return {
            id,
            workspace_alias: `repo-${id}`,
            owner_id: 1,
            runner_id,
            disabled_at: null,
            allowed_actions: ["edit"],
        };
    }
    let profiles = [profile("a"), profile("b")];
    const providers = [provider("pa", "ra"), provider("pb", "rb")];
    const repositories = [repository("wa", "ra"), repository("wb", "rb")];
    let default_server_revision = 1;
    let default_profile_id = "a";
    const calls = {
        create_profile: 0,
        update_profile: 0,
        create_provider: 0,
        profile_action: 0,
        update_team_default: 0,
    };
    const api = {
        update_runner_metadata: async () => ({}),
        async update_team_default() {
            calls.update_team_default += 1;
            return {};
        },
        async create_profile() {
            calls.create_profile += 1;
            return {};
        },
        async update_profile(id, payload) {
            calls.update_profile += 1;
            return {profile: {...profile(id), ...payload}};
        },
        async profile_action() {
            calls.profile_action += 1;
            return {};
        },
        attach_channel: async () => ({}),
        create_repository: async () => ({}),
        async create_provider() {
            calls.create_provider += 1;
            return {provider: {id: "new", name: "New", model_id: "m", revision: 1}};
        },
        update_provider: async (_id, payload) => ({provider: {...provider("a", "ra"), ...payload}}),
        profile_network_choice: () => ({network: {targets: []}}),
        list_profiles: async () => ({profiles, count: profiles.length}),
        list_runners: async () => ({runners, count: runners.length}),
        list_providers: async () => ({providers, count: providers.length}),
        list_repositories: async () => ({repositories, count: repositories.length}),
        list_grants: async () => ({grants: [], count: 0}),
        get_team_default: async () => ({
            default: {
                profile: profiles.find((item) => item.id === default_profile_id),
                selection_revision: default_server_revision,
                has_default: true,
                allowed_actions: ["clear", "set"],
            },
        }),
        get_profile: async (id) => ({profile: profile(id), setup: null, attachments: []}),
        get_provider: async (id) => ({provider: provider(id, "ra")}),
        recover_profile: async () => ({profile: profile("recovered")}),
        // load_default() always calls this; a missing key here crashes the
        // whole default-tab load (__importStar only forwards a name that
        // exists on this object when the module first requires
        // "./agent_api.ts", so it must be a key from the start).
        get_team_instructions: async () => ({
            team_instructions: {text: "", revision: 1, allowed_actions: []},
        }),
        agent_error_code: (error) =>
            error && typeof error === "object" && typeof error.responseJSON?.code === "string"
                ? error.responseJSON.code
                : undefined,
    };
    // agent_settings_labels.ts carries the full settings vocabulary (many
    // more mappings than the two agent_ui_state.ts helpers stubbed above),
    // so hand-duplicating it here would drift from the real labels; load
    // the real module instead.
    const settings_labels = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_settings_labels.ts"), "utf8")),
        {
            exports: settings_labels,
            require(name) {
                if (name === "./i18n.ts") {
                    return {$t: format_message};
                }
                throw new Error(name);
            },
        },
    );
    const out = {};
    const source = fs.readFileSync(path.join(__dirname, "../src/settings_agents.ts"), "utf8");
    vm.runInNewContext(transpile(source), {
        exports: out,
        require(name) {
            if (name === "jquery") {
                return $;
            }
            if (name === "./agent_api.ts") {
                return api;
            }
            if (name === "./agent_settings_labels.ts") {
                return settings_labels;
            }
            if (name === "./agent_ui_state.ts") {
                return {
                    new_client_key: () => `key-${Math.random()}`,
                    // Mirrors agent_ui_state.ts's own derived_budget_defaults
                    // (contract 3.5); this harness stubs the module by hand
                    // instead of loading the real one, see the file banner.
                    derived_budget_defaults: (provider) =>
                        provider
                            ? {
                                  input_tokens: Math.min(
                                      Math.max(provider.context_window_tokens * 10, 200000),
                                      4000000,
                                  ),
                                  output_tokens: Math.min(
                                      Math.max(provider.max_output_tokens * 4, 16000),
                                      256000,
                                  ),
                              }
                            : {input_tokens: 400000, output_tokens: 16000},
                };
            }
            if (name === "./state_data.ts") {
                return {current_user, realm: {realm_url: "https://realm.test"}};
            }
            if (name === "./confirm_dialog.ts") {
                return {launch: (config) => config.on_click()};
            }
            if (name === "./people.ts") {
                return {
                    maybe_get_user_by_id: () => ({full_name: "Owner"}),
                    get_realm_active_human_users: () => [],
                };
            }
            if (name === "./user_groups.ts") {
                return {get_realm_user_groups: () => []};
            }
            if (name === "./stream_data.ts") {
                return {
                    get_unsorted_subs_with_content_access: () => [{stream_id: 42, name: "Denmark"}],
                    get_sub_by_id: () => ({name: "Denmark"}),
                };
            }
            if (name === "./i18n.ts") {
                return {$t: format_message};
            }
            throw new Error(name);
        },
        window: dom.window,
        document: dom.window.document,
        sessionStorage: dom.window.sessionStorage,
        setTimeout(fn) {
            timer_id += 1;
            timers.set(timer_id, fn);
            return timer_id;
        },
        clearTimeout(id) {
            timers.delete(id);
        },
        console,
    });
    $("#agent-settings")[0].getClientRects = () => [{}];
    const flush = async () => {
        for (let i = 0; i < 20; i += 1) {
            await Promise.resolve();
        }
    };
    const click = (action, id) =>
        $(`<button data-agent-action="${action}" data-agent-id="${id}">`)
            .appendTo("body")
            .trigger("click")
            .remove();
    async function fresh(tab = "directory") {
        out.reset();
        dom.window.sessionStorage.clear();
        out.set_up();
        await flush();
        if (tab !== "directory") {
            $(`[data-agent-tab="${tab}"]`).trigger("click");
            await flush();
        }
    }
    function latest_timer() {
        const fn = [...timers.values()].at(-1);
        if (!fn) {
            throw new Error("No refresh timer was scheduled.");
        }
        return fn;
    }

    try {
        // AS-05: an unknown runner presence renders as "unknown", and a
        // failed directory load says the profile status is unknown.
        profiles = [profile("a", {runner: runner_unknown})];
        runners = [runner_unknown];
        await fresh();
        assert.match($("#agent-profile-list").text(), /Runner presence: Status unknown/);
        api.list_profiles = async () => {
            throw new Error("directory unavailable");
        };
        await fresh();
        assert.match(
            $("#agent-settings-status").text(),
            /Profile status is unknown\. Retry the directory\./,
        );

        // AS-06: two runners with different catalogs must not mix options.
        profiles = [profile("a"), profile("b")];
        runners = [runner_a, runner_b];
        api.list_profiles = async () => ({profiles, count: profiles.length});
        $("#agent-new-profile").trigger("click");
        await flush();
        assert.deepEqual(
            $("#agent-profile-adapter option")
                .map((_, item) => item.value)
                .get(),
            ["acp-a@1"],
        );
        assert.deepEqual(
            $("#agent-profile-provider option")
                .map((_, item) => item.value)
                .get(),
            ["", "pa"],
        );
        assert.deepEqual(
            $("#agent-profile-repository option")
                .map((_, item) => item.value)
                .get(),
            ["", "wa"],
        );
        $("#agent-profile-runner").val("rb").trigger("change");
        assert.deepEqual(
            $("#agent-profile-adapter option")
                .map((_, item) => item.value)
                .get(),
            ["acp-b@1"],
        );
        assert.deepEqual(
            $("#agent-profile-provider option")
                .map((_, item) => item.value)
                .get(),
            ["", "pb"],
        );
        assert.deepEqual(
            $("#agent-profile-repository option")
                .map((_, item) => item.value)
                .get(),
            ["", "wb"],
        );

        // AS-09: choosing runner A's provider and repository, then
        // switching to runner B, must clear both choices for the save.
        $("#agent-profile-runner").val("ra").trigger("change");
        $("#agent-profile-provider").val("pa");
        $("#agent-profile-repository").val("wa");
        $("#agent-profile-runner").val("rb").trigger("change");
        assert.equal($("#agent-profile-provider").val(), "");
        assert.equal($("#agent-profile-repository").val(), "");
        $("#agent-profile-name").val("AS-09 profile").trigger("input");
        $("#agent-profile-adapter").val("acp-b@1");
        $("#agent-profile-sandbox").val("safe-b");
        let saved_payload;
        api.create_profile = async (payload) => {
            saved_payload = payload;
            calls.create_profile += 1;
            return {profile: profile("as-09")};
        };
        $("#agent-profile-form").trigger("submit");
        await flush();
        assert.equal(saved_payload.provider_id, null);
        assert.equal(saved_payload.repository_id, null);
        assert.equal(saved_payload.runner_id, "rb");
        api.create_profile = async () => {
            calls.create_profile += 1;
            return {};
        };

        // AS-12: typing then clearing a field, and a scheduled refresh
        // with new server data, must not overwrite the open draft, and
        // must not trigger any profile mutation.
        await fresh();
        click("profile-edit", "a");
        await flush();
        assert.equal($("#agent-profile-description").val(), "Original description");
        $("#agent-profile-description").val("Temporary text").trigger("input");
        $("#agent-profile-description").val("").trigger("input");
        const mutations_before = calls.profile_action + calls.update_profile;
        profiles = [profile("a", {readiness_state: "unknown"}), profile("b")];
        latest_timer()();
        await flush();
        assert.equal($("#agent-profile-description").val(), "");
        assert.equal(calls.profile_action + calls.update_profile, mutations_before);

        // AS-13: closing the profile and provider editors before
        // submitting must never call the mutation API.
        await fresh();
        const create_profile_calls_before = calls.create_profile;
        $("#agent-new-profile").trigger("click");
        await flush();
        $("#agent-profile-name").val("Never saved").trigger("input");
        $("#agent-profile-cancel").trigger("click");
        await flush();
        assert.equal(calls.create_profile, create_profile_calls_before);
        assert.equal($("#agent-profile-form").prop("hidden"), true);
        const create_provider_calls_before = calls.create_provider;
        $("#agent-new-provider").trigger("click");
        $("#agent-provider-name").val("Never saved either").trigger("input");
        $("#agent-provider-cancel").trigger("click");
        await flush();
        assert.equal(calls.create_provider, create_provider_calls_before);
        assert.equal($("#agent-provider-form").prop("hidden"), true);

        // AS-25: a rejected team-default save keeps the local choice, shows
        // a conflict message, never retries by itself, and the next load
        // still reflects the true current default.
        await fresh("default");
        $("#agent-default-choice").val("b").trigger("change");
        const update_team_default_calls_before = calls.update_team_default;
        api.update_team_default = async () => {
            calls.update_team_default += 1;
            throw new Error("stale revision");
        };
        $("#agent-default-form").trigger("submit");
        await flush();
        assert.equal($("#agent-default-choice").val(), "b");
        assert.match(
            $("#agent-settings-status").text(),
            /Team default changed or is unavailable\. Your selection remains\./,
        );
        assert.equal(calls.update_team_default, update_team_default_calls_before + 1);
        default_server_revision = 2;
        default_profile_id = "a";
        api.update_team_default = async () => {
            calls.update_team_default += 1;
            return {};
        };
        await fresh("default");
        assert.match($("#agent-team-default").text(), /Profile a/);

        // AS-28: a provider's credential and local reference fields stay
        // empty after opening the editor and after a successful save.
        await fresh("connections");
        click("provider-edit", "a");
        await flush();
        assert.equal($("#agent-provider-credential").val(), "");
        assert.equal($("#agent-provider-local-ref").val(), "");
        $("#agent-provider-credential").val("super-secret-token");
        $("#agent-provider-name").val("Provider a").trigger("input");
        $("#agent-provider-url").val("https://model.test").trigger("input");
        $("#agent-provider-model").val("m").trigger("input");
        $("#agent-provider-allowed-models").val("m").trigger("input");
        $("#agent-provider-scopes input").first().prop("checked", true);
        $("#agent-provider-form").trigger("submit");
        await flush();
        assert.equal($("#agent-provider-credential").val(), "");
        assert.equal($("#agent-provider-local-ref").val(), "");

        // AS-27 (settings side): a deferred directory response from a
        // previous account must not render after the account switches.
        const stale = deferred();
        const second_session = deferred();
        let list_profiles_calls = 0;
        api.list_profiles = () => {
            list_profiles_calls += 1;
            return list_profiles_calls === 1 ? stale.promise : second_session.promise;
        };
        out.reset();
        dom.window.sessionStorage.clear();
        out.set_up();
        await flush();
        current_user.user_id = 2;
        out.reset();
        out.set_up();
        await flush();
        stale.resolve({profiles: [profile("stale-leak")], count: 1});
        await flush();
        assert.doesNotMatch($("#agent-profile-list").text(), /stale-leak/);
        second_session.resolve({profiles: [profile("second-session")], count: 1});
        await flush();
        assert.match($("#agent-profile-list").text(), /second-session/);
    } finally {
        out.reset();
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

// ---------------------------------------------------------------------
// Task composer harness (web/src/agent_task_composer.ts): AS-18, AS-19,
// AS-20, AS-21, AS-27 (composer side).
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
            // The composer's runner summary checks `instanceof HTMLElement`.
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

async function test_as_18_explicit_choice_wins_over_a_late_default_response() {
    const current_user = {user_id: 1};
    const resolve_calls = [];
    const first_call = deferred();
    let submitted;
    const api = {
        list_profiles: async () => ({
            profiles: [composer_profile("A"), composer_profile("B")],
            count: 2,
        }),
        resolve_selection(args) {
            resolve_calls.push(args);
            if (resolve_calls.length === 1) {
                return first_call.promise;
            }
            return Promise.resolve({
                selection_source: "explicit",
                profile_id: args.explicit_profile_id,
                profile_revision: 1,
                selection_revision: null,
                selection_state: "explicit",
                eligible: true,
                queue_permitted: true,
                reason: "available",
            });
        },
        async create_job(payload) {
            submitted = payload;
            return {job: {id: "job-as-18", status: "queued"}};
        },
    };
    const {dom, $, out, flush, message_store_data} = build_composer_harness(api, current_user);
    try {
        message_store_data.set(42, {id: 42, locally_echoed: false});
        out.open_for_message(42, "");
        await flush();
        assert.equal(
            resolve_calls.length,
            1,
            "the pending default resolve should still be in flight",
        );

        $("#agent-task-profile").val("B").trigger("change");
        await flush();
        assert.equal($("#agent-task-profile").val(), "B");

        first_call.resolve({
            selection_source: "team_default",
            profile_id: "A",
            profile_revision: 1,
            selection_revision: 1,
            selection_state: "unset",
            eligible: true,
            queue_permitted: true,
            reason: "available",
        });
        await flush();
        assert.equal(
            $("#agent-task-profile").val(),
            "B",
            "a stale default response must not override the member's explicit choice",
        );

        $("#agent-task-request").val("Please handle this").trigger("input");
        // A plain jQuery .trigger("submit") also invokes the native
        // HTMLFormElement.prototype.submit() as a compatibility fallback,
        // which jsdom does not implement. Dispatching the event directly
        // still reaches the composer's real addEventListener("submit")
        // handler without that fallback.
        $("#agent-task-form")[0].dispatchEvent(new dom.window.Event("submit", {cancelable: true}));
        await flush();
        assert.equal(submitted.profile_id, "B");
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function test_as_19_clearing_the_picker_keeps_it_empty() {
    const current_user = {user_id: 1};
    const api = {
        list_profiles: async () => ({
            profiles: [composer_profile("A"), composer_profile("B")],
            count: 2,
        }),
        resolve_selection: async (args) => ({
            selection_source: args.selection_state === "unset" ? "team_default" : "none",
            profile_id: args.selection_state === "unset" ? "A" : null,
            profile_revision: 1,
            selection_revision: 1,
            selection_state: args.selection_state,
            eligible: args.selection_state === "unset",
            queue_permitted: args.selection_state === "unset",
            reason: args.selection_state === "unset" ? "available" : "cleared",
        }),
        create_job: async () => ({job: {id: "job-as-19", status: "queued"}}),
    };
    const {dom, $, out, flush, message_store_data} = build_composer_harness(api, current_user);
    try {
        message_store_data.set(43, {id: 43, locally_echoed: false});
        out.open_for_message(43, "");
        await flush();
        assert.equal(
            $("#agent-task-profile").val(),
            "A",
            "the eligible default should fill the picker",
        );

        $("#agent-task-profile").val("").trigger("change");
        await flush();
        assert.equal($("#agent-task-profile").val(), "");

        $("#agent-task-kind").val("code").trigger("change");
        await flush();
        assert.equal(
            $("#agent-task-profile").val(),
            "",
            "changing task type must not refill the cleared picker",
        );

        $("#agent-task-request").val("Please look into this").trigger("input");
        await flush();
        assert.equal(
            $("#agent-task-profile").val(),
            "",
            "typing a request must not refill the cleared picker",
        );
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function composer_as_20_and_ex_55(test_name) {
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
            return {job: {id: `job-${test_name}`, status: "queued"}};
        },
    };
    const {dom, $, out, flush, message_store_data} = build_composer_harness(api, current_user);
    try {
        message_store_data.set(50, {id: 50, locally_echoed: false});
        out.open_for_message(50, "");
        await flush();
        assert.equal($("#agent-task-profile").val(), "A");

        // The team default changes to B while this draft stays open on A.
        current_default = "B";

        $("#agent-task-request").val("Finish the draft on A").trigger("input");
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
            "an open draft on A must submit A, not the new default",
        );

        message_store_data.set(51, {id: 51, locally_echoed: false});
        out.open_for_message(51, "");
        await flush();
        assert.equal(
            $("#agent-task-profile").val(),
            "B",
            "a new dialog must pick up the new default",
        );
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function test_as_20_new_default_does_not_change_an_open_draft() {
    await composer_as_20_and_ex_55("as-20");
}

async function test_as_21_offline_default_still_queues_and_a_full_queue_blocks_submit() {
    const current_user = {user_id: 1};
    let reason = "runner_offline";
    let eligible = true;
    let submitted_count = 0;
    const api = {
        list_profiles: async () => ({profiles: [composer_profile("A")], count: 1}),
        resolve_selection: async () => ({
            selection_source: "team_default",
            profile_id: "A",
            profile_revision: 1,
            selection_revision: 1,
            selection_state: "unset",
            eligible,
            queue_permitted: eligible,
            reason,
        }),
        async create_job() {
            submitted_count += 1;
            return {job: {id: "job-as-21", status: "queued"}};
        },
    };
    const {dom, $, out, flush, message_store_data} = build_composer_harness(api, current_user);
    try {
        message_store_data.set(60, {id: 60, locally_echoed: false});
        out.open_for_message(60, "");
        await flush();
        assert.equal($("#agent-task-profile").val(), "A", "an offline default is still selected");
        assert.match(
            $("#agent-task-status").text(),
            /The agent's device is offline\. You can create the task now\. It waits until the device connects or until its start deadline passes\./,
        );
        $("#agent-task-request").val("Queue this while offline").trigger("input");
        // A plain jQuery .trigger("submit") also invokes the native
        // HTMLFormElement.prototype.submit() as a compatibility fallback,
        // which jsdom does not implement. Dispatching the event directly
        // still reaches the composer's real addEventListener("submit")
        // handler without that fallback.
        $("#agent-task-form")[0].dispatchEvent(new dom.window.Event("submit", {cancelable: true}));
        await flush();
        assert.equal(
            submitted_count,
            1,
            "an offline but eligible default can still start a queued task",
        );

        reason = "queue_full";
        eligible = false;
        message_store_data.set(61, {id: 61, locally_echoed: false});
        out.open_for_message(61, "");
        await flush();
        assert.equal(
            $("#agent-task-profile").val(),
            "",
            "an ineligible queue_full default is not auto-selected",
        );
        assert.match(
            $("#agent-task-status").text(),
            /This agent has too many tasks that wait\. Try again in a few minutes\./,
        );
        $("#agent-task-profile").val("A").trigger("change");
        $("#agent-task-request").val("Try anyway").trigger("input");
        // A plain jQuery .trigger("submit") also invokes the native
        // HTMLFormElement.prototype.submit() as a compatibility fallback,
        // which jsdom does not implement. Dispatching the event directly
        // still reaches the composer's real addEventListener("submit")
        // handler without that fallback.
        $("#agent-task-form")[0].dispatchEvent(new dom.window.Event("submit", {cancelable: true}));
        await flush();
        assert.equal(submitted_count, 1, "a queue_full agent must not accept a new task");
        assert.match(
            $("#agent-task-status").text(),
            /Task was not created\..*too many tasks that wait/,
        );
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

async function test_as_27_composer_ignores_a_late_response_after_the_user_switches_accounts() {
    const current_user = {user_id: 1};
    const pending = deferred();
    let resolve_calls = 0;
    const api = {
        list_profiles: async () => ({profiles: [composer_profile("A")], count: 1}),
        resolve_selection() {
            resolve_calls += 1;
            return pending.promise;
        },
        create_job: async () => ({job: {id: "job-as-27", status: "queued"}}),
    };
    const {dom, $, out, flush, message_store_data} = build_composer_harness(api, current_user);
    try {
        message_store_data.set(70, {id: 70, locally_echoed: false});
        out.open_for_message(70, "");
        await flush();
        assert.equal(resolve_calls, 1);
        assert.equal($("#agent-task-status").text(), "Checking agent selection…");

        current_user.user_id = 2;
        pending.resolve({
            selection_source: "team_default",
            profile_id: "A",
            profile_revision: 1,
            selection_revision: 1,
            selection_state: "unset",
            eligible: true,
            queue_permitted: true,
            reason: "available",
        });
        await flush();
        assert.equal(
            $("#agent-task-profile").val(),
            "",
            "a response addressed to a switched-away account must not select an agent",
        );
        assert.equal(
            $("#agent-task-status").text(),
            "Checking agent selection…",
            "a response addressed to a switched-away account must not change the notice",
        );
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

void settings_scenarios()
    .then(() => test_as_18_explicit_choice_wins_over_a_late_default_response())
    .then(() => test_as_19_clearing_the_picker_keeps_it_empty())
    .then(() => test_as_20_new_default_does_not_change_an_open_draft())
    .then(() => test_as_21_offline_default_still_queues_and_a_full_queue_blocks_submit())
    .then(() => test_as_27_composer_ignores_a_late_response_after_the_user_switches_accounts())
    .then(() => process.stdout.write("Agent AS evidence regressions passed.\n"))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
