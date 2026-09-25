/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, promise/prefer-await-to-then, promise/always-return, @typescript-eslint/consistent-type-assertions -- This controller uses static jQuery markup and fenced delegated callbacks. */
import $ from "jquery";

import * as api from "./agent_api.ts";
import {data_scope_label, model_location_label} from "./agent_settings_labels.ts";
import {new_client_key} from "./agent_ui_state.ts";
import {$t} from "./i18n.ts";
import {
    announce,
    begin_editor,
    button,
    current,
    default_network,
    draft_revision,
    failed,
    form_visit,
    hide_editors,
    line,
    number,
    option,
    owns_editor,
    providers,
    runners,
    value,
    visit,
} from "./settings_agents_core.ts";

let provider_request = 0;
let provider_offset = 0;
export let selected_provider: api.AgentProvider | undefined;

export function reset_state(): void {
    selected_provider = undefined;
}

function capability_label(key: string): string {
    switch (key) {
        case "chat_ready":
            return $t({defaultMessage: "Chat ready"});
        case "tool_calling":
            return $t({defaultMessage: "Tool calling"});
        case "streaming":
            return $t({defaultMessage: "Streaming"});
        case "code_ready":
            return $t({defaultMessage: "Code ready"});
        default:
            return key;
    }
}
// Pure: builds the model connection cards into $list from already-fetched
// data. load_providers() below is the only caller that also updates the
// "Show more" button, since that needs the request's offset as well.
export function render_providers(
    $list: JQuery,
    data: {providers: api.AgentProvider[]},
): void {
    $list.empty();
    if (data.providers.length === 0) {
        $("<p>")
            .text($t({defaultMessage: "No model connections are visible to you."}))
            .appendTo($list);
    }
    for (const provider of data.providers) {
        const card = $("<article class='agent-card'>").appendTo($list);
        $("<h4>").text(provider.name).appendTo(card);
        line(card, $t({defaultMessage: "Model ID"}), provider.model_id);
        line(
            card,
            $t({defaultMessage: "Runner"}),
            runners.find((item) => item.id === provider.runner_id)?.name ??
                $t({defaultMessage: "Unknown"}),
        );
        line(
            card,
            $t({defaultMessage: "Endpoint location"}),
            provider.base_url ?? $t({defaultMessage: "Private to owner"}),
        );
        if (provider.model_location) {
            line(
                card,
                $t({defaultMessage: "Model location"}),
                model_location_label(provider.model_location),
            );
        }
        line(
            card,
            $t({defaultMessage: "Data scope"}),
            provider.data_scope.map((scope) => data_scope_label(scope)).join(", "),
        );
        const capabilities = provider.capabilities;
        if (capabilities && typeof capabilities === "object") {
            const row = capabilities as Record<string, unknown>;
            for (const key of ["chat_ready", "tool_calling", "streaming", "code_ready"]) {
                line(card, capability_label(key), row[key] ?? $t({defaultMessage: "unknown"}));
            }
        }
        if (provider.allowed_actions.includes("edit")) {
            button(card, $t({defaultMessage: "Edit"}), "provider-edit", provider.id);
            button(
                card,
                $t({defaultMessage: "Manage connection grants"}),
                "grant-open-provider",
                provider.id,
            );
        }
        if (provider.allowed_actions.includes("probe")) {
            button(card, $t({defaultMessage: "Probe"}), "provider-probe", provider.id);
        }
    }
}
export async function load_providers(): Promise<void> {
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
        // `providers` and `runners` are core-owned caches every panel
        // imports the same array reference for; splice in place so they
        // all keep seeing the same reference (see settings_agents_core.ts).
        providers.splice(0, providers.length, ...rows.providers);
        runners.splice(0, runners.length, ...devices.runners);
        render_providers($("#agent-provider-list"), {providers});
        $("#agent-provider-more").prop("hidden", provider_offset + providers.length >= rows.count);
    } catch {
        if (current(token) && request === provider_request) {
            providers.splice(0);
            $("#agent-provider-list").empty();
            announce($t({defaultMessage: "Model connection status is unknown."}));
        }
    }
}
export function open_provider(
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
        provider
            ? $t({defaultMessage: "Edit {name}"}, {name: provider.name})
            : $t({defaultMessage: "Add model connection"}),
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
export async function load_provider_impact(id: string, editor: number): Promise<void> {
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
        $("<h5>")
            .text($t({defaultMessage: "Profiles affected by execution changes"}))
            .appendTo(box);
        if (affected.length === 0) {
            line(
                box,
                $t({defaultMessage: "Visible profiles"}),
                $t({defaultMessage: "None in the checked authorized pages"}),
            );
        }
        for (const profile of affected) {
            line(
                box,
                $t({defaultMessage: "Needs a new probe after change"}),
                $t(
                    {defaultMessage: "{name} · owner {owner}"},
                    {name: profile.name, owner: profile.owner.name},
                ),
            );
        }
        if (incomplete) {
            line(
                box,
                $t({defaultMessage: "Limit"}),
                $t({defaultMessage: "Only the first 500 authorized profiles were checked."}),
            );
        }
        line(
            box,
            $t({defaultMessage: "Active attempts"}),
            $t({defaultMessage: "Existing attempt snapshots keep their saved configuration."}),
        );
    } catch {
        if (owns_editor(token, editor, "provider", id)) {
            $("#agent-provider-impact").text(
                $t({
                    defaultMessage:
                        "Affected profile list is unavailable. Review it before an execution change.",
                }),
            );
        }
    }
}
export async function save_provider(): Promise<void> {
    const token = visit;
    const editor = form_visit;
    const revision = draft_revision;
    const provider = selected_provider;
    const credential = value("#agent-provider-credential");
    if (credential.includes("••") || credential === "********") {
        $("#agent-provider-result").text(
            $t({defaultMessage: "Enter a new credential, not a masked value."}),
        );
        return;
    }
    const scopes = $("#agent-provider-scopes input:checked")
        .map((_, element) => String($(element).val()))
        .get();
    if (scopes.length === 0) {
        $("#agent-provider-result").text($t({defaultMessage: "Select at least one data scope."}));
        return;
    }
    const network_host = value("#agent-provider-network-host");
    const prior_network = provider?.network;
    const prior_policy =
        prior_network && typeof prior_network === "object"
            ? (prior_network as Record<string, unknown>)
            : default_network;
    const prior_targets: unknown[] = Array.isArray(prior_policy.targets)
        ? (prior_policy.targets as unknown[])
        : [];
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
    const local_credential_ref = value("#agent-provider-local-ref");
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
        if (local_credential_ref) {
            payload["local_credential_ref"] = local_credential_ref;
        }
    } else {
        payload["runner_id"] = value("#agent-provider-runner");
        if (credential) {
            payload["credential"] = credential;
        }
        if (local_credential_ref) {
            payload["local_credential_ref"] = local_credential_ref;
        }
    }
    $("#agent-provider-result").text($t({defaultMessage: "Saving connection…"}));
    try {
        if (provider) {
            await api.update_provider(provider.id, payload);
        } else {
            await api.create_provider(payload);
        }
        if (!owns_editor(token, editor, "provider", provider?.id ?? "")) {
            return;
        }
        if (
            revision === draft_revision &&
            value("#agent-provider-credential") === credential
        ) {
            $("#agent-provider-credential").val("");
        }
        if (
            revision === draft_revision &&
            value("#agent-provider-local-ref") === local_credential_ref
        ) {
            $("#agent-provider-local-ref").val("");
        }
        $("#agent-provider-result").text(
            revision === draft_revision
                ? $t({defaultMessage: "Connection saved. Probe it explicitly."})
                : $t({defaultMessage: "Connection saved. Newer edits remain in the form."}),
        );
        if (revision === draft_revision) {
            hide_editors();
        }
        await load_providers();
    } catch {
        if (owns_editor(token, editor, "provider", provider?.id ?? "")) {
            $("#agent-provider-result").text(
                $t({
                    defaultMessage:
                        "Connection save failed. Check the fields and current revision.",
                }),
            );
        }
    }
}

export function bind_handlers(): void {
    const root = $(document);
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
    root.on("click", "[data-agent-action]", function () {
        const action = $(this).attr("data-agent-action") ?? "";
        const id = $(this).attr("data-agent-id") ?? "";
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
                        announce($t({defaultMessage: "Connection edit data is unavailable."}));
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
                        announce(
                            $t({
                                defaultMessage:
                                    "Connection probe started. Refresh to see its capability report.",
                            }),
                        );
                    }
                })
                .catch(failed(token, $t({defaultMessage: "Connection probe failed to start."})));
        }
    });
}
