/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, promise/prefer-await-to-then, promise/always-return, promise/no-nesting, @typescript-eslint/consistent-type-assertions, no-alert -- This controller uses static jQuery markup and fenced delegated callbacks. */
import $ from "jquery";

import * as api from "./agent_api.ts";
import {new_client_key} from "./agent_ui_state.ts";
import * as people from "./people.ts";
import {current_user} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import * as user_groups from "./user_groups.ts";

const default_network = {
    targets: [],
    public_https_only: true,
    block_metadata: true,
    cross_origin_authorization: false,
    project_network: false,
};
const steps = ["Identity", "Runner", "Runtime", "Work", "Access", "Review"];
type Tab = "directory" | "devices" | "connections" | "default";
let visit = 0;
let draft_revision = 0;
let form_visit = 0;
let editor_kind = "";
let editor_target = "";
type GrantKind = "profile" | "runner" | "provider" | "repository";
let grant_target: {kind: GrantKind; id: string; revision: number; label: string} | undefined;
let default_draft_revision = 0;
let default_dirty = false;
let default_expected_revision: number | undefined;
let directory_revision = 0;
let directory_request = 0;
let runner_request = 0;
let provider_request = 0;
let default_request = 0;
let profile_submitted = false;
let step = 0;
let profile_offset = 0;
let runner_offset = 0;
let provider_offset = 0;
let selected_profile: api.AgentProfile | undefined;
let selected_runner: api.AgentRunner | undefined;
let selected_provider: api.AgentProvider | undefined;
let profile_key = "";
let profiles: api.AgentProfile[] = [];
let runners: api.AgentRunner[] = [];
let providers: api.AgentProvider[] = [];
let repositories: api.AgentRepository[] = [];
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

