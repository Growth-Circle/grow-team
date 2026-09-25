/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, promise/prefer-await-to-then, promise/always-return, @typescript-eslint/consistent-type-assertions -- This controller uses static jQuery markup and fenced delegated callbacks. */
import $ from "jquery";

import * as api from "./agent_api.ts";
import {grant_action_label, host_kind_label, presence_label} from "./agent_settings_labels.ts";
import {$t} from "./i18n.ts";
import * as people from "./people.ts";
import {current_user} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import * as user_groups from "./user_groups.ts";

// Shared state, DOM helpers, and the resource-grant editor for every agent
// settings panel: directory, devices, connections, and team default. Each
// panel file, and settings_agents.ts itself, imports the bindings and
// functions below directly; none of them import each other, so there is
// no import cycle to resolve.
export const default_network = {
    targets: [],
    public_https_only: true,
    block_metadata: true,
    cross_origin_authorization: false,
    project_network: false,
};
export type GrantKind = "profile" | "runner" | "provider" | "repository";
export let visit = 0;
export let draft_revision = 0;
export let form_visit = 0;
let editor_kind = "";
let editor_target = "";
let grant_target: {kind: GrantKind; id: string; revision: number; label: string} | undefined;
export const runners: api.AgentRunner[] = [];
export const providers: api.AgentProvider[] = [];
export const repositories: api.AgentRepository[] = [];
let session_identity = "";

function identity(): string {
    return `${window.location.origin}:${current_user.user_id}`;
}
export function current(token: number): boolean {
    return token === visit && identity() === session_identity;
}
// Called once from settings_agents.ts's set_up(), so a later current()
// check also catches a page left open across a sign-out and sign-in as a
// different user.
export function set_session_identity(): void {
    session_identity = identity();
}
// visit and draft_revision are read directly from every file that imports
// them (a live ES module binding always reads the current value), but only
// this declaring module may assign to its own `let` export; these two
// functions are the only way another file advances either counter.
export function advance_visit(): void {
    visit += 1;
}
export function advance_draft_revision(): void {
    draft_revision += 1;
}
export function announce(message: string): void {
    $("#agent-settings-status").text(message);
}
export function value(id: string): string {
    return String($(id).val() ?? "").trim();
}
export function number(id: string): number {
    return Number(value(id));
}
export function option(select: JQuery, id: string, name: string): void {
    $("<option>").val(id).text(name).appendTo(select);
}
export function line(parent: JQuery, label: string, value: unknown): void {
    const printable =
        typeof value === "string" || typeof value === "number" || typeof value === "boolean"
            ? String(value)
            : $t({defaultMessage: "Unknown"});
    $("<p>").text(`${label}: ${printable}`).appendTo(parent);
}
export function button(parent: JQuery, label: string, action: string, id: string): void {
    $("<button type='button' class='action-button action-button-subtle-neutral'>")
        .text(label)
        .attr("data-agent-action", action)
        .attr("data-agent-id", id)
        .appendTo(parent);
}
// Revisions, raw timestamps, and codes are evidence for a technical
// audience, not normal reading text; a native <details> keeps them out of
// the main card body without any JavaScript to open it.
export function technical_details(parent: JQuery, build: (box: JQuery) => void): void {
    const details = $("<details class='agent-technical-details'>").appendTo(parent);
    $("<summary>")
        .text($t({defaultMessage: "Technical details"}))
        .appendTo(details);
    build(details);
}
export function failed(token: number, message: string): () => void {
    return () => {
        if (current(token)) {
            announce(message);
        }
    };
}
export function hide_editors(): void {
    $(
        "#agent-profile-form, #agent-profile-detail, #agent-runner-form, #agent-repository-form, #agent-provider-form, #agent-grant-editor",
    ).prop("hidden", true);
}
export function begin_editor(kind: string, target = ""): number {
    form_visit += 1;
    draft_revision += 1;
    editor_kind = kind;
    editor_target = target;
    hide_editors();
    return form_visit;
}
export function owns_editor(token: number, editor: number, kind: string, target = ""): boolean {
    return (
        current(token) && form_visit === editor && editor_kind === kind && editor_target === target
    );
}
// True while no editor is open (the sentinel begin_editor("") sets), so a
// background recovery message does not interrupt a more specific one.
export function editor_is_idle(): boolean {
    return editor_kind === "";
}
// Called only from settings_agents_directory.ts's save_profile(), after it
// has already confirmed (via owns_editor) that its form is still current;
// a freshly created or recovered profile's id becomes the editor's target
// so a second save of the same still-open form is recognized as an edit.
export function set_editor_target(target: string): void {
    editor_target = target;
}
export function runner_label(runner: api.AgentRunner | null): string {
    if (!runner) {
        return $t({defaultMessage: "Device unavailable"});
    }
    return `${runner.name} · ${host_kind_label(runner.host_kind)} · ${presence_label(runner.observed_presence)}`;
}
export function catalog(runner: api.AgentRunner | undefined): api.AgentRunner["catalog_summary"] {
    return runner?.catalog_summary ?? {revision: 0, reported_at: null, adapters: [], sandboxes: []};
}
export async function load_choices(editor?: number, kind = "profile", target = ""): Promise<void> {
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
        // Splice in place rather than reassign: every file that imports
        // runners, providers, or repositories holds this same array
        // reference, and only this declaring module may rebind the `let`
        // counters above, so a loader anywhere must update these three
        // arrays the same way.
        runners.splice(0, runners.length, ...runner_data.runners);
        providers.splice(0, providers.length, ...provider_data.providers);
        repositories.splice(0, repositories.length, ...repository_data.repositories);
    } catch {
        if (current(token) && (editor === undefined || owns_editor(token, editor, kind, target))) {
            announce(
                $t({
                    defaultMessage:
                        "Some resource choices are unavailable. Retry before saving a profile.",
                }),
            );
        }
    }
}

