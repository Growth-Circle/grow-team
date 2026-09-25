/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, promise/prefer-await-to-then, promise/always-return, @typescript-eslint/consistent-type-assertions -- This controller uses static jQuery markup and fenced delegated callbacks. */
import $ from "jquery";

import * as api from "./agent_api.ts";
import {$t} from "./i18n.ts";
import * as connections from "./settings_agents_connections.ts";
import {
    type GrantKind,
    advance_draft_revision,
    advance_visit,
    announce,
    begin_editor,
    bind_grant_handlers,
    current,
    editor_is_idle,
    hide_editors,
    line,
    load_choices,
    open_grant_editor,
    pending_creation_keys,
    providers,
    remove_pending_key,
    repositories,
    runners,
    set_session_identity,
    visit,
} from "./settings_agents_core.ts";
import * as devices from "./settings_agents_devices.ts";
import * as directory from "./settings_agents_directory.ts";
import * as team_default from "./settings_agents_team_default.ts";

// The tab switcher, the top-level reset, and the handlers that span more
// than one panel (job list, tab clicks, the shared editor cancel buttons,
// and resolving which resource a "grant-open-*" or "repair-grants" click
// means). Each panel's own render/load/save logic lives in its own
// settings_agents_*.ts file, and every shared DOM helper, freshness
// counter, and the resource-grant editor itself live in
// settings_agents_core.ts.
export type Tab = "directory" | "devices" | "connections" | "default";
let handlers_bound = false;
let visible_tab: Tab = "directory";
let refresh_timer: ReturnType<typeof setTimeout> | undefined;
const refresh_interval_ms = 15000;
export type AgentCreateTaskRequest = {profile_id: string};
let create_task_handler: ((request: AgentCreateTaskRequest) => void) | undefined;

export function register_create_task_handler(
    handler: (request: AgentCreateTaskRequest) => void,
): () => void {
    create_task_handler = handler;
    return () => {
        if (create_task_handler === handler) {
            create_task_handler = undefined;
        }
    };
}

function schedule_refresh(token: number): void {
    if (refresh_timer) {
        clearTimeout(refresh_timer);
    }
    if (!current(token) || !$("#agent-settings").get(0)?.getClientRects().length) {
        return;
    }
    refresh_timer = setTimeout(() => {
        if (!current(token) || !$("#agent-settings").get(0)?.getClientRects().length) {
            return;
        }
        const refresh =
            visible_tab === "directory"
                ? directory.load_profiles()
                : visible_tab === "devices"
                  ? devices.load_runners()
                  : visible_tab === "connections"
                    ? connections.load_providers()
                    : team_default.load_default();
        void refresh.finally(() => {
            if (current(token)) {
                schedule_refresh(token);
            }
        });
    }, refresh_interval_ms);
}
function show_tab(next: Tab): void {
    advance_visit();
    begin_editor("");
    visible_tab = next;
    team_default.clear_default_draft();
    $(".agent-settings-panel").prop("hidden", true);
    $(
        `#agent-${next === "directory" ? "directory" : next === "devices" ? "devices" : next === "connections" ? "connections" : "default"}-panel`,
    ).prop("hidden", false);
    $("[data-agent-tab]").attr("aria-current", "false").removeClass("selected");
    $(`[data-agent-tab='${next}']`).attr("aria-current", "page").addClass("selected");
    announce("");
    if (next === "directory") {
        void directory.load_profiles();
    }
    if (next === "devices") {
        void devices.load_runners();
    }
    if (next === "connections") {
        void connections.load_providers();
    }
    if (next === "default") {
        void team_default.load_default();
    }
    schedule_refresh(visit);
}
export function reset(): void {
    advance_visit();
    if (refresh_timer) {
        clearTimeout(refresh_timer);
    }
    refresh_timer = undefined;
    advance_draft_revision();
    begin_editor("");
    runners.splice(0);
    providers.splice(0);
    repositories.splice(0);
    directory.reset_state();
    devices.reset_state();
    connections.reset_state();
    team_default.reset_state();
    $("#agent-profile-list, #agent-runner-list, #agent-provider-list, #agent-team-default").empty();
    hide_editors();
    announce("");
    $("#agent-provider-credential, #agent-provider-local-ref").val("");
    $("#agent-pairing-preview").empty().prop("hidden", true);
    $("#agent-pairing-added").prop("hidden", true);
    $("#agent-pairing-approve").prop("disabled", true);
    $("#agent-pairing-result").text("");
}

async function load_recent_jobs(): Promise<void> {
    const token = visit;
    try {
        const result = await api.list_jobs();
        if (!current(token)) {
            return;
        }
        const list = $("#agent-recent-jobs").empty();
        line(list, $t({defaultMessage: "Authorized jobs"}), result.count);
        for (const job of result.jobs) {
            const row = $("<div class='agent-card'>").appendTo(list);
            line(row, $t({defaultMessage: "Job"}), `${job.id} · ${job.status} · ${job.job_kind}`);
            $("<button type='button' class='action-button action-button-subtle-neutral'>")
                .attr("data-agent-job-id", job.id)
                .text($t({defaultMessage: "Open job panel"}))
                .appendTo(row);
        }
    } catch {
        if (current(token)) {
            $("#agent-recent-jobs").text($t({defaultMessage: "Job list is unavailable."}));
        }
    }
}