function identity(): string {
    return `${window.location.origin}:${current_user.user_id}`;
}
function current(token: number): boolean {
    return token === visit && identity() === session_identity;
}
let session_identity = "";
function announce(message: string): void {
    $("#agent-settings-status").text(message);
}
function value(id: string): string {
    return String($(id).val() ?? "").trim();
}
function number(id: string): number {
    return Number(value(id));
}
function option(select: JQuery, id: string, name: string): void {
    $("<option>").val(id).text(name).appendTo(select);
}
function line(parent: JQuery, label: string, value: unknown): void {
    const printable =
        typeof value === "string" || typeof value === "number" || typeof value === "boolean"
            ? String(value)
            : "Unknown";
    $("<p>").text(`${label}: ${printable}`).appendTo(parent);
}
function button(parent: JQuery, label: string, action: string, id: string): void {
    $("<button type='button' class='action-button action-button-subtle-neutral'>")
        .text(label)
        .attr("data-agent-action", action)
        .attr("data-agent-id", id)
        .appendTo(parent);
}
const repository_grant_actions = [
    {id: "repository.read", label: "Read repository"},
    {id: "repository.edit", label: "Edit repository"},
    {id: "checks.run", label: "Run checks"},
    {id: "shell.run", label: "Run shell"},
    {id: "dependencies.install", label: "Install dependencies"},
    {id: "git.commit", label: "Create commits"},
    {id: "git.push", label: "Push changes"},
    {id: "git.draft_pr", label: "Create draft pull requests"},
];
// The server checks each job action against the profile grant as well as
// the repository grant, so a profile grant must be able to carry them all.
const grant_actions: Record<GrantKind, {id: string; label: string}[]> = {
    profile: [
        {id: "profile.use", label: "Use profile"},
        {id: "context.read", label: "Read the conversation"},
        ...repository_grant_actions,
        {id: "profile.manage", label: "Manage profile"},
    ],
    runner: [{id: "runner.use", label: "Use device"}],
    provider: [{id: "provider.use", label: "Use model connection"}],
    repository: repository_grant_actions,
};
function grant_principal_label(principal: unknown): string {
    if (!principal || typeof principal !== "object") {
        return "Restricted principal";
    }
    const row = principal as Record<string, unknown>;
    if (row["kind"] === "current_user") {
        return "You (other audience details are private)";
    }
    if (row["kind"] === "user" && typeof row["user_id"] === "number") {
        return people.maybe_get_user_by_id(row["user_id"])?.full_name ?? "Restricted user";
    }
    if (row["kind"] === "group" && typeof row["group_id"] === "number") {
        return (
            user_groups.get_realm_user_groups().find((item) => item.id === row["group_id"])?.name ??
            "Restricted group"
        );
    }
    return "Restricted principal";
}
function grant_scope_label(scope: unknown, restricted: boolean): string {
    if (restricted) {
        return "Restricted conversation";
    }
    if (!scope || typeof scope !== "object") {
        return "Any authorized conversation";
    }
    const row = scope as Record<string, unknown>;
    if (row["kind"] === "stream" && typeof row["stream_id"] === "number") {
        const name = stream_data.get_sub_by_id(row["stream_id"])?.name ?? "Restricted channel";
        return `${name}${typeof row["topic"] === "string" && row["topic"] ? ` · ${row["topic"]}` : ""}`;
    }
    if (row["kind"] === "direct" && Array.isArray(row["participant_user_ids"])) {
        return `Direct message with ${row["participant_user_ids"].map((id: unknown) => (typeof id === "number" ? (people.maybe_get_user_by_id(id)?.full_name ?? "Restricted user") : "Restricted user")).join(", ")}`;
    }
    return "Restricted conversation";
}
function render_grants(box: JQuery, result: Awaited<ReturnType<typeof api.list_grants>>): void {
    box.empty();
    line(box, "Recorded grants", result.count);
    if (result.count > result.grants.length) {
        line(box, "History", "Only the first 50 authorized grants are shown.");
    }
    for (const grant of result.grants) {
        const row = $("<div class='agent-card'>").appendTo(box);
        line(row, "Principal", grant_principal_label(grant.principal));
        line(row, "Actions", grant.actions.join(", "));
        line(row, "Conversation", grant_scope_label(grant.scope, grant.scope_restricted));
        if (grant.repository_restricted) {
            const name = repositories.find(
                (item) => item.id === grant.repository_id,
            )?.workspace_alias;
            line(row, "Repository restriction", name ?? "Restricted repository");
        }
        line(row, "Expiry", grant.expires_at ?? "No expiry");
        line(row, "Status", grant.revoked ? "Revoked" : "Active");
        if (
            box.attr("id") === "agent-resource-grants" &&
            grant.allowed_actions.includes("revoke")
        ) {
            button(row, "Revoke grant", "grant-revoke", grant.id);
        }
    }
}
async function load_grants(
    kind: GrantKind,
    id: string,
    box: JQuery,
    editor?: number,
    owner_kind = "grant",
): Promise<void> {
    const token = visit;
    try {
        const result = await api.list_grants(kind, id);
        if (
            !current(token) ||
            (editor !== undefined &&
                !owns_editor(
                    token,
                    editor,
                    owner_kind,
                    owner_kind === "grant" ? `${kind}:${id}` : id,
                ))
        ) {
            return;
        }
        render_grants(box, result);
    } catch {
        if (
            current(token) &&
            (editor === undefined ||
                owns_editor(
                    token,
                    editor,
                    owner_kind,
                    owner_kind === "grant" ? `${kind}:${id}` : id,
                ))
        ) {
            box.text("Grant status is unknown.");
        }
    }
}
function open_grant_editor(kind: GrantKind, id: string, revision: number, label: string): void {
    const editor = begin_editor("grant", `${kind}:${id}`);
    grant_target = {kind, id, revision, label};
    const box = $("#agent-grant-editor").empty().prop("hidden", false);
    $("<h4>").text(`Share ${label}`).appendTo(box);
    $("<p>")
        .text(
            "Each resource owner grants only their own resource. A grant does not give access to other dependencies.",
        )
        .appendTo(box);
    const form = $("<form id='agent-resource-grant-form' class='agent-inline-form'>").appendTo(box);
    $("<label for='agent-grant-principal-kind' class='settings-field-label'>")
        .text("Audience type")
        .appendTo(form);
    const principal_kind = $(
        "<select id='agent-grant-principal-kind' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    option(principal_kind, "user", "Person");
    option(principal_kind, "group", "Group");
    $("<label for='agent-grant-principal' class='settings-field-label'>")
        .text("Audience")
        .appendTo(form);
    const principal = $(
        "<select id='agent-grant-principal' required class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    for (const user of people.get_realm_active_human_users()) {
        option(principal, String(user.user_id), user.full_name);
    }
    $("<label for='agent-grant-actions' class='settings-field-label'>")
        .text("Allowed actions")
        .appendTo(form);
    const actions = $(
        "<select id='agent-grant-actions' multiple required size='5' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    for (const item of grant_actions[kind]) {
        option(actions, item.id, item.label);
    }
    // Every task reads its conversation, so a profile grant for use needs both.
    actions.val(
        kind === "profile" ? ["profile.use", "context.read"] : [grant_actions[kind][0]!.id],
    );
    $("<label for='agent-grant-scope-kind' class='settings-field-label'>")
        .text("Conversation restriction")
        .appendTo(form);
    const scope = $(
        "<select id='agent-grant-scope-kind' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    option(scope, "", "Any authorized conversation");
    option(scope, "stream", "Channel");
    option(scope, "direct", "Direct message");
    $("<label for='agent-grant-channel' class='settings-field-label'>")
        .text("Channel")
        .appendTo(form);
    const channel = $(
        "<select id='agent-grant-channel' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    for (const sub of stream_data.get_unsorted_subs_with_content_access()) {
        option(channel, String(sub.stream_id), sub.name);
    }
    $("<label for='agent-grant-topic' class='settings-field-label'>")
        .text("Topic (optional)")
        .appendTo(form);
    $("<input id='agent-grant-topic' maxlength='200' class='settings_text_input'>").appendTo(form);
    $("<label for='agent-grant-dm' class='settings-field-label'>")
        .text("Direct message participants")
        .appendTo(form);
    const dm = $(
        "<select id='agent-grant-dm' multiple size='5' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    for (const user of people.get_realm_active_human_users()) {
        option(dm, String(user.user_id), user.full_name);
    }
    if (kind === "profile") {
        $("<label for='agent-grant-repository' class='settings-field-label'>")
            .text("Repository restriction")
            .appendTo(form);
        const repository = $(
            "<select id='agent-grant-repository' class='settings_select bootstrap-focus-style'>",
        ).appendTo(form);
        option(repository, "", "No repository restriction");
        for (const item of repositories) {
            option(repository, item.id, item.workspace_alias);
        }
    }
    $("<label for='agent-grant-expiry' class='settings-field-label'>")
        .text("Expiry (optional)")
        .appendTo(form);
    $("<input id='agent-grant-expiry' type='datetime-local' class='settings_text_input'>").appendTo(
        form,
    );
    $("<button type='submit' class='action-button action-button-solid-brand'>")
        .text("Create grant")
        .appendTo(form);
    $(
        "<button type='button' id='agent-grant-close' class='action-button action-button-subtle-neutral'>",
    )
        .text("Close sharing")
        .appendTo(box);
    $("<p id='agent-grant-result' role='status'>").appendTo(box);
    $("<div id='agent-resource-grants'>").appendTo(box);
    void load_grants(kind, id, $("#agent-resource-grants"), editor);
    form.find("select").first().trigger("focus");
}
function failed(token: number, message: string): () => void {
    return () => {
        if (current(token)) {
            announce(message);
        }
    };
}
function hide_editors(): void {
    $(
        "#agent-profile-form, #agent-profile-detail, #agent-runner-form, #agent-repository-form, #agent-provider-form, #agent-grant-editor",
    ).prop("hidden", true);
}
function begin_editor(kind: string, target = ""): number {
    form_visit += 1;
    draft_revision += 1;
    editor_kind = kind;
    editor_target = target;
    hide_editors();
    return form_visit;
}
function owns_editor(token: number, editor: number, kind: string, target = ""): boolean {
    return (
        current(token) && form_visit === editor && editor_kind === kind && editor_target === target
    );
}
function remove_pending_key(key: string): void {
    const storage = pending_key();
    if (sessionStorage.getItem(storage) === key) {
        sessionStorage.removeItem(storage);
    }
    const keys = pending_creation_keys().filter((item) => item !== key);
    sessionStorage.setItem(`${storage}:submitted`, JSON.stringify(keys));
}
function pending_creation_keys(): string[] {
    try {
        const data: unknown = JSON.parse(
            sessionStorage.getItem(`${pending_key()}:submitted`) ?? "[]",
        );
        return Array.isArray(data)
            ? data.filter((item): item is string => typeof item === "string")
            : [];
    } catch {
        return [];
    }
}
function register_pending_key(key: string): void {
    sessionStorage.setItem(
        `${pending_key()}:submitted`,
        JSON.stringify([...new Set([...pending_creation_keys(), key])]),
    );
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
                ? load_profiles()
                : visible_tab === "devices"
                  ? load_runners()
                  : visible_tab === "connections"
                    ? load_providers()
                    : load_default();
        void refresh.finally(() => {
            if (current(token)) {
                schedule_refresh(token);
            }
        });
    }, refresh_interval_ms);
}
function show_tab(next: Tab): void {
    visit += 1;
    begin_editor("");
    visible_tab = next;
    default_dirty = false;
    default_expected_revision = undefined;
    $(".agent-settings-panel").prop("hidden", true);
    $(
        `#agent-${next === "directory" ? "directory" : next === "devices" ? "devices" : next === "connections" ? "connections" : "default"}-panel`,
    ).prop("hidden", false);
    $("[data-agent-tab]").attr("aria-current", "false").removeClass("selected");
    $(`[data-agent-tab='${next}']`).attr("aria-current", "page").addClass("selected");
    announce("");
    if (next === "directory") {
        void load_profiles();
    }
    if (next === "devices") {
        void load_runners();
    }
    if (next === "connections") {
        void load_providers();
    }
    if (next === "default") {
        void load_default();
    }
    schedule_refresh(visit);
}
function runner_label(runner: api.AgentRunner | null): string {
    if (!runner) {
        return "Device unavailable";
    }
    return `${runner.name} · ${runner.host_kind} · ${runner.observed_presence}`;
}
function provider_location(profile: api.AgentProfile): string {
    const provider = profile.provider;
    if (!provider) {
        return "No model connection";
    }
    // A shared provider never discloses an owner endpoint.
    return provider.base_url
        ? `${provider.name} · ${provider.base_url}`
        : `${provider.name} · endpoint private`;
}
function render_profiles(count: number): void {
    const list = $("#agent-profile-list").empty();
    $("#agent-directory-count").text(
        `${count} authorized profiles · page ${Math.floor(profile_offset / 20) + 1}`,
    );
    $("#agent-directory-prev").prop("disabled", profile_offset === 0);
    $("#agent-directory-next").prop("disabled", profile_offset + profiles.length >= count);
    if (profiles.length === 0) {
        $("<p>").text("No profiles match these filters.").appendTo(list);
    }
    for (const profile of profiles) {
        const card = $("<article class='agent-card'>").appendTo(list);
        $("<h4>").text(profile.name).appendTo(card);
        line(card, "Owner", profile.owner.name);
        line(card, "Profile state", profile.desired_state);
        line(card, "Readiness", profile.readiness_state);
        line(card, "Runner presence", profile.runner?.observed_presence ?? "unknown");
        line(card, "Declared device category", profile.runner?.host_kind ?? "unknown");
        line(card, "Tool runner", runner_label(profile.runner));
        line(card, "Model location", provider_location(profile));
        line(card, "Access", profile.access.complete ? "Complete" : "Partial");
        const controls = $("<div class='agent-actions'>").appendTo(card);
        button(controls, "Details", "profile-detail", profile.id);
        if (profile.access.complete && profile.desired_state === "enabled") {
            button(controls, "Create task", "create-task", profile.id);
        }
        if (profile.allowed_actions.includes("edit")) {
            button(controls, "Edit", "profile-edit", profile.id);
        }
        if (profile.allowed_actions.includes("pause") && profile.desired_state === "enabled") {
            button(controls, "Pause", "profile-pause", profile.id);
        }
        if (profile.allowed_actions.includes("archive")) {
            button(controls, "Archive", "profile-archive", profile.id);
        }
    }
}
async function load_profiles(): Promise<void> {
    const token = visit;
    directory_request += 1;
    const request = directory_request;
    const filter_revision = directory_revision;
    const filters = {
        offset: profile_offset,
        limit: 20,
        ownership: value("#agent-ownership") as "all" | "mine" | "shared",
        access: value("#agent-access") as "all" | "complete" | "partial",
        host_kind: value("#agent-host-filter") as "all" | "workstation" | "server" | "unknown",
        search: value("#agent-search"),
    };
    try {
        const result = await api.list_profiles(filters);
        if (
            !current(token) ||
            filter_revision !== directory_revision ||
            request !== directory_request
        ) {
            return;
        }
        profiles = result.profiles;
        render_profiles(result.count);
    } catch {
        if (
            current(token) &&
            filter_revision === directory_revision &&
            request === directory_request
        ) {
            profiles = [];
            $("#agent-profile-list").empty();
            announce("Profile status is unknown. Retry the directory.");
        }
    }
}
function catalog(runner: api.AgentRunner | undefined): api.AgentRunner["catalog_summary"] {
    return runner?.catalog_summary ?? {revision: 0, reported_at: null, adapters: [], sandboxes: []};
}
function update_runtime_choices(clear = true): void {
    const runner = runners.find((item) => item.id === value("#agent-profile-runner"));
    const adapter = $("#agent-profile-adapter").empty();
    const sandbox = $("#agent-profile-sandbox").empty();
    const provider = $("#agent-profile-provider").empty();
    const repository = $("#agent-profile-repository").empty();
    option(provider, "", "No model connection");
    option(repository, "", "No repository");
    for (const item of catalog(runner).adapters) {
        option(
            adapter,
            `${item.id}@${item.version}`,
            `${item.id} ${item.version} · ${item.auth_state}`,
        );
    }
    for (const item of catalog(runner).sandboxes) {
        option(sandbox, item.alias, item.alias);
    }
    for (const item of providers.filter(
        (row) => row.runner_id === runner?.id && !row.disabled_at,
    )) {
        option(provider, item.id, `${item.name} · ${item.model_id}`);
    }
    for (const item of repositories.filter(
        (row) => row.runner_id === runner?.id && !row.disabled_at,
    )) {
        option(repository, item.id, item.workspace_alias);
    }
    if (clear) {
        provider.val("");
        repository.val("");
        $("#agent-profile-mode").val("acp");
    }
    $("#agent-profile-network-note").text(
        "A shared connection copies its owner's approved network policy into this profile when saved. Later connection changes do not change this saved policy. Localhost refers to the selected runner.",
    );
}
function update_step(): void {
    $("#agent-profile-form [data-agent-step]").prop("hidden", true);
    $(`#agent-profile-form [data-agent-step='${step}']`).prop("hidden", false);
    $("#agent-profile-step-label").text(`Step ${step + 1} of ${steps.length}: ${steps[step]}`);
    $("#agent-profile-back").prop("disabled", step === 0);
    $("#agent-profile-next").prop("hidden", step === steps.length - 1);
    $("#agent-profile-save").prop("hidden", step !== steps.length - 1);
    if (step === steps.length - 1) {
        const box = $("#agent-profile-review").empty();
        const runner = runners.find((item) => item.id === value("#agent-profile-runner"));
        const provider = providers.find((item) => item.id === value("#agent-profile-provider"));
        const repository = repositories.find(
            (item) => item.id === value("#agent-profile-repository"),
        );
        const payload = profile_payload();
        const actions = payload["actions"] as string[];
        line(box, "Profile", value("#agent-profile-name"));
        line(
            box,
            "Device",
            runner
                ? `${runner.name} · owner ${people.maybe_get_user_by_id(runner.owner_id)?.full_name ?? "Authorized owner"}`
                : "Select a device",
        );
        line(box, "Adapter", value("#agent-profile-adapter"));
        line(box, "Sandbox", value("#agent-profile-sandbox"));
        line(box, "Runtime mode", value("#agent-profile-mode"));
        line(box, "Default task", value("#agent-profile-default-mode"));
        line(
            box,
            "Model",
            provider
                ? `${provider.name} · ${provider.model_id} · owner ${people.maybe_get_user_by_id(provider.owner_id)?.full_name ?? "Authorized owner"}`
                : "No model connection",
        );
        line(box, "Data sent to model", provider?.data_scope.join(", ") ?? "No model data scope");
        line(
            box,
            "Repository",
            repository
                ? `${repository.workspace_alias} · owner ${people.maybe_get_user_by_id(repository.owner_id)?.full_name ?? "Authorized owner"}`
                : "None",
        );
        line(
            box,
            "Required checks",
            repository?.required_checks?.length
                ? `${repository.required_checks.length} configured by repository owner`
                : "No visible required checks",
        );
        line(box, "Tools and actions", actions.length > 0 ? actions.join(", ") : "None selected");
        line(
            box,
            "Budget",
            `${number("#agent-profile-active-seconds")} active seconds · ${number("#agent-profile-input-tokens")} input tokens · ${number("#agent-profile-output-tokens")} output tokens`,
        );
        line(box, "Hard cost cap", payload["hard_cost_cap"] === true ? "Enabled" : "Not enabled");
        const capabilities = provider?.capabilities;
        const report =
            capabilities && typeof capabilities === "object"
                ? (capabilities as Record<string, unknown>)
                : {};
        line(
            box,
            "Capabilities not yet tested",
            !provider ||
                report["chat_ready"] !== true ||
                (value("#agent-profile-default-mode") === "code" && report["code_ready"] !== true)
                ? "Save a draft, then run a probe before enable."
                : "A new profile revision still needs its own probe before enable.",
        );
    }
}
function pending_key(): string {
    return `grow-agent-profile:${identity()}`;
}
function open_profile(
    profile?: api.AgentProfile,
    editor = begin_editor("profile", profile?.id ?? ""),
): void {
    if (!owns_editor(visit, editor, "profile", profile?.id ?? "")) {
        return;
    }
    profile_submitted = false;
    selected_profile = profile;
    step = 0;
    $("#agent-profile-form").trigger("reset").prop("hidden", false);
    $("#agent-profile-form-title").text(profile ? `Edit ${profile.name}` : "Create agent profile");
    $("#agent-profile-result").text("");
    const runner_select = $("#agent-profile-runner").empty();
    for (const runner of runners) {
        option(runner_select, runner.id, runner_label(runner));
    }
    if (profile) {
        $("#agent-profile-name").val(profile.name);
        $("#agent-profile-description").val(profile.description);
        runner_select.val(profile.runner_id ?? "");
        runner_select.prop("disabled", true);
    } else {
        runner_select.prop("disabled", false);
    }
    update_runtime_choices(false);
    if (profile) {
        $("#agent-profile-adapter").val(`${profile.adapter_id}@${profile.adapter_version}`);
        $("#agent-profile-mode").val(profile.mode);
        $("#agent-profile-provider").val(profile.provider_id ?? "");
        $("#agent-profile-repository").val(profile.repository_id ?? "");
        $("#agent-profile-default-mode").val(profile.default_mode);
        $("#agent-profile-sandbox").val(profile.configuration?.policy.sandbox_alias ?? "");
        $("#agent-profile-context").prop(
            "checked",
            profile.configuration?.policy.actions.includes("context.read") ?? true,
        );
        $("#agent-profile-shell").prop(
            "checked",
            profile.configuration?.policy.actions.includes("shell.run") ?? false,
        );
        for (const {field, action} of [
            {field: "repository-read", action: "repository.read"},
            {field: "repository-edit", action: "repository.edit"},
            {field: "checks", action: "checks.run"},
            {field: "dependencies", action: "dependencies.install"},
            {field: "commit", action: "git.commit"},
            {field: "push", action: "git.push"},
            {field: "pr", action: "git.draft_pr"},
        ]) {
            $("#agent-profile-" + field).prop(
                "checked",
                profile.configuration?.policy.actions.includes(action) ?? false,
            );
        }
        $("#agent-profile-hard-cap").prop(
            "checked",
            profile.configuration?.policy.hard_cost_cap ?? false,
        );
        const budget = profile.configuration?.budget;
        if (budget && typeof budget === "object") {
            const row = budget as Record<string, unknown>;
            $("#agent-profile-active-seconds").val(Number(row["active_seconds"] ?? 3600));
            $("#agent-profile-input-tokens").val(Number(row["input_tokens"] ?? 1024));
            $("#agent-profile-output-tokens").val(Number(row["output_tokens"] ?? 512));
        }
    } else {
        profile_key = new_client_key();
        sessionStorage.setItem(pending_key(), profile_key);
    }
    update_step();
    $("#agent-profile-name").trigger("focus");
}
function profile_payload(): Record<string, unknown> {
    const [adapter_id, adapter_version] = value("#agent-profile-adapter").split("@");
    const budget = selected_profile?.configuration?.budget;
    const base = budget && typeof budget === "object" ? (budget as Record<string, unknown>) : {};
    const actions = [
        $("#agent-profile-context").prop("checked") ? "context.read" : "",
        $("#agent-profile-shell").prop("checked") ? "shell.run" : "",
        $("#agent-profile-repository-read").prop("checked") ? "repository.read" : "",
        $("#agent-profile-repository-edit").prop("checked") ? "repository.edit" : "",
        $("#agent-profile-checks").prop("checked") ? "checks.run" : "",
        $("#agent-profile-dependencies").prop("checked") ? "dependencies.install" : "",
        $("#agent-profile-commit").prop("checked") ? "git.commit" : "",
        $("#agent-profile-push").prop("checked") ? "git.push" : "",
        $("#agent-profile-pr").prop("checked") ? "git.draft_pr" : "",
    ].filter(Boolean);
    const provider_id = value("#agent-profile-provider");
    const provider = providers.find((item) => item.id === provider_id);
    return {
        name: value("#agent-profile-name"),
        description: value("#agent-profile-description"),
        runner_id: value("#agent-profile-runner"),
        adapter_id,
        adapter_version,
        mode: value("#agent-profile-mode"),
        default_mode: value("#agent-profile-default-mode"),
        provider_id: provider_id || null,
        repository_id: value("#agent-profile-repository") || null,
        sandbox_alias: value("#agent-profile-sandbox"),
        actions,
        budget: {
            ...base,
            active_seconds: number("#agent-profile-active-seconds"),
            input_tokens: number("#agent-profile-input-tokens"),
            output_tokens: number("#agent-profile-output-tokens"),
        },
        hard_cost_cap: Boolean($("#agent-profile-hard-cap").prop("checked")),
        ...api.profile_network_choice(
            provider_id,
            provider,
            selected_profile,
            current_user.user_id,
            default_network,
        ),
    };
}
async function save_profile(): Promise<void> {
    const token = visit;
    const form_revision = draft_revision;
    const form_token = form_visit;
    const request_profile = selected_profile;
    const request_target = request_profile?.id ?? "";
    const payload = profile_payload();
    if (!payload["name"] || !payload["adapter_id"] || !payload["sandbox_alias"]) {
        $("#agent-profile-result").text("Complete the identity, adapter, and sandbox fields.");
        return;
    }
    const is_edit = Boolean(request_profile);
    const key = profile_key;
    profile_submitted = true;
    if (!is_edit && key) {
        register_pending_key(key);
    }
    $("#agent-profile-result").text("Saving draft…");
    try {
        let profile: api.AgentProfile;
        if (request_profile) {
            const edit_payload = {...payload};
            delete edit_payload["runner_id"];
            profile = (
                await api.update_profile(request_target, {
                    ...edit_payload,
                    expected_revision: request_profile.revision,
                    expected_metadata_revision: request_profile.metadata_revision,
                })
            ).profile;
        } else {
            profile = (await api.create_profile({...payload, idempotency_key: key})).profile;
        }
        if (!current(token)) {
            return;
        }
        if (!is_edit && key) {
            remove_pending_key(key);
        }
        if (!owns_editor(token, form_token, "profile", request_target)) {
            return;
        }
        selected_profile = profile;
        if (!is_edit) {
            editor_target = profile.id;
        }
        if (draft_revision !== form_revision) {
            $("#agent-profile-result").text("Draft saved. Your newer edits remain in the form.");
            return;
        }
        $("#agent-profile-result").text("Draft saved. Run a probe, then enable it explicitly.");
        if (!is_edit) {
            hide_editors();
        }
        void load_profiles();
    } catch {
        if (!current(token)) {
            return;
        }
        if (!is_edit && key) {
            try {
                const recovered = await api.recover_profile(key);
                if (!current(token)) {
                    return;
                }
                remove_pending_key(key);
                if (!owns_editor(token, form_token, "profile", request_target)) {
                    return;
                }
                selected_profile = recovered.profile;
                editor_target = recovered.profile.id;
                $("#agent-profile-result").text(
                    "The server saved this profile. Its identity was recovered. Your current form edits remain.",
                );
                return;
            } catch {
                /* The original request may not have reached the server. */
            }
        }
        if (owns_editor(token, form_token, "profile", request_target)) {
            $("#agent-profile-result").text(
                "Save status is unknown. Review the draft and retry with the same identity.",
            );
        }
    }
}
async function load_choices(editor?: number, kind = "profile", target = ""): Promise<void> {
    const token = visit;
    try {
        const [runner_data, provider_data, repository_data] = await Promise.all([
            api.list_runners({offset: 0, limit: 100}),
            api.list_providers({offset: 0, limit: 100}),
            api.list_repositories({offset: 0, limit: 100}),
        ]);
        if (
            !current(token) ||
            (editor !== undefined && !owns_editor(token, editor, kind, target))
        ) {
            return;
        }
        runners = runner_data.runners;
        providers = provider_data.providers;
        repositories = repository_data.repositories;
    } catch {
        if (current(token) && (editor === undefined || owns_editor(token, editor, kind, target))) {
            announce("Some resource choices are unavailable. Retry before saving a profile.");
        }
    }
}
function render_profile_detail(
    profile: api.AgentProfile,
    attachments: Awaited<ReturnType<typeof api.get_profile>>["attachments"],
    setup: api.AgentSetup | null,
    editor: number,
): void {
    const detail = $("#agent-profile-detail").empty().prop("hidden", false);
    $("<h4>").text(profile.name).appendTo(detail);
    line(detail, "Owner", profile.owner.name);
    line(detail, "Saved state", profile.desired_state);
    line(
        detail,
        "Readiness",
        `${profile.readiness_state} at revision ${profile.readiness_revision ?? "none"}`,
    );
    line(detail, "Runner", runner_label(profile.runner));
    line(detail, "Model", provider_location(profile));
    line(detail, "Repository", profile.repository?.workspace_alias ?? "None");
    if (setup) {
        line(detail, "Setup", `${setup.phase} · profile revision ${setup.profile_revision}`);
        line(detail, "Provider test revision", setup.provider_config_version ?? "None");
        if (setup.profile_revision !== profile.revision) {
            line(
                detail,
                "Probe evidence",
                "This setup tested an older profile revision. Run a new probe.",
            );
        }
        for (const requirement of setup.requirements) {
            const row = $("<div class='agent-card'>").appendTo(detail);
            line(
                row,
                "Requirement",
                `${requirement.surface}: ${requirement.code.replaceAll("_", " ")}`,
            );
            const repair = {
                connect_runner: ["Open devices", "repair-devices"],
                register_workspace: ["Open repositories", "repair-devices"],
                install_adapter: ["Open devices", "repair-devices"],
                login_vendor: ["Open model connections", "repair-connections"],
                edit_provider: ["Open model connections", "repair-connections"],
                probe_again: ["Run another probe", "profile-probe"],
                request_grant: ["Review resource grants", "repair-grants"],
                configure_sandbox: ["Edit profile runtime", "profile-edit"],
                view_diagnostic: ["Ask the resource owner for diagnostics", "repair-devices"],
            }[requirement.action];
            if (repair) {
                button(row, repair[0]!, repair[1]!, profile.id);
            }
        }
    } else {
        line(detail, "Setup", "No setup result yet. Save, probe, then enable explicitly.");
    }
    for (const [name, accessible] of [
        ["Combined", profile.access.complete],
        ["Device", profile.access.runner],
        ["Model connection", profile.access.provider],
        ["Repository", profile.access.repository],
    ] as const) {
        line(
            detail,
            `${name} access for you`,
            accessible ? "Available" : "Requires the resource owner's grant",
        );
    }
    if (!profile.access.complete) {
        line(
            detail,
            "Next step",
            "Ask each listed resource owner for the missing grant. Your profile grant alone is insufficient.",
        );
    }
    if (profile.desired_state === "draft" && profile.readiness_state === "ready") {
        line(detail, "Activation", "Ready draft. Explicit enable is required.");
    }
    if (profile.runner?.observed_presence === "offline" && profile.desired_state === "enabled") {
        line(detail, "Queue", "Offline runner. New work can remain queued if authorized.");
    }
    line(
        detail,
        "Channel attachments",
        attachments.length > 0
            ? attachments
                  .map((item) => `${item.name}: ${item.bot_member ? "bot member" : "not a member"}`)
                  .join(", ")
            : "None visible",
    );
    const controls = $("<div class='agent-actions'>").appendTo(detail);
    if (profile.access.complete && profile.desired_state === "enabled") {
        button(controls, "Create task", "create-task", profile.id);
    }
    for (const action of ["edit", "probe", "enable", "pause", "archive"] as const) {
        if (
            profile.allowed_actions.includes(action) &&
            (action !== "pause" || profile.desired_state === "enabled")
        ) {
            button(
                controls,
                action[0]!.toUpperCase() + action.slice(1),
                `profile-${action}`,
                profile.id,
            );
        }
    }
    if (profile.allowed_actions.includes("edit")) {
        const channel = $("<form class='agent-inline-form'>")
            .attr("id", "agent-attach-form")
            .appendTo(detail);
        $("<label for='agent-attach-stream' class='settings-field-label'>")
            .text("Channel")
            .appendTo(channel);
        const channel_choice = $(
            "<select id='agent-attach-stream' required class='settings_select bootstrap-focus-style'>",
        ).appendTo(channel);
        option(channel_choice, "", "Select a channel");
        for (const sub of stream_data.get_unsorted_subs_with_content_access()) {
            option(channel_choice, String(sub.stream_id), sub.name);
        }
        $("<button type='submit' class='action-button action-button-solid-brand'>")
            .text("Attach to channel")
            .appendTo(channel);
        button(controls, "Manage profile grants", "grant-open-profile", profile.id);
    }
    $("<h5>").text("Grants").appendTo(detail);
    $("<div id='agent-profile-grants'>").appendTo(detail);
    $(
        "<button type='button' id='agent-detail-close' class='action-button action-button-subtle-neutral'>",
    )
        .text("Close details")
        .appendTo(detail);
    void load_grants("profile", profile.id, $("#agent-profile-grants"), editor, "profile-detail");
}
async function open_profile_detail(id: string): Promise<void> {
    const token = visit;
    const editor = begin_editor("profile-detail", id);
    try {
        const result = await api.get_profile(id);
        if (!owns_editor(token, editor, "profile-detail", id)) {
            return;
        }
        hide_editors();
        selected_profile = result.profile;
        render_profile_detail(result.profile, result.attachments, result.setup, editor);
        $("#agent-profile-detail h4").trigger("focus");
    } catch {
        if (owns_editor(token, editor, "profile-detail", id)) {
            announce("Profile details are unavailable.");
        }
    }
}
async function profile_control(
    id: string,
    action: "pause" | "archive" | "enable" | "readiness",
): Promise<void> {
    const token = visit;
    const profile = profiles.find((item) => item.id === id) ?? selected_profile;
    if (!profile?.allowed_actions.includes(action === "readiness" ? "probe" : action)) {
        return;
    }
    const editor = begin_editor("profile-control", id);
    const payload = {
        expected_revision: profile.revision,
        ...(action === "readiness" ? {retry_key: new_client_key()} : {}),
    };
    try {
        await api.profile_action(id, action, payload);
        if (!owns_editor(token, editor, "profile-control", id)) {
            return;
        }
        announce(
            action === "readiness"
                ? "Probe started. Readiness will update after the runner reports."
                : `Profile ${action} accepted.`,
        );
        await load_profiles();
        if (owns_editor(token, editor, "profile-control", id)) {
            await open_profile_detail(id);
        }
    } catch {
        if (owns_editor(token, editor, "profile-control", id)) {
            announce(`Profile ${action} failed. Refresh its revision and retry.`);
        }
    }
}
function render_runners(count: number): void {
    const list = $("#agent-runner-list").empty();
    $("#agent-device-count").text(`${count} authorized devices`);
    $("#agent-device-more").prop("hidden", runner_offset + runners.length >= count);
    if (runners.length === 0) {
        $("<p>").text("No devices are visible to you.").appendTo(list);
    }
    for (const runner of runners) {
        const card = $("<article class='agent-card'>").appendTo(list);
        $("<h4>").text(runner.name).appendTo(card);
        line(card, "Declared category", runner.host_kind);
        line(card, "Observed presence", runner.observed_presence);
        line(card, "Observed at", runner.observed_at);
        line(card, "Catalog revision", runner.catalog_revision);
        const safe = catalog(runner);
        line(
            card,
            "Approved adapters",
            safe.adapters.length > 0
                ? safe.adapters.map((item) => `${item.id} ${item.version}`).join(", ")
                : "Unavailable",
        );
        line(
            card,
            "Approved sandboxes",
            safe.sandboxes.length > 0
                ? safe.sandboxes.map((item) => item.alias).join(", ")
                : "Unavailable",
        );
        if (runner.allowed_actions.includes("edit")) {
            button(card, "Edit metadata", "runner-edit", runner.id);
            button(card, "Manage device grants", "grant-open-runner", runner.id);
        }
        if (runner.allowed_actions.includes("edit")) {
            button(card, "Register repository", "repository-new", runner.id);
        }
        if (runner.allowed_actions.includes("revoke")) {
            button(card, "Revoke pairing", "runner-revoke", runner.id);
        }
    }
}
async function load_runners(): Promise<void> {
    const token = visit;
    runner_request += 1;
    const request = runner_request;
    try {
        const [result, repository_data] = await Promise.all([
            api.list_runners({offset: runner_offset, limit: 50}),
            api.list_repositories({offset: 0, limit: 100}),
        ]);
        if (!current(token) || request !== runner_request) {
            return;
        }
        runners = result.runners;
        repositories = repository_data.repositories;
        render_runners(result.count);
        const list = $("#agent-repository-list").empty();
        for (const repository of repositories) {
            const card = $("<article class='agent-card'>").appendTo(list);
            $("<h4>").text(repository.workspace_alias).appendTo(card);
            line(
                card,
                "Device",
                runners.find((item) => item.id === repository.runner_id)?.name ??
                    "Authorized device",
            );
            if (repository.allowed_actions.includes("manage") && repository.policy_version) {
                button(card, "Manage repository grants", "grant-open-repository", repository.id);
            }
        }
    } catch {
        if (current(token) && request === runner_request) {
            runners = [];
            $("#agent-runner-list").empty();
            announce("Device status is unknown. Retry devices.");
        }
    }
}
function open_runner(id: string): void {
    selected_runner = runners.find((item) => item.id === id);
    if (!selected_runner?.allowed_actions.includes("edit")) {
        return;
    }
    begin_editor("runner", id);
    $("#agent-runner-form").prop("hidden", false);
    $("#agent-runner-name").val(selected_runner.name).trigger("focus");
    $("#agent-runner-kind").val(selected_runner.host_kind);
}
async function save_runner(): Promise<void> {
    const runner = selected_runner;
    if (!runner) {
        return;
    }
    const token = visit;
    const editor = form_visit;
    const revision = draft_revision;
    try {
        await api.update_runner_metadata({
            runner_id: runner.id,
            expected_metadata_revision: runner.metadata_revision,
            name: value("#agent-runner-name"),
            host_kind: value("#agent-runner-kind") as api.AgentRunner["host_kind"],
        });
        if (!owns_editor(token, editor, "runner", runner.id)) {
            return;
        }
        announce("Device metadata saved.");
        if (revision === draft_revision) {
            hide_editors();
        }
        await load_runners();
    } catch {
        if (owns_editor(token, editor, "runner", runner.id)) {
            announce("Device metadata was not saved. Refresh and retry.");
        }
    }
}
async function save_repository(): Promise<void> {
    const runner = selected_runner;
    if (!runner) {
        return;
    }
    const token = visit;
    const editor = form_visit;
    const submitted_draft = draft_revision;
    try {
        await api.create_repository({
            runner_id: runner.id,
            workspace_alias: value("#agent-repository-alias"),
            canonical_origin: value("#agent-repository-origin") || null,
            allowed_refs: [value("#agent-repository-ref")],
            required_checks: [],
        });
        if (!owns_editor(token, editor, "repository", runner.id)) {
            return;
        }
        if (submitted_draft === draft_revision) {
            hide_editors();
            announce("Repository registered. Add explicit grants before shared use.");
        } else {
            $("#agent-repository-result").text(
                "Repository registered. Your newer changes remain unsaved.",
            );
        }
        await load_choices();
    } catch {
        if (owns_editor(token, editor, "repository", runner.id)) {
            $("#agent-repository-result").text(
                "Repository registration failed. Check the device catalog and origin.",
            );
        }
    }
}
async function approve_pairing(): Promise<void> {
    const token = visit;
    const editor = begin_editor("pairing");
    const pairing_id = value("#agent-pairing-id");
    const code = value("#agent-pairing-code");
    try {
        await api.approve_pairing(pairing_id, code);
        if (
            !owns_editor(token, editor, "pairing") ||
            value("#agent-pairing-id") !== pairing_id ||
            value("#agent-pairing-code") !== code
        ) {
            return;
        }
        $("#agent-pairing-form").trigger("reset");
        announce("Pairing approved. The device may report its catalog shortly.");
        await load_runners();
    } catch {
        if (owns_editor(token, editor, "pairing")) {
            announce("Pairing approval failed. Check the code and pairing state.");
        }
    }
}
function render_providers(count: number): void {
    const list = $("#agent-provider-list").empty();
    $("#agent-provider-more").prop("hidden", provider_offset + providers.length >= count);
    if (providers.length === 0) {
        $("<p>").text("No model connections are visible to you.").appendTo(list);
    }
    for (const provider of providers) {
        const card = $("<article class='agent-card'>").appendTo(list);
        $("<h4>").text(provider.name).appendTo(card);
        line(card, "Model ID", provider.model_id);
        line(
            card,
            "Runner",
            runners.find((item) => item.id === provider.runner_id)?.name ?? "Unknown",
        );
        line(card, "Endpoint location", provider.base_url ?? "Private to owner");
        line(card, "Data scope", provider.data_scope.join(", "));
        const capabilities = provider.capabilities;
        if (capabilities && typeof capabilities === "object") {
            const data = capabilities as Record<string, unknown>;
            for (const key of ["chat_ready", "tool_calling", "streaming", "code_ready"]) {
                line(card, key.replaceAll("_", " "), data[key] ?? "unknown");
            }
        }
        if (provider.allowed_actions.includes("edit")) {
            button(card, "Edit", "provider-edit", provider.id);
            button(card, "Manage connection grants", "grant-open-provider", provider.id);
        }
        if (provider.allowed_actions.includes("probe")) {
            button(card, "Probe", "provider-probe", provider.id);
        }
    }
}
async function load_providers(): Promise<void> {
    const token = visit;
    provider_request += 1;
    const request = provider_request;
    try {
        const [rows, devices] = await Promise.all([
            api.list_providers({offset: provider_offset, limit: 50}),
            api.list_runners({offset: 0, limit: 100}),
        ]);
        if (!current(token) || request !== provider_request) {
            return;
        }
        providers = rows.providers;
        runners = devices.runners;
        render_providers(rows.count);
    } catch {
        if (current(token) && request === provider_request) {
            providers = [];
            $("#agent-provider-list").empty();
            announce("Model connection status is unknown.");
        }
    }
}
function open_provider(
    provider?: api.AgentProvider,
    editor = begin_editor("provider", provider?.id ?? ""),
): void {
    if (!owns_editor(visit, editor, "provider", provider?.id ?? "")) {
        return;
    }
    if (provider && !provider.allowed_actions.includes("edit")) {
        return;
    }
    selected_provider = provider;
    $("#agent-provider-form").trigger("reset").prop("hidden", false);
    $("#agent-provider-result").text("");
    $("#agent-provider-form-title").text(
        provider ? `Edit ${provider.name}` : "Add model connection",
    );
    const select = $("#agent-provider-runner").empty();
    for (const runner of runners.filter((item) => item.allowed_actions.includes("edit"))) {
        option(select, runner.id, runner.name);
    }
    select.val(provider?.runner_id ?? String(select.val() ?? ""));
    select.prop("disabled", Boolean(provider));
    $("#agent-provider-name").val(provider?.name ?? "");
    $("#agent-provider-url").val(provider?.base_url ?? "");
    $("#agent-provider-api-mode").val(provider?.api_mode ?? "chat_completions");
    $("#agent-provider-model").val(provider?.model_id ?? "");
    $("#agent-provider-allowed-models").val(provider?.allowed_models.join("\n") ?? "");
    $("#agent-provider-context").val(provider?.context_window_tokens ?? 8192);
    $("#agent-provider-output").val(provider?.max_output_tokens ?? 2048);
    $("#agent-provider-scopes input").each((_, element) => {
        $(element).prop(
            "checked",
            (provider?.data_scope ?? ["synthetic"]).includes(String($(element).val())),
        );
    });
    const target =
        provider?.network &&
        typeof provider.network === "object" &&
        "targets" in provider.network &&
        Array.isArray(provider.network.targets)
            ? (provider.network.targets[0] as Record<string, unknown> | undefined)
            : undefined;
    $("#agent-provider-network-host").val(
        typeof target?.["hostname"] === "string" ? target["hostname"] : "",
    );
    $("#agent-provider-network-port").val(Number(target?.["port"] ?? 443));
    $("#agent-provider-network-private").prop("checked", Boolean(target?.["allow_private"]));
    $("#agent-provider-network-loopback").prop("checked", Boolean(target?.["allow_http_loopback"]));
    $("#agent-provider-network-http-private").prop(
        "checked",
        Boolean(target?.["allow_http_private"]),
    );
    $("#agent-provider-credential, #agent-provider-local-ref").val("");
    $("#agent-provider-impact").empty();
    if (provider) {
        void load_provider_impact(provider.id, editor);
    }
    $("#agent-provider-name").trigger("focus");
}
async function load_provider_impact(id: string, editor: number): Promise<void> {
    const token = visit;
    const affected: api.AgentProfile[] = [];
    let incomplete = false;
    try {
        for (let offset = 0; offset < 500; offset += 100) {
            const result = await api.list_profiles({offset, limit: 100, ownership: "all"});
            if (!owns_editor(token, editor, "provider", id)) {
                return;
            }
            affected.push(...result.profiles.filter((profile) => profile.provider_id === id));
            if (offset + result.profiles.length >= result.count) {
                break;
            }
            if (offset === 400) {
                incomplete = true;
            }
        }
        const box = $("#agent-provider-impact").empty();
        $("<h5>").text("Profiles affected by execution changes").appendTo(box);
        if (affected.length === 0) {
            line(box, "Visible profiles", "None in the checked authorized pages");
        }
        for (const profile of affected) {
            line(
                box,
                "Needs a new probe after change",
                `${profile.name} · owner ${profile.owner.name}`,
            );
        }
        if (incomplete) {
            line(box, "Limit", "Only the first 500 authorized profiles were checked.");
        }
        line(box, "Active attempts", "Existing attempt snapshots keep their saved configuration.");
    } catch {
        if (owns_editor(token, editor, "provider", id)) {
            $("#agent-provider-impact").text(
                "Affected profile list is unavailable. Review it before an execution change.",
            );
        }
    }
}
async function save_provider(): Promise<void> {
    const token = visit;
    const editor = form_visit;
    const revision = draft_revision;
    const provider = selected_provider;
    const credential = value("#agent-provider-credential");
    if (credential.includes("••") || credential === "********") {
        $("#agent-provider-result").text("Enter a new credential, not a masked value.");
        return;
    }
    const local = value("#agent-provider-local-ref");
    const scopes = $("#agent-provider-scopes input:checked")
        .map((_, element) => String($(element).val()))
        .get();
    if (scopes.length === 0) {
        $("#agent-provider-result").text("Select at least one data scope.");
        return;
    }
    const prior_network = provider?.network;
    const prior_policy =
        prior_network && typeof prior_network === "object"
            ? (prior_network as Record<string, unknown>)
            : default_network;
    const prior_targets: unknown[] = Array.isArray(prior_policy.targets)
        ? (prior_policy.targets as unknown[])
        : [];
    const network_host = value("#agent-provider-network-host");
    const network = network_host
        ? {
              ...prior_policy,
              targets: [
                  {
                      ...(prior_targets[0] && typeof prior_targets[0] === "object"
                          ? (prior_targets[0] as Record<string, unknown>)
                          : {}),
                      hostname: network_host,
                      port: number("#agent-provider-network-port"),
                      allow_private: Boolean($("#agent-provider-network-private").prop("checked")),
                      allow_http_loopback: Boolean(
                          $("#agent-provider-network-loopback").prop("checked"),
                      ),
                      allow_http_private: Boolean(
                          $("#agent-provider-network-http-private").prop("checked"),
                      ),
                  },
                  ...prior_targets.slice(1),
              ],
          }
        : default_network;
    const payload: Record<string, unknown> = {
        name: value("#agent-provider-name"),
        base_url: value("#agent-provider-url"),
        api_mode: value("#agent-provider-api-mode"),
        model_id: value("#agent-provider-model"),
        allowed_models: value("#agent-provider-allowed-models")
            .split(/\n/)
            .map((item) => item.trim())
            .filter(Boolean),
        context_window_tokens: number("#agent-provider-context"),
        max_output_tokens: number("#agent-provider-output"),
        data_scope: scopes,
        network,
    };
    if (provider) {
        payload["expected_config_version"] = provider.config_version;
        payload["expected_metadata_revision"] = provider.metadata_revision;
        if (credential) {
            payload["credential_replacement"] = credential;
        }
        if (local) {
            payload["local_credential_ref"] = local;
        }
    } else {
        payload["runner_id"] = value("#agent-provider-runner");
        if (credential) {
            payload["credential"] = credential;
        }
        if (local) {
            payload["local_credential_ref"] = local;
        }
    }
    $("#agent-provider-result").text("Saving connection…");
    try {
        if (provider) {
            await api.update_provider(provider.id, payload);
        } else {
            await api.create_provider(payload);
        }
        if (!owns_editor(token, editor, "provider", provider?.id ?? "")) {
            return;
        }
        if (revision === draft_revision && value("#agent-provider-credential") === credential) {
            $("#agent-provider-credential").val("");
        }
        if (revision === draft_revision && value("#agent-provider-local-ref") === local) {
            $("#agent-provider-local-ref").val("");
        }
        $("#agent-provider-result").text(
            revision === draft_revision
                ? "Connection saved. Probe it explicitly."
                : "Connection saved. Newer edits remain in the form.",
        );
        if (revision === draft_revision) {
            hide_editors();
        }
        await load_providers();
    } catch {
        if (owns_editor(token, editor, "provider", provider?.id ?? "")) {
            $("#agent-provider-result").text(
                "Connection save failed. Check the fields and current revision.",
            );
        }
    }
}
async function load_default(): Promise<void> {
    const token = visit;
    default_request += 1;
    const request = default_request;
    try {
        const [setting, candidates] = await Promise.all([
            api.get_team_default(),
            api.list_profiles({offset: 0, limit: 100, access: "complete"}),
        ]);
        if (!current(token) || request !== default_request) {
            return;
        }
        const data = setting.default;
        const status = $("#agent-team-default").empty();
        if (data.profile) {
            line(status, "Selected profile", `${data.profile.name} · ${data.profile.owner.name}`);
            line(status, "Runner presence", data.profile.runner?.observed_presence ?? "unknown");
            line(
                status,
                "Audience",
                "Recorded grants below show only identities you may view. Each member still needs all resource grants.",
            );
            if (data.profile.runner?.observed_presence === "offline") {
                line(
                    status,
                    "Availability",
                    "Valid default; work may queue while runner is offline.",
                );
            }
        } else {
            line(
                status,
                "Selection",
                data.has_default
                    ? "A default exists but is not visible to you."
                    : "No team default.",
            );
        }
        const form = $("#agent-default-form");
        form.prop("hidden", data.selection_revision === undefined);
        if (!default_dirty) {
            default_expected_revision = data.selection_revision;
        } else if (default_expected_revision !== data.selection_revision) {
            line(
                status,
                "Conflict",
                "The saved selection changed. Keep your choice and refresh before saving.",
            );
        }
        const chosen = default_dirty ? value("#agent-default-choice") : (data.profile?.id ?? "");
        const select = $("#agent-default-choice").empty();
        option(select, "", "No default");
        for (const profile of candidates.profiles.filter(
            (item) => item.desired_state === "enabled" && item.readiness_state === "ready",
        )) {
            option(
                select,
                profile.id,
                `${profile.name} · ${profile.owner.name} · ${runner_label(profile.runner)}`,
            );
        }
        if (
            chosen &&
            select.find("option").filter((_, item) => item.value === chosen).length === 0
        ) {
            option(
                select,
                chosen,
                "Your unsaved selection is no longer in the visible candidate list",
            );
        }
        select.val(chosen);
        $("#agent-default-clear").prop(
            "hidden",
            !data.has_default || !data.allowed_actions?.includes("clear"),
        );
        line(status, "Visible candidate count", candidates.count);
        if (data.profile) {
            try {
                const grants = await api.list_grants("profile", data.profile.id);
                if (current(token) && request === default_request) {
                    const audience = $("<div class='agent-cards'>").appendTo(status);
                    render_grants(audience, grants);
                }
            } catch {
                if (current(token) && request === default_request) {
                    line(status, "Audience", "Grant details are unavailable or restricted.");
                }
            }
        }
    } catch {
        if (current(token) && request === default_request) {
            $("#agent-team-default").text("Team default status is unknown. Retry this panel.");
            $("#agent-default-form").prop("hidden", true);
        }
    }
}
async function save_default(profile_id: string | null): Promise<void> {
    const token = visit;
    const revision = default_expected_revision;
    const draft = default_draft_revision;
    if (!revision) {
        return;
    }
    try {
        await api.update_team_default(revision, profile_id);
        if (!current(token)) {
            return;
        }
        if (draft === default_draft_revision) {
            default_dirty = false;
            announce("Team default saved. Resource access is unchanged.");
        } else {
            announce("Earlier default saved. Your newer selection remains unsaved.");
        }
        await load_default();
    } catch {
        if (current(token) && draft === default_draft_revision) {
            announce(
                "Team default changed or is unavailable. Your selection remains. Refresh before trying again.",
            );
        }
    }
}
export function reset(): void {
    visit += 1;
    if (refresh_timer) {
        clearTimeout(refresh_timer);
    }
    refresh_timer = undefined;
    draft_revision += 1;
    begin_editor("");
    default_dirty = false;
    default_expected_revision = undefined;
    profiles = [];
    runners = [];
    providers = [];
    repositories = [];
    selected_profile = undefined;
    selected_runner = undefined;
    selected_provider = undefined;
    $("#agent-profile-list, #agent-runner-list, #agent-provider-list, #agent-team-default").empty();
    hide_editors();
    announce("");
    $("#agent-provider-credential, #agent-provider-local-ref").val("");
}