function grant_action_option(id: string): {id: string; label: string} {
    return {id, label: grant_action_label(id)};
}
const repository_grant_actions = [
    "repository.read",
    "repository.edit",
    "checks.run",
    "shell.run",
    "dependencies.install",
    "git.commit",
    "git.push",
    "git.draft_pr",
].map((id) => grant_action_option(id));
// The server checks each job action against the profile grant as well as
// the repository grant, so a profile grant must be able to carry them all.
const grant_actions: Record<GrantKind, {id: string; label: string}[]> = {
    profile: [
        grant_action_option("profile.use"),
        grant_action_option("context.read"),
        ...repository_grant_actions,
        grant_action_option("profile.manage"),
        grant_action_option("team.manage"),
    ],
    runner: [grant_action_option("runner.use")],
    provider: [grant_action_option("provider.use")],
    repository: repository_grant_actions,
};
function grant_principal_label(principal: unknown): string {
    if (!principal || typeof principal !== "object") {
        return $t({defaultMessage: "Restricted principal"});
    }
    const row = principal as Record<string, unknown>;
    if (row["kind"] === "current_user") {
        return $t({defaultMessage: "You (other audience details are private)"});
    }
    if (row["kind"] === "user" && typeof row["user_id"] === "number") {
        return (
            people.maybe_get_user_by_id(row["user_id"])?.full_name ??
            $t({defaultMessage: "Restricted user"})
        );
    }
    if (row["kind"] === "group" && typeof row["group_id"] === "number") {
        return (
            user_groups.get_realm_user_groups().find((item) => item.id === row["group_id"])?.name ??
            $t({defaultMessage: "Restricted group"})
        );
    }
    return $t({defaultMessage: "Restricted principal"});
}
function grant_scope_label(scope: unknown, restricted: boolean): string {
    if (restricted) {
        return $t({defaultMessage: "Restricted conversation"});
    }
    if (!scope || typeof scope !== "object") {
        return $t({defaultMessage: "Any authorized conversation"});
    }
    const row = scope as Record<string, unknown>;
    if (row["kind"] === "stream" && typeof row["stream_id"] === "number") {
        const name =
            stream_data.get_sub_by_id(row["stream_id"])?.name ??
            $t({defaultMessage: "Restricted channel"});
        return `${name}${typeof row["topic"] === "string" && row["topic"] ? ` · ${row["topic"]}` : ""}`;
    }
    if (row["kind"] === "direct" && Array.isArray(row["participant_user_ids"])) {
        const names = row["participant_user_ids"]
            .map((id: unknown) =>
                typeof id === "number"
                    ? (people.maybe_get_user_by_id(id)?.full_name ??
                      $t({defaultMessage: "Restricted user"}))
                    : $t({defaultMessage: "Restricted user"}),
            )
            .join(", ");
        return $t({defaultMessage: "Direct message with {names}"}, {names});
    }
    return $t({defaultMessage: "Restricted conversation"});
}
export function render_grants(
    box: JQuery,
    result: Awaited<ReturnType<typeof api.list_grants>>,
): void {
    box.empty();
    line(box, $t({defaultMessage: "Recorded grants"}), result.count);
    if (result.count > result.grants.length) {
        line(
            box,
            $t({defaultMessage: "History"}),
            $t({defaultMessage: "Only the first 50 authorized grants are shown."}),
        );
    }
    for (const grant of result.grants) {
        const row = $("<div class='agent-card'>").appendTo(box);
        line(row, $t({defaultMessage: "Principal"}), grant_principal_label(grant.principal));
        line(
            row,
            $t({defaultMessage: "Actions"}),
            grant.actions.map((action) => grant_action_label(action)).join(", "),
        );
        line(
            row,
            $t({defaultMessage: "Conversation"}),
            grant_scope_label(grant.scope, grant.scope_restricted),
        );
        if (grant.repository_restricted) {
            const name = repositories.find(
                (item) => item.id === grant.repository_id,
            )?.workspace_alias;
            line(
                row,
                $t({defaultMessage: "Repository restriction"}),
                name ?? $t({defaultMessage: "Restricted repository"}),
            );
        }
        line(
            row,
            $t({defaultMessage: "Expiry"}),
            grant.expires_at ?? $t({defaultMessage: "No expiry"}),
        );
        line(
            row,
            $t({defaultMessage: "Status"}),
            grant.revoked ? $t({defaultMessage: "Revoked"}) : $t({defaultMessage: "Active"}),
        );
        if (
            box.attr("id") === "agent-resource-grants" &&
            grant.allowed_actions.includes("revoke")
        ) {
            button(row, $t({defaultMessage: "Revoke grant"}), "grant-revoke", grant.id);
        }
    }
}
export async function load_grants(
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
            box.text($t({defaultMessage: "Grant status is unknown."}));
        }
    }
}
export function open_grant_editor(kind: GrantKind, id: string, revision: number, label: string): void {
    const editor = begin_editor("grant", `${kind}:${id}`);
    grant_target = {kind, id, revision, label};
    const box = $("#agent-grant-editor").empty().prop("hidden", false);
    $("<h4>")
        .text($t({defaultMessage: "Share {label}"}, {label}))
        .appendTo(box);
    $("<p>")
        .text(
            $t({
                defaultMessage:
                    "Each resource owner grants only their own resource. A grant does not give access to other dependencies.",
            }),
        )
        .appendTo(box);
    const form = $("<form id='agent-resource-grant-form' class='agent-inline-form'>").appendTo(box);
    $("<label for='agent-grant-principal-kind' class='settings-field-label'>")
        .text($t({defaultMessage: "Audience type"}))
        .appendTo(form);
    const principal_kind = $(
        "<select id='agent-grant-principal-kind' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    option(principal_kind, "user", $t({defaultMessage: "Person"}));
    option(principal_kind, "group", $t({defaultMessage: "Group"}));
    $("<label for='agent-grant-principal' class='settings-field-label'>")
        .text($t({defaultMessage: "Audience"}))
        .appendTo(form);
    const principal = $(
        "<select id='agent-grant-principal' required class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    for (const user of people.get_realm_active_human_users()) {
        option(principal, String(user.user_id), user.full_name);
    }
    $("<label for='agent-grant-actions' class='settings-field-label'>")
        .text($t({defaultMessage: "Allowed actions"}))
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
        .text($t({defaultMessage: "Conversation restriction"}))
        .appendTo(form);
    const scope = $(
        "<select id='agent-grant-scope-kind' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    option(scope, "", $t({defaultMessage: "Any authorized conversation"}));
    option(scope, "stream", $t({defaultMessage: "Channel"}));
    option(scope, "direct", $t({defaultMessage: "Direct message"}));
    $("<label for='agent-grant-channel' class='settings-field-label'>")
        .text($t({defaultMessage: "Channel"}))
        .appendTo(form);
    const channel = $(
        "<select id='agent-grant-channel' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    for (const sub of stream_data.get_unsorted_subs_with_content_access()) {
        option(channel, String(sub.stream_id), sub.name);
    }
    $("<label for='agent-grant-topic' class='settings-field-label'>")
        .text($t({defaultMessage: "Topic (optional)"}))
        .appendTo(form);
    $("<input id='agent-grant-topic' maxlength='200' class='settings_text_input'>").appendTo(form);
    $("<label for='agent-grant-dm' class='settings-field-label'>")
        .text($t({defaultMessage: "Direct message participants"}))
        .appendTo(form);
    const dm = $(
        "<select id='agent-grant-dm' multiple size='5' class='settings_select bootstrap-focus-style'>",
    ).appendTo(form);
    for (const user of people.get_realm_active_human_users()) {
        option(dm, String(user.user_id), user.full_name);
    }
    if (kind === "profile") {
        $("<label for='agent-grant-repository' class='settings-field-label'>")
            .text($t({defaultMessage: "Repository restriction"}))
            .appendTo(form);
        const repository = $(
            "<select id='agent-grant-repository' class='settings_select bootstrap-focus-style'>",
        ).appendTo(form);
        option(repository, "", $t({defaultMessage: "No repository restriction"}));
        for (const item of repositories) {
            option(repository, item.id, item.workspace_alias);
        }
    }
    $("<label for='agent-grant-expiry' class='settings-field-label'>")
        .text($t({defaultMessage: "Expiry (optional)"}))
        .appendTo(form);
    $("<input id='agent-grant-expiry' type='datetime-local' class='settings_text_input'>").appendTo(
        form,
    );
    $("<button type='submit' class='action-button action-button-solid-brand'>")
        .text($t({defaultMessage: "Create grant"}))
        .appendTo(form);
    $(
        "<button type='button' id='agent-grant-close' class='action-button action-button-subtle-neutral'>",
    )
        .text($t({defaultMessage: "Close sharing"}))
        .appendTo(box);
    $("<p id='agent-grant-result' role='status'>").appendTo(box);
    $("<div id='agent-resource-grants'>").appendTo(box);
    void load_grants(kind, id, $("#agent-resource-grants"), editor);
    form.find("select").first().trigger("focus");
}
function read_grant_form(): {
    principal_kind: string;
    principal_id: number;
    actions: string[];
    scope_kind: string;
    channel_id: number;
    topic: string;
    participants: number[];
    repository_id: string;
    expiry: string;
} {
    const selected_actions = $("#agent-grant-actions").val();
    const actions = Array.isArray(selected_actions)
        ? selected_actions.map(String)
        : selected_actions
          ? [String(selected_actions)]
          : [];
    const dm = $("#agent-grant-dm").val();
    const participants = (Array.isArray(dm) ? dm : dm ? [dm] : []).map(Number);
    return {
        principal_kind: value("#agent-grant-principal-kind"),
        principal_id: number("#agent-grant-principal"),
        actions,
        scope_kind: value("#agent-grant-scope-kind"),
        channel_id: number("#agent-grant-channel"),
        topic: value("#agent-grant-topic"),
        participants,
        repository_id: value("#agent-grant-repository"),
        expiry: value("#agent-grant-expiry"),
    };
}
// Pure: computes the create-grant request body from an already-read form
// and its target. Never touches the DOM, so a caller can unit test it with
// a plain object instead of a live grant editor.
export function build_grant_payload(
    form: ReturnType<typeof read_grant_form>,
    target: {kind: GrantKind; id: string; revision: number},
): Record<string, unknown> {
    const scope =
        form.scope_kind === "stream"
            ? {kind: "stream", stream_id: form.channel_id, topic: form.topic || null}
            : form.scope_kind === "direct"
              ? {kind: "direct", participant_user_ids: form.participants}
              : null;
    return {
        target_kind: target.kind,
        target_id: target.id,
        expected_revision: target.revision,
        ...(form.principal_kind === "group"
            ? {principal_group_id: form.principal_id}
            : {principal_user_id: form.principal_id}),
        actions: form.actions,
        scope,
        ...(target.kind === "profile" && form.repository_id
            ? {repository_id: form.repository_id}
            : {}),
        expires_at: form.expiry ? new Date(form.expiry).toISOString() : null,
    };
}
// Binds the resource-grant editor's own form and action handlers. Separate
// from settings_agents.ts's general handlers, which still decide *which*
// profile, runner, provider, or repository a "grant-open-*" or
// "repair-grants" click resolves to before calling open_grant_editor above.
export function bind_grant_handlers(): void {
    const root = $(document);
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
        const form = read_grant_form();
        if (
            !form.principal_id ||
            form.actions.length === 0 ||
            (form.scope_kind === "direct" && form.participants.length === 0) ||
            (form.scope_kind === "stream" && !form.channel_id)
        ) {
            $("#agent-grant-result").text(
                $t({
                    defaultMessage:
                        "Select an audience, actions, and a valid conversation restriction.",
                }),
            );
            return;
        }
        const payload = build_grant_payload(form, target);
        void api
            .create_grant(payload)
            .then(() => {
                if (!owns_editor(token, editor, "grant", `${target.kind}:${target.id}`)) {
                    return;
                }
                $("#agent-grant-result").text(
                    draft === draft_revision
                        ? $t({defaultMessage: "Grant created."})
                        : $t({defaultMessage: "Grant created. Newer form choices remain."}),
                );
                void load_grants(target.kind, target.id, $("#agent-resource-grants"), editor);
            })
            .catch(() => {
                if (owns_editor(token, editor, "grant", `${target.kind}:${target.id}`)) {
                    $("#agent-grant-result").text(
                        $t({
                            defaultMessage:
                                "Grant was not created. Check the target revision and audience.",
                        }),
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
        if (action !== "grant-revoke") {
            return;
        }
        const id = $(this).attr("data-agent-id") ?? "";
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
                        $("#agent-grant-result").text(
                            $t({defaultMessage: "Grant revocation failed."}),
                        );
                    }
                });
        });
    });
}
function shared_with_label(entry: {principal_kind: "user" | "group"; principal_id: number}): string {
    if (entry.principal_kind === "group") {
        return (
            user_groups.get_realm_user_groups().find((item) => item.id === entry.principal_id)
                ?.name ?? $t({defaultMessage: "Restricted group"})
        );
    }
    return (
        people.maybe_get_user_by_id(entry.principal_id)?.full_name ??
        $t({defaultMessage: "Restricted user"})
    );
}
export function render_shared_with(box: JQuery, profile: api.AgentProfile): void {
    box.empty();
    const shared_with = profile.shared_with ?? [];
    if (shared_with.length === 0) {
        $("<p class='agent-empty'>")
            .text($t({defaultMessage: "Not shared with anyone yet."}))
            .appendTo(box);
        return;
    }
    for (const entry of shared_with) {
        const row = $("<div class='agent-card'>").appendTo(box);
        line(row, $t({defaultMessage: "Shared with"}), shared_with_label(entry));
        line(
            row,
            $t({defaultMessage: "Access"}),
            entry.complete
                ? $t({defaultMessage: "Ready"})
                : $t({defaultMessage: "Waiting on another resource owner"}),
        );
        $("<button type='button' class='action-button action-button-subtle-neutral'>")
            .text($t({defaultMessage: "Stop sharing"}))
            .attr("data-agent-share-kind", entry.principal_kind)
            .attr("data-agent-share-id", String(entry.principal_id))
            .appendTo(row);
    }
}
// The key a new profile's create request last used, kept in sessionStorage
// (not module state) so a page reload before the response arrives can
// still recover it.
export function pending_key(): string {
    return `grow-agent-profile:${window.location.origin}:${current_user.user_id}`;
}
export function pending_creation_keys(): string[] {
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
export function register_pending_key(key: string): void {
    sessionStorage.setItem(
        `${pending_key()}:submitted`,
        JSON.stringify([...new Set([...pending_creation_keys(), key])]),
    );
}
// Exported so settings_agents.ts's shared cancel handler, and its own
// recover_pending_creation, can forget a key without re-deriving
// sessionStorage's own list of pending keys.
export function remove_pending_key(key: string): void {
    const storage = pending_key();
    if (sessionStorage.getItem(storage) === key) {
        sessionStorage.removeItem(storage);
    }
    const keys = pending_creation_keys().filter((item) => item !== key);
    sessionStorage.setItem(`${storage}:submitted`, JSON.stringify(keys));
}