async function recover_pending_creation(key: string, token: number): Promise<void> {
    try {
        const result = await api.recover_profile(key);
        if (!current(token)) {
            return;
        }
        remove_pending_key(key);
        if (editor_is_idle()) {
            announce(
                $t(
                    {
                        defaultMessage:
                            "Recovered saved profile {name}. Review its draft before probing.",
                    },
                    {name: result.profile.name},
                ),
            );
        }
        void directory.load_profiles();
    } catch {
        if (current(token) && editor_is_idle()) {
            announce(
                $t({
                    defaultMessage:
                        "An uncertain profile save can be retried with its original identity.",
                }),
            );
        }
    }
}

export function set_up(): void {
    set_session_identity();
    if (!handlers_bound) {
        bind_handlers();
        handlers_bound = true;
    }
    devices.render_connect_steps();
    show_tab("directory");
    const token = visit;
    void load_choices().then(() => {
        if (!current(token)) {
            return;
        }
        for (const key of pending_creation_keys()) {
            void recover_pending_creation(key, token);
        }
    });
}

function bind_handlers(): void {
    directory.bind_handlers();
    devices.bind_handlers();
    connections.bind_handlers();
    team_default.bind_handlers();
    bind_grant_handlers();
    const root = $(document);
    root.on("click", "[data-agent-tab]", function () {
        show_tab($(this).attr("data-agent-tab") as Tab);
    });
    root.on("click", "#agent-load-jobs", () => {
        void load_recent_jobs();
    });
    root.on("click", "[data-agent-job-id]", function () {
        const id = $(this).attr("data-agent-job-id");
        if (id) {
            window.location.hash = `#agent-jobs/${id}`;
        }
    });
    root.on(
        "click",
        "#agent-profile-cancel, #agent-detail-close, #agent-runner-cancel, #agent-provider-cancel, #agent-repository-cancel",
        () => {
            begin_editor("");
            if (!directory.profile_submitted && directory.profile_key) {
                remove_pending_key(directory.profile_key);
            }
            $("#agent-new-profile").trigger("focus");
        },
    );
    root.on(
        "input change",
        "#agent-profile-form input, #agent-profile-form textarea, #agent-profile-form select, #agent-provider-form input, #agent-provider-form textarea, #agent-provider-form select, #agent-runner-form input, #agent-runner-form select, #agent-repository-form input, #agent-repository-form select",
        () => {
            advance_draft_revision();
        },
    );
    root.on("click", "[data-agent-action]", function () {
        const action = $(this).attr("data-agent-action") ?? "";
        const id = $(this).attr("data-agent-id") ?? "";
        if (action === "repair-devices") {
            show_tab("devices");
            return;
        }
        if (action === "repair-connections") {
            show_tab("connections");
            return;
        }
        if (action === "repair-grants") {
            const profile =
                directory.selected_profile?.id === id
                    ? directory.selected_profile
                    : directory.profiles.find((item) => item.id === id);
            if (profile?.allowed_actions.includes("edit")) {
                open_grant_editor("profile", id, profile.revision, profile.name);
            } else {
                announce(
                    $t({defaultMessage: "Ask the resource owner to grant the missing access."}),
                );
            }
            return;
        }
        if (action === "create-task") {
            if (create_task_handler) {
                create_task_handler({profile_id: id});
            } else {
                announce(
                    $t({
                        defaultMessage:
                            "Task form is unavailable. Open a conversation and try again.",
                    }),
                );
            }
        }
        if (action.startsWith("grant-open-")) {
            const kind = action.slice("grant-open-".length) as GrantKind;
            switch (kind) {
                case "profile": {
                    const profile =
                        directory.selected_profile?.id === id
                            ? directory.selected_profile
                            : directory.profiles.find((item) => item.id === id);
                    if (profile?.allowed_actions.includes("edit")) {
                        open_grant_editor(kind, id, profile.revision, profile.name);
                    }
                    break;
                }
                case "runner": {
                    const runner = runners.find((item) => item.id === id);
                    if (runner?.allowed_actions.includes("edit")) {
                        open_grant_editor(kind, id, runner.revision, runner.name);
                    }
                    break;
                }
                case "provider": {
                    const provider = providers.find((item) => item.id === id);
                    if (provider?.allowed_actions.includes("edit")) {
                        open_grant_editor(kind, id, provider.config_version, provider.name);
                    }
                    break;
                }
                case "repository": {
                    const repository = repositories.find((item) => item.id === id);
                    if (
                        repository?.allowed_actions.includes("manage") &&
                        repository.policy_version
                    ) {
                        open_grant_editor(
                            kind,
                            id,
                            repository.policy_version,
                            repository.workspace_alias,
                        );
                    }
                    break;
                }
            }
        }
    });
}