async function load_recent_jobs(): Promise<void> {
    const token = visit;
    try {
        const result = await api.list_jobs();
        if (!current(token)) {
            return;
        }
        const list = $("#agent-recent-jobs").empty();
        line(list, "Authorized jobs", result.count);
        for (const job of result.jobs) {
            const row = $("<div class='agent-card'>").appendTo(list);
            line(row, "Job", `${job.id} · ${job.status} · ${job.job_kind}`);
            $("<button type='button' class='action-button action-button-subtle-neutral'>")
                .attr("data-agent-job-id", job.id)
                .text("Open job panel")
                .appendTo(row);
        }
    } catch {
        if (current(token)) {
            $("#agent-recent-jobs").text("Job list is unavailable.");
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
        if (editor_kind === "") {
            announce(
                `Recovered saved profile ${result.profile.name}. Review its draft before probing.`,
            );
        }
        void load_profiles();
    } catch {
        if (current(token) && editor_kind === "") {
            announce("An uncertain profile save can be retried with its original identity.");
        }
    }
}

export function set_up(): void {
    session_identity = identity();
    if (!handlers_bound) {
        bind_handlers();
        handlers_bound = true;
    }
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
    const root = $(document);
    root.on("click", "[data-agent-tab]", function () {
        show_tab($(this).attr("data-agent-tab") as Tab);
    });
    root.on("submit", "#agent-directory-filter", (event) => {
        event.preventDefault();
        profile_offset = 0;
        directory_revision += 1;
        void load_profiles();
    });
    root.on("click", "#agent-directory-prev", () => {
        profile_offset = Math.max(0, profile_offset - 20);
        void load_profiles();
    });
    root.on("click", "#agent-directory-next", () => {
        profile_offset += 20;
        void load_profiles();
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
    root.on("click", "#agent-new-profile", () => {
        const token = visit;
        const editor = begin_editor("profile");
        void load_choices(editor).then(() => {
            if (owns_editor(token, editor, "profile")) {
                open_profile(undefined, editor);
            }
        });
    });
    root.on("click", "#agent-profile-next", () => {
        if (step < steps.length - 1) {
            step += 1;
            update_step();
            $(`#agent-profile-form [data-agent-step='${step}']`).trigger("focus");
        }
    });
    root.on("click", "#agent-profile-back", () => {
        if (step > 0) {
            step -= 1;
            update_step();
        }
    });
    root.on(
        "click",
        "#agent-profile-cancel, #agent-detail-close, #agent-runner-cancel, #agent-provider-cancel, #agent-repository-cancel",
        () => {
            begin_editor("");
            if (!profile_submitted && profile_key) {
                remove_pending_key(profile_key);
            }
            $("#agent-new-profile").trigger("focus");
        },
    );
    root.on(
        "input change",
        "#agent-profile-form input, #agent-profile-form textarea, #agent-profile-form select, #agent-provider-form input, #agent-provider-form textarea, #agent-provider-form select, #agent-runner-form input, #agent-runner-form select, #agent-repository-form input, #agent-repository-form select",
        () => {
            draft_revision += 1;
        },
    );
    root.on("input change", "#agent-directory-filter input, #agent-directory-filter select", () => {
        directory_revision += 1;
    });
    root.on("change", "#agent-profile-runner", () => {
        update_runtime_choices();
    });
    root.on("submit", "#agent-profile-form", (event) => {
        event.preventDefault();
        void save_profile();
    });
    root.on("submit", "#agent-pairing-form", (event) => {
        event.preventDefault();
        void approve_pairing();
    });
    root.on("submit", "#agent-runner-form", (event) => {
        event.preventDefault();
        void save_runner();
    });
    root.on("submit", "#agent-repository-form", (event) => {
        event.preventDefault();
        void save_repository();
    });
    root.on("click", "#agent-device-more", () => {
        runner_offset += 50;
        void load_runners();
    });
    root.on("click", "#agent-provider-more", () => {
        provider_offset += 50;
        void load_providers();
    });
    root.on("click", "#agent-new-provider", () => {
        open_provider();
    });
    root.on("submit", "#agent-provider-form", (event) => {
        event.preventDefault();
        void save_provider();
    });
    root.on("submit", "#agent-default-form", (event) => {
        event.preventDefault();
        void save_default(value("#agent-default-choice") || null);
    });
    root.on("click", "#agent-default-clear", () => {
        $("#agent-default-choice").val("");
        default_draft_revision += 1;
        default_dirty = true;
        void save_default(null);
    });
    root.on("change", "#agent-default-choice", () => {
        default_draft_revision += 1;
        default_dirty = true;
    });
    root.on("submit", "#agent-attach-form", (event) => {
        event.preventDefault();
        const profile = selected_profile;
        const stream_id = number("#agent-attach-stream");
        if (!profile || !stream_id) {
            return;
        }
        const token = visit;
        const editor = form_visit;
        void api
            .attach_channel(profile.id, stream_id, profile.revision)
            .then(() => {
                if (!owns_editor(token, editor, "profile-detail", profile.id)) {
                    return;
                }
                announce("Channel attachment saved. Profile configuration remains saved.");
                void open_profile_detail(profile.id);
            })
            .catch(() => {
                if (owns_editor(token, editor, "profile-detail", profile.id)) {
                    announce("Channel attachment failed. The saved profile remains available.");
                }
            });
    });
    root.on("change", "#agent-grant-principal-kind", () => {
        const select = $("#agent-grant-principal").empty();
        if (value("#agent-grant-principal-kind") === "group") {
            for (const group of user_groups.get_realm_user_groups()) {
                option(select, String(group.id), group.name);
            }
        } else {
            for (const user of people.get_realm_active_human_users()) {
                option(select, String(user.user_id), user.full_name);
            }
        }
    });
    root.on("click", "#agent-grant-close", () => {
        begin_editor("");
    });
    root.on("submit", "#agent-resource-grant-form", (event) => {
        event.preventDefault();
        const target = grant_target;
        if (!target || editor_kind !== "grant" || editor_target !== `${target.kind}:${target.id}`) {
            return;
        }
        const token = visit;
        const editor = form_visit;
        const draft = draft_revision;
        const selected_actions = $("#agent-grant-actions").val();
        const actions = Array.isArray(selected_actions)
            ? selected_actions
            : selected_actions
              ? [selected_actions]
              : [];
        const principal_id = number("#agent-grant-principal");
        const principal_kind = value("#agent-grant-principal-kind");
        const scope_kind = value("#agent-grant-scope-kind");
        const dm = $("#agent-grant-dm").val();
        const participants = (Array.isArray(dm) ? dm : dm ? [dm] : []).map(Number);
        const scope =
            scope_kind === "stream"
                ? {
                      kind: "stream",
                      stream_id: number("#agent-grant-channel"),
                      topic: value("#agent-grant-topic") || null,
                  }
                : scope_kind === "direct"
                  ? {kind: "direct", participant_user_ids: participants}
                  : null;
        if (
            !principal_id ||
            actions.length === 0 ||
            (scope_kind === "direct" && participants.length === 0) ||
            (scope_kind === "stream" && !number("#agent-grant-channel"))
        ) {
            $("#agent-grant-result").text(
                "Select an audience, actions, and a valid conversation restriction.",
            );
            return;
        }
        const expiry = value("#agent-grant-expiry");
        const payload = {
            target_kind: target.kind,
            target_id: target.id,
            expected_revision: target.revision,
            ...(principal_kind === "group"
                ? {principal_group_id: principal_id}
                : {principal_user_id: principal_id}),
            actions,
            scope,
            ...(target.kind === "profile" && value("#agent-grant-repository")
                ? {repository_id: value("#agent-grant-repository")}
                : {}),
            expires_at: expiry ? new Date(expiry).toISOString() : null,
        };
        void api
            .create_grant(payload)
            .then(() => {
                if (!owns_editor(token, editor, "grant", `${target.kind}:${target.id}`)) {
                    return;
                }
                $("#agent-grant-result").text(
                    draft === draft_revision
                        ? "Grant created."
                        : "Grant created. Newer form choices remain.",
                );
                void load_grants(target.kind, target.id, $("#agent-resource-grants"), editor);
            })
            .catch(() => {
                if (owns_editor(token, editor, "grant", `${target.kind}:${target.id}`)) {
                    $("#agent-grant-result").text(
                        "Grant was not created. Check the target revision and audience.",
                    );
                }
            });
    });
    root.on(
        "input change",
        "#agent-resource-grant-form input, #agent-resource-grant-form select",
        () => {
            draft_revision += 1;
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
                selected_profile?.id === id
                    ? selected_profile
                    : profiles.find((item) => item.id === id);
            if (profile?.allowed_actions.includes("edit")) {
                open_grant_editor("profile", id, profile.revision, profile.name);
            } else {
                announce("Ask the resource owner to grant the missing access.");
            }
            return;
        }
        if (action === "profile-detail") {
            void open_profile_detail(id);
        }
        if (action === "profile-edit") {
            const token = visit;
            const editor = begin_editor("profile", id);
            void api
                .get_profile(id)
                .then((result) => {
                    if (owns_editor(token, editor, "profile", id)) {
                        open_profile(result.profile, editor);
                    }
                })
                .catch(() => {
                    if (owns_editor(token, editor, "profile", id)) {
                        announce("Profile edit data is unavailable.");
                    }
                });
        }
        if (action === "profile-probe") {
            void profile_control(id, "readiness");
        }
        if (action === "profile-pause") {
            void profile_control(id, "pause");
        }
        if (action === "profile-archive") {
            void profile_control(id, "archive");
        }
        if (action === "profile-enable") {
            void profile_control(id, "enable");
        }
        if (action === "create-task") {
            if (create_task_handler) {
                create_task_handler({profile_id: id});
            } else {
                announce("Task form is unavailable. Open a conversation and try again.");
            }
        }
        if (action.startsWith("grant-open-")) {
            const kind = action.slice("grant-open-".length) as GrantKind;
            switch (kind) {
                case "profile": {
                    const profile =
                        selected_profile?.id === id
                            ? selected_profile
                            : profiles.find((item) => item.id === id);
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
        if (action === "runner-edit") {
            open_runner(id);
        }
        if (action === "repository-new") {
            selected_runner = runners.find((item) => item.id === id);
            if (!selected_runner?.allowed_actions.includes("edit")) {
                return;
            }
            begin_editor("repository", id);
            $("#agent-repository-form").trigger("reset").prop("hidden", false);
            $("#agent-repository-result").text("");
            $("#agent-repository-alias").trigger("focus");
        }
        if (action === "runner-revoke") {
            const runner = runners.find((item) => item.id === id);
            if (!runner?.allowed_actions.includes("revoke")) {
                return;
            }
            if (!window.confirm(`Revoke pairing for ${runner.name}?`)) {
                return;
            }
            const token = visit;
            void api
                .revoke_runner(id, runner.revision)
                .then(() => {
                    if (current(token)) {
                        announce("Pairing revoked.");
                        void load_runners();
                    }
                })
                .catch(failed(token, "Pairing revocation failed."));
        }
        if (action === "provider-edit") {
            const token = visit;
            const editor = begin_editor("provider", id);
            void api
                .get_provider(id)
                .then((result) => {
                    if (owns_editor(token, editor, "provider", id)) {
                        open_provider(result.provider, editor);
                    }
                })
                .catch(() => {
                    if (owns_editor(token, editor, "provider", id)) {
                        announce("Connection edit data is unavailable.");
                    }
                });
        }
        if (action === "provider-probe") {
            const provider = providers.find((item) => item.id === id);
            if (!provider?.allowed_actions.includes("probe") || !provider.config_version) {
                return;
            }
            const token = visit;
            void api
                .probe_provider(id, {
                    expected_revision: provider.config_version,
                    retry_key: new_client_key(),
                })
                .then(() => {
                    if (current(token)) {
                        announce("Connection probe started. Refresh to see its capability report.");
                    }
                })
                .catch(failed(token, "Connection probe failed to start."));
        }
        if (action === "grant-revoke") {
            const target = grant_target;
            if (!target || editor_kind !== "grant") {
                return;
            }
            const token = visit;
            const editor = form_visit;
            void api.list_grants(target.kind, target.id).then((result) => {
                if (!owns_editor(token, editor, "grant", `${target.kind}:${target.id}`)) {
                    return;
                }
                const grant = result.grants.find((item) => item.id === id);
                if (!grant?.allowed_actions.includes("revoke")) {
                    return;
                }
                void api
                    .revoke_grant(id, grant.revision)
                    .then(() => {
                        if (owns_editor(token, editor, "grant", `${target.kind}:${target.id}`)) {
                            void load_grants(
                                target.kind,
                                target.id,
                                $("#agent-resource-grants"),
                                editor,
                            );
                        }
                    })
                    .catch(() => {
                        if (owns_editor(token, editor, "grant", `${target.kind}:${target.id}`)) {
                            $("#agent-grant-result").text("Grant revocation failed.");
                        }
                    });
            });
        }
    });
}
