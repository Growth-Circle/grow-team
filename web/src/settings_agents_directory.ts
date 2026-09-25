/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, promise/prefer-await-to-then, promise/always-return, @typescript-eslint/consistent-type-assertions -- This controller uses static jQuery markup and fenced delegated callbacks. */
import $ from "jquery";

import * as api from "./agent_api.ts";
import {
    action_button_label,
    auth_state_label,
    data_scope_label,
    default_mode_label,
    desired_state_label,
    host_kind_label,
    model_location_label,
    presence_label,
    readiness_label,
    requirement_sentence,
    setup_phase_label,
    sharing_label,
    team_default_badge_label,
} from "./agent_settings_labels.ts";
import {derived_budget_defaults, new_client_key} from "./agent_ui_state.ts";
import * as confirm_dialog from "./confirm_dialog.ts";
import {$t, $t_html} from "./i18n.ts";
import * as people from "./people.ts";
import {
    announce,
    begin_editor,
    button,
    catalog,
    current,
    default_network,
    draft_revision,
    form_visit,
    hide_editors,
    line,
    load_choices,
    load_grants,
    number,
    option,
    owns_editor,
    pending_key,
    providers,
    register_pending_key,
    remove_pending_key,
    render_shared_with,
    repositories,
    runner_label,
    runners,
    set_editor_target,
    technical_details,
    value,
    visit,
} from "./settings_agents_core.ts";
// Devices exports this one binding for the pairing hint; directory does not
// export anything back, so this import does not cycle.
import {pairing_runner_hint} from "./settings_agents_devices.ts";
import {current_user} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import * as user_groups from "./user_groups.ts";

const steps = [
    $t({defaultMessage: "Identity"}),
    $t({defaultMessage: "Runner"}),
    $t({defaultMessage: "Runtime"}),
    $t({defaultMessage: "Work"}),
    $t({defaultMessage: "Access"}),
    $t({defaultMessage: "Review"}),
];
let directory_revision = 0;
let directory_request = 0;
let profile_offset = 0;
export let profiles: api.AgentProfile[] = [];
// The directory highlights the team default profile with a badge; this is
// only known by asking the team-default endpoint separately, since the
// profile projection itself carries no such flag.
let team_default_profile_id: string | undefined;
export let selected_profile: api.AgentProfile | undefined;
export let profile_key = "";
export let profile_submitted = false;
let step = 0;
// True once the person edits a budget field by hand, so a later model
// connection change stops overwriting their edit.
let budget_dirty = false;

export function reset_state(): void {
    profiles = [];
    team_default_profile_id = undefined;
    selected_profile = undefined;
}

