"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const handlebars = require("handlebars");
const {JSDOM} = require("jsdom");
const ts = require("typescript");

function deferred() {
    let resolve_request;
    let reject_request;
    const promise = new Promise((resolve, reject) => {
        resolve_request = resolve;
        reject_request = reject;
    });
    return {promise, resolve: resolve_request, reject: reject_request};
}

async function main() {
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
    let key_id = 0;
    const runner = (id) => ({
        id,
        name: `Runner ${id}`,
        owner_id: 1,
        host_kind: "workstation",
        observed_presence: "online",
        metadata_revision: 1,
        revision: 1,
        catalog_revision: 1,
        allowed_actions: ["edit"],
        catalog_summary: {
            revision: 1,
            reported_at: null,
            adapters: [{id: "grow", version: "1", auth_state: "ready"}],
            sandboxes: [{alias: "safe"}],
        },
    });
    const runners = [runner("ra"), runner("rb")];
    const profile = (id) => ({
        id,
        name: `Profile ${id}`,
        description: "",
        runner_id: "ra",
        provider_id: null,
        repository_id: null,
        default_mode: "answer",
        mode: "acp",
        adapter_id: "grow",
        adapter_version: "1",
        revision: 1,
        metadata_revision: 1,
        owner: {id: 1, name: "Owner"},
        runner: runners[0],
        provider: null,
        repository: null,
        access: {complete: true, runner: true, provider: true, repository: true},
        desired_state: "enabled",
        readiness_state: "ready",
        readiness_revision: 1,
        allowed_actions: ["edit"],
        configuration: null,
    });
    const profiles = [profile("a"), profile("b")];
    let default_server_revision = 1;
    const provider = (id) => ({
        id,
        name: `Provider ${id}`,
        owner_id: 1,
        runner_id: "ra",
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
    });
    const api = {
        update_runner_metadata: async () => ({}),
        update_team_default: async () => ({}),
        create_profile: async () => ({}),
        update_profile: async () => ({}),
        attach_channel: async () => ({}),
        create_repository: async () => ({}),
        profile_network_choice: () => ({network: {targets: []}}),
        list_profiles: async () => ({profiles, count: profiles.length}),
        list_runners: async () => ({runners, count: 2}),
        list_providers: async () => ({providers: [provider("a"), provider("b")], count: 2}),
        list_repositories: async () => ({repositories: [], count: 0}),
        list_grants: async () => ({grants: [], count: 0}),
        get_team_default: async () => ({
            default: {
                profile: profiles[0],
                selection_revision: default_server_revision,
                has_default: true,
                allowed_actions: ["clear", "set"],
            },
        }),
        get_profile: async (id) => ({profile: profile(id), setup: null, attachments: []}),
        get_provider: async (id) => ({provider: provider(id)}),
        recover_profile: async () => ({profile: profile("recovered")}),
    };
    const out = {};
    const source = fs.readFileSync(path.join(__dirname, "../src/settings_agents.ts"), "utf8");
    vm.runInNewContext(
        ts.transpileModule(source, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                esModuleInterop: true,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText,
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
                    return {
                        new_client_key() {
                            key_id += 1;
                            return `key-${key_id}`;
                        },
                    };
                }
                if (name === "./state_data.ts") {
                    return {current_user: {user_id: 1}};
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
                        get_unsorted_subs_with_content_access: () => [
                            {stream_id: 42, name: "Denmark"},
                        ],
                        get_sub_by_id: () => ({name: "Denmark"}),
                    };
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
        },
    );
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
    async function fresh(tab = "directory", clear = true) {
        out.reset();
        if (clear) {
            dom.window.sessionStorage.clear();
        }
        out.set_up();
        await flush();
        if (tab !== "directory") {
            $(`[data-agent-tab="${tab}"]`).trigger("click");
            await flush();
        }
    }
    try {
        await fresh();
        const a = deferred();
        const b = deferred();
        api.get_profile = (id) => (id === "a" ? a : b).promise;
        click("profile-edit", "a");
        click("profile-edit", "b");
        b.resolve({profile: profile("b")});
        await flush();
        $("#agent-profile-name").val("New B draft").trigger("input");
        a.resolve({profile: profile("a")});
        await flush();
        assert.equal($("#agent-profile-name").val(), "New B draft");
        await fresh("connections");
        const old_provider = deferred();
        api.get_provider = () => old_provider.promise;
        click("provider-edit", "a");
        $("#agent-new-provider").trigger("click");
        $("#agent-provider-cancel").trigger("click");
        old_provider.resolve({provider: provider("a")});
        await flush();
        assert.equal($("#agent-provider-form").prop("hidden"), true);
        await fresh("devices");
        const old_save = deferred();
        api.update_runner_metadata = () => old_save.promise;
        click("runner-edit", "ra");
        $("#agent-runner-form").trigger("submit");
        click("runner-edit", "rb");
        old_save.resolve({});
        await flush();
        assert.equal($("#agent-runner-form").prop("hidden"), false);
        assert.equal($("#agent-runner-name").val(), "Runner rb");
        await fresh("default");
        $("#agent-default-choice").val("b").trigger("change");
        [...timers.values()].at(-1)();
        await flush();
        assert.equal($("#agent-default-choice").val(), "b");
        $("#agent-default-choice").val("").trigger("change");
        [...timers.values()].at(-1)();
        await flush();
        assert.equal($("#agent-default-choice").val(), "");
        const default_save = deferred();
        api.update_team_default = () => default_save.promise;
        $("#agent-default-choice").val("b").trigger("change");
        $("#agent-default-form").trigger("submit");
        await flush();
        $("#agent-default-choice").val("a").trigger("change");
        default_server_revision = 2;
        default_save.resolve({});
        await flush();
        assert.equal($("#agent-default-choice").val(), "a");
        assert.match($("#agent-team-default").text(), /Conflict/);
        assert.match($("#agent-settings-status").text(), /newer selection remains unsaved/);
        await fresh();
        const first = deferred();
        api.create_profile = () => first.promise;
        $("#agent-new-profile").trigger("click");
        await flush();
        $("#agent-profile-name").val("First").trigger("input");
        $("#agent-profile-form").trigger("submit");
        await flush();
        $("#agent-new-profile").trigger("click");
        await flush();
        const pointer = "grow-agent-profile:https://realm.test:1";
        const second_key = dom.window.sessionStorage.getItem(pointer);
        assert.ok(second_key);
        first.resolve({profile: profile("saved-first")});
        await flush();
        assert.equal(dom.window.sessionStorage.getItem(pointer), second_key);
        const second = deferred();
        api.create_profile = () => second.promise;
        $("#agent-profile-name").val("Second").trigger("input");
        $("#agent-profile-form").trigger("submit");
        await flush();
        api.recover_profile = async () => {
            throw new Error("recovery pending");
        };
        second.reject(new Error("lost response"));
        await flush();
        assert.equal(dom.window.sessionStorage.getItem(pointer), second_key);
        api.recover_profile = async (key) => ({
            profile: profile(key === second_key ? "saved-second" : "wrong"),
        });
        await fresh("directory", false);
        assert.equal(dom.window.sessionStorage.getItem(pointer), null);
        await fresh();
        const failed_old = deferred();
        const recovered_old = deferred();
        api.create_profile = () => failed_old.promise;
        api.recover_profile = () => recovered_old.promise;
        $("#agent-new-profile").trigger("click");
        await flush();
        $("#agent-profile-name").val("Recovered old").trigger("input");
        $("#agent-profile-form").trigger("submit");
        await flush();
        $("#agent-new-profile").trigger("click");
        await flush();
        const newer_key = dom.window.sessionStorage.getItem(pointer);
        assert.ok(newer_key);
        failed_old.reject(new Error("lost response"));
        await flush();
        recovered_old.resolve({profile: profile("recovered-old")});
        await flush();
        assert.equal(dom.window.sessionStorage.getItem(pointer), newer_key);
        api.get_profile = async (id) => ({profile: profile(id), setup: null, attachments: []});
        await fresh();
        const created = deferred();
        api.create_profile = () => created.promise;
        $("#agent-new-profile").trigger("click");
        await flush();
        $("#agent-profile-name").val("Initial").trigger("input");
        $("#agent-profile-form").trigger("submit");
        await flush();
        $("#agent-profile-name").val("Newer draft").trigger("input");
        created.resolve({profile: profile("canonical")});
        await flush();
        assert.equal($("#agent-profile-name").val(), "Newer draft");
        const update_revisions = [];
        api.update_profile = async (id, payload) => {
            assert.equal(id, "canonical");
            update_revisions.push([payload.expected_revision, payload.expected_metadata_revision]);
            return {
                profile: {
                    ...profile("canonical"),
                    revision: update_revisions.length + 1,
                    metadata_revision: update_revisions.length + 1,
                },
            };
        };
        $("#agent-profile-form").trigger("submit");
        await flush();
        assert.match($("#agent-profile-result").text(), /Draft saved/);
        $("#agent-profile-name").val("Third draft").trigger("input");
        $("#agent-profile-form").trigger("submit");
        await flush();
        assert.deepEqual(update_revisions, [
            [1, 1],
            [2, 2],
        ]);
        await fresh();
        const lost_create = deferred();
        const recovered_create = deferred();
        api.create_profile = () => lost_create.promise;
        api.recover_profile = () => recovered_create.promise;
        const recovery_revisions = [];
        api.update_profile = async (id, payload) => {
            assert.equal(id, "recovered-canonical");
            recovery_revisions.push([
                payload.expected_revision,
                payload.expected_metadata_revision,
            ]);
            return {
                profile: {
                    ...profile("recovered-canonical"),
                    revision: recovery_revisions.length + 1,
                    metadata_revision: recovery_revisions.length + 1,
                },
            };
        };
        $("#agent-new-profile").trigger("click");
        await flush();
        $("#agent-profile-name").val("Recover initial").trigger("input");
        $("#agent-profile-form").trigger("submit");
        await flush();
        $("#agent-profile-name").val("Recover newer").trigger("input");
        lost_create.reject(new Error("response lost"));
        await flush();
        recovered_create.resolve({profile: profile("recovered-canonical")});
        await flush();
        assert.equal($("#agent-profile-name").val(), "Recover newer");
        $("#agent-profile-form").trigger("submit");
        await flush();
        assert.match($("#agent-profile-result").text(), /Draft saved/);
        $("#agent-profile-name").val("Recover third").trigger("input");
        $("#agent-profile-form").trigger("submit");
        await flush();
        assert.deepEqual(recovery_revisions, [
            [1, 1],
            [2, 2],
        ]);
        await fresh();
        click("profile-detail", "a");
        await flush();
        const $channel_option = $("#agent-attach-stream option").filter(
            (_, item) => $(item).text() === "Denmark",
        );
        assert.equal($channel_option.length, 1);
        assert.equal($("#agent-attach-stream").attr("type"), undefined);
        const first_attachment = deferred();
        const attachments = [];
        api.attach_channel = (id, stream_id, revision) => {
            attachments.push({id, stream_id, revision});
            return first_attachment.promise;
        };
        $("#agent-attach-stream").val($channel_option.val()).trigger("change");
        $("#agent-attach-form").trigger("submit");
        await flush();
        assert.deepEqual(attachments, [{id: "a", stream_id: 42, revision: 1}]);
        click("profile-edit", "b");
        await flush();
        $("#agent-profile-name").val("B newer").trigger("input");
        first_attachment.resolve({});
        await flush();
        assert.equal($("#agent-profile-form").prop("hidden"), false);
        assert.equal($("#agent-profile-name").val(), "B newer");
        assert.equal($("#agent-profile-detail").prop("hidden"), true);
        await fresh();
        click("profile-detail", "a");
        await flush();
        const closed_attachment = deferred();
        api.attach_channel = () => closed_attachment.promise;
        $("#agent-attach-stream").val("42");
        $("#agent-attach-form").trigger("submit");
        await flush();
        $("#agent-detail-close").trigger("click");
        closed_attachment.resolve({});
        await flush();
        assert.equal($("#agent-profile-detail").prop("hidden"), true);
        await fresh();
        click("profile-detail", "a");
        await flush();
        const failed_attachment = deferred();
        api.attach_channel = () => failed_attachment.promise;
        $("#agent-attach-stream").val("42");
        $("#agent-attach-form").trigger("submit");
        await flush();
        click("profile-edit", "b");
        await flush();
        $("#agent-profile-name").val("B after failure").trigger("input");
        failed_attachment.reject(new Error("attachment failed"));
        await flush();
        assert.equal($("#agent-profile-name").val(), "B after failure");
        assert.doesNotMatch($("#agent-settings-status").text(), /attachment failed/i);
        await fresh();
        click("profile-detail", "a");
        await flush();
        const navigated_attachment = deferred();
        api.attach_channel = () => navigated_attachment.promise;
        $("#agent-attach-stream").val("42");
        $("#agent-attach-form").trigger("submit");
        await flush();
        $("[data-agent-tab='devices']").trigger("click");
        await flush();
        navigated_attachment.reject(new Error("attachment failed"));
        await flush();
        assert.doesNotMatch($("#agent-settings-status").text(), /attachment failed/i);
        await fresh("devices");
        click("repository-new", "ra");
        const repository_save = deferred();
        api.create_repository = () => repository_save.promise;
        $("#agent-repository-alias").val("first").trigger("input");
        $("#agent-repository-form").trigger("submit");
        await flush();
        $("#agent-repository-alias").val("newer").trigger("input");
        repository_save.resolve({});
        await flush();
        assert.equal($("#agent-repository-form").prop("hidden"), false);
        assert.equal($("#agent-repository-alias").val(), "newer");
        assert.match($("#agent-repository-result").text(), /newer.*unsaved/i);
        assert.equal(
            $("#agent-repository-result").closest("form").attr("id"),
            "agent-repository-form",
        );
        assert.equal($("#agent-repository-result").closest("form").prop("hidden"), false);
        assert.equal($("#agent-repository-result").attr("role"), "status");
        const rejected_repository = deferred();
        api.create_repository = () => rejected_repository.promise;
        $("#agent-repository-form").trigger("submit");
        await flush();
        rejected_repository.reject(new Error("registration rejected"));
        await flush();
        assert.match($("#agent-repository-result").text(), /registration failed/i);
        assert.equal(
            $("#agent-repository-result").closest("form").attr("id"),
            "agent-repository-form",
        );
        assert.equal($("#agent-repository-result").closest("form").prop("hidden"), false);
        click("repository-new", "ra");
        assert.equal($("#agent-repository-result").text(), "");
    } finally {
        out.reset();
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

void main()
    .then(() => process.stdout.write("Agent settings delegated-handler regressions passed.\n"))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