export function sharing_profile_label(profile: api.AgentProfile): string {
    return sharing_label(
        profile.owner.id === current_user.user_id,
        profile.shared_with?.length ?? 0,
    );
}
// Fills the token budget fields from the selected model connection, unless
// the person already edited them by hand in this form.
export function fill_budget_defaults(): void {
    if (budget_dirty) {
        return;
    }
    const provider = providers.find((item) => item.id === value("#agent-profile-provider"));
    const defaults = derived_budget_defaults(provider);
    $("#agent-profile-input-tokens").val(defaults.input_tokens);
    $("#agent-profile-output-tokens").val(defaults.output_tokens);
}
export function provider_location(profile: api.AgentProfile): string {
    const provider = profile.provider;
    if (!provider) {
        return $t({defaultMessage: "No model connection"});
    }
    // A shared provider never discloses an owner endpoint.
    const base = provider.base_url
        ? `${provider.name} · ${provider.base_url}`
        : $t({defaultMessage: "{name} · endpoint private"}, {name: provider.name});
    return provider.model_location
        ? `${base} · ${model_location_label(provider.model_location)}`
        : base;
}
// Pure: builds the directory cards and pagination-independent list content
// into $list from already-fetched data. load_profiles() below is the only
// caller that also updates the page-count text and the Previous/Next
// buttons, since those need the request's offset as well as this data.
export function render_profiles(
    $list: JQuery,
    data: {
        profiles: api.AgentProfile[];
        count: number;
        team_default_profile_id: string | undefined;
    },
): void {
    $list.empty();
    if (data.profiles.length === 0) {
        $("<p>")
            .text($t({defaultMessage: "No profiles match these filters."}))
            .appendTo($list);
    }
    for (const profile of data.profiles) {
        const card = $("<article class='agent-card'>").appendTo($list);
        $("<h4>").text(profile.name).appendTo(card);
        if (profile.id === data.team_default_profile_id) {
            $("<p class='agent-badge'>").text(team_default_badge_label()).appendTo(card);
        }
        line(card, $t({defaultMessage: "Owner"}), profile.owner.name);
        line(card, $t({defaultMessage: "Sharing"}), sharing_profile_label(profile));
        line(card, $t({defaultMessage: "Mode"}), default_mode_label(profile.default_mode));
        line(
            card,
            $t({defaultMessage: "Profile state"}),
            desired_state_label(profile.desired_state),
        );
        line(card, $t({defaultMessage: "Readiness"}), readiness_label(profile.readiness_state));
        line(
            card,
            $t({defaultMessage: "Runner presence"}),
            presence_label(profile.runner?.observed_presence ?? "unknown"),
        );
        line(
            card,
            $t({defaultMessage: "Declared device category"}),
            host_kind_label(profile.runner?.host_kind ?? "unknown"),
        );
        line(card, $t({defaultMessage: "Tool runner"}), runner_label(profile.runner));
        line(card, $t({defaultMessage: "Model location"}), provider_location(profile));
        line(
            card,
            $t({defaultMessage: "Access"}),
            profile.access.complete
                ? $t({defaultMessage: "Complete"})
                : $t({defaultMessage: "Partial"}),
        );
        const controls = $("<div class='agent-actions'>").appendTo(card);
        button(controls, $t({defaultMessage: "Details"}), "profile-detail", profile.id);
        if (
            profile.access.complete &&
            profile.desired_state === "enabled" &&
            profile.command_allowed !== false
        ) {
            button(controls, $t({defaultMessage: "Create task"}), "create-task", profile.id);
        }
        if (profile.allowed_actions.includes("edit")) {
            button(controls, action_button_label("edit"), "profile-edit", profile.id);
        }
        if (profile.allowed_actions.includes("pause") && profile.desired_state === "enabled") {
            button(controls, action_button_label("pause"), "profile-pause", profile.id);
        }
        if (profile.allowed_actions.includes("archive")) {
            button(controls, action_button_label("archive"), "profile-archive", profile.id);
        }
    }
}
export async function load_profiles(): Promise<void> {
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
        const [result, default_result] = await Promise.all([
            api.list_profiles(filters),
            // The badge is a convenience, not required reading, so a failed
            // lookup here must not fail the whole directory load.
            api.get_team_default().catch(() => undefined),
        ]);
        if (
            !current(token) ||
            filter_revision !== directory_revision ||
            request !== directory_request
        ) {
            return;
        }
        profiles = result.profiles;
        team_default_profile_id = default_result?.default.profile?.id;
        render_profiles($("#agent-profile-list"), {
            profiles,
            count: result.count,
            team_default_profile_id,
        });
        $("#agent-directory-count").text(
            $t(
                {defaultMessage: "{count} authorized profiles · page {page}"},
                {count: result.count, page: Math.floor(profile_offset / 20) + 1},
            ),
        );
        $("#agent-directory-prev").prop("disabled", profile_offset === 0);
        $("#agent-directory-next").prop(
            "disabled",
            profile_offset + profiles.length >= result.count,
        );
    } catch {
        if (
            current(token) &&
            filter_revision === directory_revision &&
            request === directory_request
        ) {
            profiles = [];
            $("#agent-profile-list").empty();
            announce($t({defaultMessage: "Profile status is unknown. Retry the directory."}));
        }
    }
}
export function update_runtime_choices(clear = true): void {
    const runner = runners.find((item) => item.id === value("#agent-profile-runner"));
    const adapter = $("#agent-profile-adapter").empty();
    const sandbox = $("#agent-profile-sandbox").empty();
    const provider = $("#agent-profile-provider").empty();
    const repository = $("#agent-profile-repository").empty();
    option(provider, "", $t({defaultMessage: "No model connection"}));
    option(repository, "", $t({defaultMessage: "No repository"}));
    for (const item of catalog(runner).adapters) {
        option(
            adapter,
            `${item.id}@${item.version}`,
            `${item.id} ${item.version} · ${auth_state_label(item.auth_state)}`,
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
        $t({
            defaultMessage:
                "A shared connection copies its owner's approved network policy into this profile when saved. Later connection changes do not change this saved policy. Localhost refers to the selected runner.",
        }),
    );
}
export function update_step(): void {
    $("#agent-profile-form [data-agent-step]").prop("hidden", true);
    $(`#agent-profile-form [data-agent-step='${step}']`).prop("hidden", false);
    $("#agent-profile-step-label").text(
        $t(
            {defaultMessage: "Step {current} of {total}: {name}"},
            {current: step + 1, total: steps.length, name: steps[step]},
        ),
    );
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
        line(box, $t({defaultMessage: "Profile"}), value("#agent-profile-name"));
        line(
            box,
            $t({defaultMessage: "Device"}),
            runner
                ? `${runner.name} · owner ${people.maybe_get_user_by_id(runner.owner_id)?.full_name ?? $t({defaultMessage: "Authorized owner"})}`
                : $t({defaultMessage: "Select a device"}),
        );
        line(box, $t({defaultMessage: "Adapter"}), value("#agent-profile-adapter"));
        line(box, $t({defaultMessage: "Sandbox"}), value("#agent-profile-sandbox"));
        line(box, $t({defaultMessage: "Runtime mode"}), value("#agent-profile-mode"));
        line(
            box,
            $t({defaultMessage: "Default task"}),
            default_mode_label(value("#agent-profile-default-mode")),
        );
        line(
            box,
            $t({defaultMessage: "Model"}),
            provider
                ? `${provider.name} · ${provider.model_id} · owner ${people.maybe_get_user_by_id(provider.owner_id)?.full_name ?? $t({defaultMessage: "Authorized owner"})}`
                : $t({defaultMessage: "No model connection"}),
        );
        line(
            box,
            $t({defaultMessage: "Data sent to model"}),
            provider
                ? provider.data_scope.map((scope) => data_scope_label(scope)).join(", ")
                : $t({defaultMessage: "No model data scope"}),
        );
        line(
            box,
            $t({defaultMessage: "Repository"}),
            repository
                ? `${repository.workspace_alias} · owner ${people.maybe_get_user_by_id(repository.owner_id)?.full_name ?? $t({defaultMessage: "Authorized owner"})}`
                : $t({defaultMessage: "None"}),
        );
        line(
            box,
            $t({defaultMessage: "Required checks"}),
            repository?.required_checks?.length
                ? $t(
                      {defaultMessage: "{count} configured by repository owner"},
                      {count: repository.required_checks.length},
                  )
                : $t({defaultMessage: "No visible required checks"}),
        );
        line(
            box,
            $t({defaultMessage: "Tools and actions"}),
            actions.length > 0 ? actions.join(", ") : $t({defaultMessage: "None selected"}),
        );
        line(
            box,
            $t({defaultMessage: "Budget"}),
            $t(
                {
                    defaultMessage:
                        "{active} active seconds · {input} input tokens · {output} output tokens",
                },
                {
                    active: number("#agent-profile-active-seconds"),
                    input: number("#agent-profile-input-tokens"),
                    output: number("#agent-profile-output-tokens"),
                },
            ),
        );
        line(
            box,
            $t({defaultMessage: "Hard cost cap"}),
            payload["hard_cost_cap"] === true
                ? $t({defaultMessage: "Enabled"})
                : $t({defaultMessage: "Not enabled"}),
        );
        const capabilities = provider?.capabilities;
        const report =
            capabilities && typeof capabilities === "object"
                ? (capabilities as Record<string, unknown>)
                : {};
        line(
            box,
            $t({defaultMessage: "Capabilities not yet tested"}),
            !provider ||
                report["chat_ready"] !== true ||
                (value("#agent-profile-default-mode") === "code" && report["code_ready"] !== true)
                ? $t({defaultMessage: "Save a draft, then run a probe before enable."})
                : $t({
                      defaultMessage:
                          "A new profile revision still needs its own probe before enable.",
                  }),
        );
    }
}
export function open_profile(
    profile?: api.AgentProfile,
    editor = begin_editor("profile", profile?.id ?? ""),
): void {
    if (!owns_editor(visit, editor, "profile", profile?.id ?? "")) {
        return;
    }
    profile_submitted = false;
    selected_profile = profile;
    step = 0;
    budget_dirty = false;
    $("#agent-profile-form").trigger("reset").prop("hidden", false);
    $("#agent-profile-form-title").text(
        profile
            ? $t({defaultMessage: "Edit {name}"}, {name: profile.name})
            : $t({defaultMessage: "Create agent profile"}),
    );
    $("#agent-profile-result").text("");
    // Only a realm administrator or owner may save a Team management
    // profile; the option stays visible for everyone but disabled.
    const can_manage_team = current_user.is_admin || current_user.is_owner;
    $("#agent-profile-default-mode-manage").prop("disabled", !can_manage_team);
    $("#agent-profile-manage-note").prop("hidden", can_manage_team);
    const runner_select = $("#agent-profile-runner").empty();
    for (const runner of runners) {
        option(runner_select, runner.id, runner_label(runner));
    }
    if (profile) {
        $("#agent-profile-name").val(profile.name);
        $("#agent-profile-description").val(profile.description);
        $("#agent-profile-instructions").val(profile.configuration?.instructions ?? "");
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
        fill_budget_defaults();
    }
    update_step();
    $("#agent-profile-name").trigger("focus");
}
export function profile_payload(): Record<string, unknown> {
    const provider_id = value("#agent-profile-provider");
    const provider = providers.find((item) => item.id === provider_id);
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
    return {
        name: value("#agent-profile-name"),
        description: value("#agent-profile-description"),
        instructions: value("#agent-profile-instructions"),
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
export async function save_profile(): Promise<void> {
    const token = visit;
    const form_revision = draft_revision;
    const form_token = form_visit;
    const request_profile = selected_profile;
    const request_target = request_profile?.id ?? "";
    const payload = profile_payload();
    if (!payload["name"] || !payload["adapter_id"] || !payload["sandbox_alias"]) {
        $("#agent-profile-result").text(
            $t({defaultMessage: "Complete the identity, adapter, and sandbox fields."}),
        );
        return;
    }
    const is_edit = Boolean(request_profile);
    const key = profile_key;
    profile_submitted = true;
    if (!is_edit && key) {
        register_pending_key(key);
    }
    $("#agent-profile-result").text($t({defaultMessage: "Saving draft…"}));
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
            set_editor_target(profile.id);
        }
        if (draft_revision !== form_revision) {
            $("#agent-profile-result").text(
                $t({defaultMessage: "Draft saved. Your newer edits remain in the form."}),
            );
            return;
        }
        $("#agent-profile-result").text(
            $t({defaultMessage: "Draft saved. Run a probe, then enable it explicitly."}),
        );
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
                set_editor_target(recovered.profile.id);
                $("#agent-profile-result").text(
                    $t({
                        defaultMessage:
                            "The server saved this profile. Its identity was recovered. Your current form edits remain.",
                    }),
                );
                return;
            } catch {
                /* The original request may not have reached the server. */
            }
        }
        if (owns_editor(token, form_token, "profile", request_target)) {
            $("#agent-profile-result").text(
                $t({
                    defaultMessage:
                        "Save status is unknown. Review the draft and retry with the same identity.",
                }),
            );
        }
    }
}
// Pure-ish: builds the profile detail card into $detail from already-fetched
// data (only technical_details, line, and button touch the DOM, each
// building fresh elements under $detail rather than a fixed id).
export function render_profile_detail(
    $detail: JQuery,
    data: {
        profile: api.AgentProfile;
        attachments: Awaited<ReturnType<typeof api.get_profile>>["attachments"];
        setup: api.AgentSetup | null;
        editor: number;
    },
): void {
    const {profile, attachments, setup, editor} = data;
    $detail.empty();
    $("<h4>").text(profile.name).appendTo($detail);
    line($detail, $t({defaultMessage: "Owner"}), profile.owner.name);
    line($detail, $t({defaultMessage: "Sharing"}), sharing_profile_label(profile));
    line($detail, $t({defaultMessage: "Mode"}), default_mode_label(profile.default_mode));
    line($detail, $t({defaultMessage: "Saved state"}), desired_state_label(profile.desired_state));
    line($detail, $t({defaultMessage: "Readiness"}), readiness_label(profile.readiness_state));
    technical_details($detail, (box) => {
        line(
            box,
            $t({defaultMessage: "Readiness revision"}),
            profile.readiness_revision ?? $t({defaultMessage: "None"}),
        );
    });
    line($detail, $t({defaultMessage: "Runner"}), runner_label(profile.runner));
    line($detail, $t({defaultMessage: "Model"}), provider_location(profile));
    line(
        $detail,
        $t({defaultMessage: "Repository"}),
        profile.repository?.workspace_alias ?? $t({defaultMessage: "None"}),
    );
    if (setup) {
        line($detail, $t({defaultMessage: "Setup"}), setup_phase_label(setup.phase));
        technical_details($detail, (box) => {
            line(
                box,
                $t({defaultMessage: "Setup tested profile revision"}),
                setup.profile_revision,
            );
            line(
                box,
                $t({defaultMessage: "Provider test revision"}),
                setup.provider_config_version ?? $t({defaultMessage: "None"}),
            );
        });
        if (setup.profile_revision !== profile.revision) {
            line(
                $detail,
                $t({defaultMessage: "Probe evidence"}),
                $t({
                    defaultMessage: "This setup tested an older profile revision. Run a new probe.",
                }),
            );
        }
        for (const requirement of setup.requirements) {
            const row = $("<div class='agent-card'>").appendTo($detail);
            line(row, $t({defaultMessage: "Requirement"}), requirement_sentence(requirement.code));
            technical_details(row, (box) => {
                line(box, $t({defaultMessage: "Surface"}), requirement.surface);
                line(box, $t({defaultMessage: "Code"}), requirement.code);
            });
            const repair = {
                connect_runner: [$t({defaultMessage: "Open devices"}), "repair-devices"],
                register_workspace: [$t({defaultMessage: "Open repositories"}), "repair-devices"],
                install_adapter: [$t({defaultMessage: "Open devices"}), "repair-devices"],
                login_vendor: [
                    $t({defaultMessage: "Open model connections"}),
                    "repair-connections",
                ],
                edit_provider: [
                    $t({defaultMessage: "Open model connections"}),
                    "repair-connections",
                ],
                probe_again: [$t({defaultMessage: "Run another probe"}), "profile-probe"],
                request_grant: [$t({defaultMessage: "Review resource grants"}), "repair-grants"],
                configure_sandbox: [$t({defaultMessage: "Edit profile runtime"}), "profile-edit"],
                view_diagnostic: [
                    $t({defaultMessage: "Ask the resource owner for diagnostics"}),
                    "repair-devices",
                ],
            }[requirement.action];
            if (repair) {
                button(row, repair[0]!, repair[1]!, profile.id);
            }
        }
    } else {
        line(
            $detail,
            $t({defaultMessage: "Setup"}),
            $t({defaultMessage: "No setup result yet. Save, probe, then enable explicitly."}),
        );
    }
    for (const [name, accessible] of [
        [$t({defaultMessage: "Combined"}), profile.access.complete],
        [$t({defaultMessage: "Device"}), profile.access.runner],
        [$t({defaultMessage: "Model connection"}), profile.access.provider],
        [$t({defaultMessage: "Repository"}), profile.access.repository],
    ] as const) {
        line(
            $detail,
            $t({defaultMessage: "{name} access for you"}, {name}),
            accessible
                ? $t({defaultMessage: "Available"})
                : $t({defaultMessage: "Requires the resource owner's grant"}),
        );
    }
    if (!profile.access.complete) {
        line(
            $detail,
            $t({defaultMessage: "Next step"}),
            $t({
                defaultMessage:
                    "Ask each listed resource owner for the missing grant. Your profile grant alone is insufficient.",
            }),
        );
    }
    if (profile.desired_state === "draft" && profile.readiness_state === "ready") {
        line(
            $detail,
            $t({defaultMessage: "Activation"}),
            $t({defaultMessage: "Ready draft. Explicit enable is required."}),
        );
    }
    if (profile.runner?.observed_presence === "offline" && profile.desired_state === "enabled") {
        line(
            $detail,
            $t({defaultMessage: "Queue"}),
            $t({defaultMessage: "Offline runner. New work can remain queued if authorized."}),
        );
    }
    line(
        $detail,
        $t({defaultMessage: "Channel attachments"}),
        attachments.length > 0
            ? attachments
                  .map((item) =>
                      item.bot_member
                          ? $t({defaultMessage: "{name}: bot member"}, {name: item.name})
                          : $t({defaultMessage: "{name}: not a member"}, {name: item.name}),
                  )
                  .join(", ")
            : $t({defaultMessage: "None visible"}),
    );
    const controls = $("<div class='agent-actions'>").appendTo($detail);
    if (
        profile.access.complete &&
        profile.desired_state === "enabled" &&
        profile.command_allowed !== false
    ) {
        button(controls, $t({defaultMessage: "Create task"}), "create-task", profile.id);
    }
    for (const action of ["edit", "probe", "enable", "pause", "archive", "test_task"] as const) {
        if (
            profile.allowed_actions.includes(action) &&
            (action !== "pause" || profile.desired_state === "enabled")
        ) {
            button(controls, action_button_label(action), `profile-${action}`, profile.id);
        }
    }
    if (profile.allowed_actions.includes("edit")) {
        const channel = $("<form class='agent-inline-form'>")
            .attr("id", "agent-attach-form")
            .appendTo($detail);
        $("<label for='agent-attach-stream' class='settings-field-label'>")
            .text($t({defaultMessage: "Channel"}))
            .appendTo(channel);
        const channel_choice = $(
            "<select id='agent-attach-stream' required class='settings_select bootstrap-focus-style'>",
        ).appendTo(channel);
        option(channel_choice, "", $t({defaultMessage: "Select a channel"}));
        for (const sub of stream_data.get_unsorted_subs_with_content_access()) {
            option(channel_choice, String(sub.stream_id), sub.name);
        }
        $("<button type='submit' class='action-button action-button-solid-brand'>")
            .text($t({defaultMessage: "Attach to channel"}))
            .appendTo(channel);
        button(
            controls,
            $t({defaultMessage: "Manage profile grants"}),
            "grant-open-profile",
            profile.id,
        );
    }
    if (profile.allowed_actions.includes("edit")) {
        $("<h5>")
            .text($t({defaultMessage: "Share with team"}))
            .appendTo($detail);
        $("<p class='agent-note'>")
            .text(
                $t({
                    defaultMessage:
                        "Tasks from people you share with run on your device and use your model connection.",
                }),
            )
            .appendTo($detail);
        render_shared_with($("<div id='agent-profile-shared-with'>").appendTo($detail), profile);
        const share_form = $("<form id='agent-share-form' class='agent-inline-form'>").appendTo(
            $detail,
        );
        $("<label for='agent-share-principal-kind' class='settings-field-label'>")
            .text($t({defaultMessage: "Share with"}))
            .appendTo(share_form);
        const share_kind = $(
            "<select id='agent-share-principal-kind' class='settings_select bootstrap-focus-style'>",
        ).appendTo(share_form);
        option(share_kind, "user", $t({defaultMessage: "Person"}));
        option(share_kind, "group", $t({defaultMessage: "Group"}));
        const share_principal = $(
            "<select id='agent-share-principal' required class='settings_select bootstrap-focus-style'>",
        ).appendTo(share_form);
        for (const user of people.get_realm_active_human_users()) {
            option(share_principal, String(user.user_id), user.full_name);
        }
        const control_label = $("<label class='checkbox-label'>").appendTo(share_form);
        $("<input type='checkbox' id='agent-share-control'>").appendTo(control_label);
        $("<span>")
            .text($t({defaultMessage: "Let them stop and resume tasks"}))
            .appendTo(control_label);
        const review_label = $("<label class='checkbox-label'>").appendTo(share_form);
        $("<input type='checkbox' id='agent-share-review'>").appendTo(review_label);
        $("<span>")
            .text($t({defaultMessage: "Let them review results"}))
            .appendTo(review_label);
        $("<button type='submit' class='action-button action-button-solid-brand'>")
            .text($t({defaultMessage: "Share"}))
            .appendTo(share_form);
        $("<p id='agent-share-result' role='status'>").appendTo(share_form);
    }
    $("<h5>")
        .text($t({defaultMessage: "Grants"}))
        .appendTo($detail);
    $("<div id='agent-profile-grants'>").appendTo($detail);
    $(
        "<button type='button' id='agent-detail-close' class='action-button action-button-subtle-neutral'>",
    )
        .text($t({defaultMessage: "Close details"}))
        .appendTo($detail);
    void load_grants("profile", profile.id, $("#agent-profile-grants"), editor, "profile-detail");
}
export async function open_profile_detail(id: string): Promise<void> {
    const token = visit;
    const editor = begin_editor("profile-detail", id);
    try {
        const result = await api.get_profile(id);
        if (!owns_editor(token, editor, "profile-detail", id)) {
            return;
        }
        hide_editors();
        selected_profile = result.profile;
        const $detail = $("#agent-profile-detail").prop("hidden", false);
        render_profile_detail($detail, {
            profile: result.profile,
            attachments: result.attachments,
            setup: result.setup,
            editor,
        });
        $detail.find("h4").trigger("focus");
    } catch {
        if (owns_editor(token, editor, "profile-detail", id)) {
            announce($t({defaultMessage: "Profile details are unavailable."}));
        }
    }
}
export async function profile_control(
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
                ? $t({
                      defaultMessage:
                          "Probe started. Readiness will update after the runner reports.",
                  })
                : $t({defaultMessage: "Profile {action} accepted."}, {action}),
        );
        await load_profiles();
        if (owns_editor(token, editor, "profile-control", id)) {
            await open_profile_detail(id);
        }
    } catch {
        if (owns_editor(token, editor, "profile-control", id)) {
            announce(
                $t(
                    {defaultMessage: "Profile {action} failed. Refresh its revision and retry."},
                    {action},
                ),
            );
        }
    }
}
export async function submit_test_task(id: string): Promise<void> {
    const token = visit;
    try {
        const result = await api.send_test_task(id, {idempotency_key: new_client_key()});
        if (!current(token)) {
            return;
        }
        announce($t({defaultMessage: "Test task sent."}));
        window.location.hash = `#agent-jobs/${result.job.id}`;
    } catch {
        if (current(token)) {
            announce($t({defaultMessage: "Test task was not sent. Check the agent and retry."}));
        }
    }
}
export function bind_handlers(): void {
    const root = $(document);
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
    root.on("input change", "#agent-directory-filter input, #agent-directory-filter select", () => {
        directory_revision += 1;
    });
    root.on("change", "#agent-profile-runner", () => {
        update_runtime_choices();
        fill_budget_defaults();
    });
    root.on("change", "#agent-profile-provider", () => {
        fill_budget_defaults();
    });
    root.on("input change", "#agent-profile-input-tokens, #agent-profile-output-tokens", () => {
        budget_dirty = true;
    });
    root.on("submit", "#agent-profile-form", (event) => {
        event.preventDefault();
        void save_profile();
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
                announce(
                    $t({
                        defaultMessage:
                            "Channel attachment saved. Profile configuration remains saved.",
                    }),
                );
                void open_profile_detail(profile.id);
            })
            .catch(() => {
                if (owns_editor(token, editor, "profile-detail", profile.id)) {
                    announce(
                        $t({
                            defaultMessage:
                                "Channel attachment failed. The saved profile remains available.",
                        }),
                    );
                }
            });
    });
    root.on("change", "#agent-share-principal-kind", () => {
        const select = $("#agent-share-principal").empty();
        if (value("#agent-share-principal-kind") === "group") {
            for (const group of user_groups.get_realm_user_groups()) {
                option(select, String(group.id), group.name);
            }
        } else {
            for (const user of people.get_realm_active_human_users()) {
                option(select, String(user.user_id), user.full_name);
            }
        }
    });
    root.on("submit", "#agent-share-form", (event) => {
        event.preventDefault();
        const profile = selected_profile;
        if (!profile) {
            return;
        }
        const token = visit;
        const editor = form_visit;
        const principal_kind = value("#agent-share-principal-kind");
        const principal_id = number("#agent-share-principal");
        if (!principal_id) {
            $("#agent-share-result").text(
                $t({defaultMessage: "Choose a person or a group to share with."}),
            );
            return;
        }
        const payload: Record<string, unknown> = {
            ...(principal_kind === "group"
                ? {principal_group_id: principal_id}
                : {principal_user_id: principal_id}),
            allow_job_control: Boolean($("#agent-share-control").prop("checked")),
            allow_job_review: Boolean($("#agent-share-review").prop("checked")),
        };
        void api
            .share_profile(profile.id, payload)
            .then(() => {
                if (!owns_editor(token, editor, "profile-detail", profile.id)) {
                    return;
                }
                announce($t({defaultMessage: "Sharing saved."}));
                void open_profile_detail(profile.id);
            })
            .catch(() => {
                if (owns_editor(token, editor, "profile-detail", profile.id)) {
                    $("#agent-share-result").text(
                        $t({
                            defaultMessage: "Sharing was not saved. Check the audience and retry.",
                        }),
                    );
                }
            });
    });
    root.on("click", "[data-agent-share-kind]", function () {
        const profile = selected_profile;
        const kind = $(this).attr("data-agent-share-kind");
        const id = Number($(this).attr("data-agent-share-id"));
        if (!profile || !kind || !id) {
            return;
        }
        const token = visit;
        const editor = form_visit;
        const payload = kind === "group" ? {principal_group_id: id} : {principal_user_id: id};
        void api
            .unshare_profile(profile.id, payload)
            .then(() => {
                if (owns_editor(token, editor, "profile-detail", profile.id)) {
                    void open_profile_detail(profile.id);
                }
            })
            .catch(() => {
                if (owns_editor(token, editor, "profile-detail", profile.id)) {
                    announce(
                        $t({defaultMessage: "Stop sharing failed. The share remains active."}),
                    );
                }
            });
    });
    root.on("click", "[data-agent-action]", function () {
        const action = $(this).attr("data-agent-action") ?? "";
        const id = $(this).attr("data-agent-id") ?? "";
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
                        announce($t({defaultMessage: "Profile edit data is unavailable."}));
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
        if (action === "profile-test_task") {
            const profile =
                selected_profile?.id === id
                    ? selected_profile
                    : profiles.find((item) => item.id === id);
            if (!profile?.allowed_actions.includes("test_task")) {
                return;
            }
            confirm_dialog.launch({
                modal_title_html: $t_html({defaultMessage: "Send a test task?"}),
                modal_content_html: $t_html({
                    defaultMessage:
                        "The agent answers one short test message in a direct message with you. This uses its model connection and can cost money.",
                }),
                modal_submit_button_text: $t({defaultMessage: "Send test task"}),
                is_compact: true,
                on_click: () => void submit_test_task(profile.id),
            });
        }
        if (action === "profile-new-on-runner") {
            const token = visit;
            const editor = begin_editor("profile");
            void load_choices(editor).then(() => {
                if (owns_editor(token, editor, "profile")) {
                    open_profile(undefined, editor);
                    if (pairing_runner_hint) {
                        $("#agent-profile-runner").val(pairing_runner_hint);
                    }
                }
            });
        }
    });
}
