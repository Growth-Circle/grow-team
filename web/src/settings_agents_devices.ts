/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, promise/prefer-await-to-then, promise/always-return, @typescript-eslint/consistent-type-assertions, no-alert -- This controller uses static jQuery markup and fenced delegated callbacks. */
import $ from "jquery";

import * as api from "./agent_api.ts";
import {host_kind_label, presence_label} from "./agent_settings_labels.ts";
import {$t} from "./i18n.ts";
import {
    announce,
    begin_editor,
    button,
    catalog,
    current,
    draft_revision,
    failed,
    form_visit,
    hide_editors,
    line,
    load_choices,
    owns_editor,
    repositories,
    runners,
    technical_details,
    value,
    visit,
} from "./settings_agents_core.ts";
import {realm} from "./state_data.ts";

let runner_request = 0;
let runner_offset = 0;
export let selected_runner: api.AgentRunner | undefined;
// Set right after a pairing approval to the one runner id that appeared in
// the device list that the approval did not already know about, so "Add
// agent on this device" can preselect it. Stays undefined when the device
// has not exchanged its pairing for a runner yet.
export let pairing_runner_hint: string | undefined;

export function reset_state(): void {
    selected_runner = undefined;
    pairing_runner_hint = undefined;
}

// Pure: builds the device cards into $list from already-fetched data.
// load_runners() below is the only caller that also updates the device
// count text and the "Show more" button, since those need the request's
// offset as well as this data.
export function render_runners(
    $list: JQuery,
    data: {runners: api.AgentRunner[]; count: number},
): void {
    $list.empty();
    if (data.runners.length === 0) {
        $("<p>")
            .text($t({defaultMessage: "No devices are visible to you."}))
            .appendTo($list);
    }
    for (const runner of data.runners) {
        const card = $("<article class='agent-card'>").appendTo($list);
        $("<h4>").text(runner.name).appendTo(card);
        line(card, $t({defaultMessage: "Declared category"}), host_kind_label(runner.host_kind));
        line(
            card,
            $t({defaultMessage: "Observed presence"}),
            presence_label(runner.observed_presence),
        );
        if (runner.fingerprint_prefix) {
            line(card, $t({defaultMessage: "Device fingerprint"}), runner.fingerprint_prefix);
        }
        if (runner.observed_presence !== "online") {
            $("<p class='agent-field-note'>")
                .text($t({defaultMessage: "This device is not connected to sanji."}))
                .appendTo(card);
        }
        technical_details(card, (box) => {
            line(box, $t({defaultMessage: "Observed at"}), runner.observed_at);
            line(box, $t({defaultMessage: "Catalog revision"}), runner.catalog_revision);
        });
        const safe = catalog(runner);
        line(
            card,
            $t({defaultMessage: "Approved adapters"}),
            safe.adapters.length > 0
                ? safe.adapters.map((item) => `${item.id} ${item.version}`).join(", ")
                : $t({defaultMessage: "Unavailable"}),
        );
        line(
            card,
            $t({defaultMessage: "Approved sandboxes"}),
            safe.sandboxes.length > 0
                ? safe.sandboxes.map((item) => item.alias).join(", ")
                : $t({defaultMessage: "Unavailable"}),
        );
        if (runner.allowed_actions.includes("edit")) {
            button(card, $t({defaultMessage: "Edit metadata"}), "runner-edit", runner.id);
            button(
                card,
                $t({defaultMessage: "Manage device grants"}),
                "grant-open-runner",
                runner.id,
            );
        }
        if (runner.allowed_actions.includes("edit")) {
            button(card, $t({defaultMessage: "Register repository"}), "repository-new", runner.id);
        }
        if (runner.allowed_actions.includes("revoke")) {
            button(card, $t({defaultMessage: "Revoke pairing"}), "runner-revoke", runner.id);
        }
    }
}
export async function load_runners(): Promise<void> {
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
        // `runners` and `repositories` are core-owned caches every panel
        // imports the same array reference for; splice in place so they all
        // keep seeing the same reference (see settings_agents_core.ts).
        runners.splice(0, runners.length, ...result.runners);
        repositories.splice(0, repositories.length, ...repository_data.repositories);
        render_runners($("#agent-runner-list"), {runners, count: result.count});
        $("#agent-device-count").text(
            $t({defaultMessage: "{count} authorized devices"}, {count: result.count}),
        );
        $("#agent-device-more").prop("hidden", runner_offset + runners.length >= result.count);
        const list = $("#agent-repository-list").empty();
        for (const repository of repositories) {
            const card = $("<article class='agent-card'>").appendTo(list);
            $("<h4>").text(repository.workspace_alias).appendTo(card);
            line(
                card,
                $t({defaultMessage: "Device"}),
                runners.find((item) => item.id === repository.runner_id)?.name ??
                    $t({defaultMessage: "Authorized device"}),
            );
            if (repository.allowed_actions.includes("manage") && repository.policy_version) {
                button(
                    card,
                    $t({defaultMessage: "Manage repository grants"}),
                    "grant-open-repository",
                    repository.id,
                );
            }
        }
    } catch {
        if (current(token) && request === runner_request) {
            runners.splice(0);
            $("#agent-runner-list").empty();
            announce($t({defaultMessage: "Device status is unknown. Retry devices."}));
        }
    }
}
export function open_runner(id: string): void {
    selected_runner = runners.find((item) => item.id === id);
    if (!selected_runner?.allowed_actions.includes("edit")) {
        return;
    }
    begin_editor("runner", id);
    $("#agent-runner-form").prop("hidden", false);
    $("#agent-runner-name").val(selected_runner.name).trigger("focus");
    $("#agent-runner-kind").val(selected_runner.host_kind);
}
type RunnerForm = {name: string; host_kind: api.AgentRunner["host_kind"]};
// Pure DOM read: the runner metadata form's own two fields.
export function read_runner_form(): RunnerForm {
    return {
        name: value("#agent-runner-name"),
        host_kind: value("#agent-runner-kind") as api.AgentRunner["host_kind"],
    };
}
// Pure: computes the update-runner-metadata request body from an
// already-read form and the runner it edits. The return type mirrors
// update_runner_metadata()'s own argument type exactly, since that API
// function (unlike its siblings) declares a specific shape rather than
// accepting a loose Record<string, unknown>.
export function build_runner_payload(
    form: RunnerForm,
    runner: {id: string; metadata_revision: number},
): {
    runner_id: string;
    expected_metadata_revision: number;
    name: string;
    host_kind: api.AgentRunner["host_kind"];
} {
    return {
        runner_id: runner.id,
        expected_metadata_revision: runner.metadata_revision,
        name: form.name,
        host_kind: form.host_kind,
    };
}
export async function save_runner(): Promise<void> {
    const runner = selected_runner;
    if (!runner) {
        return;
    }
    const token = visit;
    const editor = form_visit;
    const revision = draft_revision;
    try {
        await api.update_runner_metadata(build_runner_payload(read_runner_form(), runner));
        if (!owns_editor(token, editor, "runner", runner.id)) {
            return;
        }
        announce($t({defaultMessage: "Device metadata saved."}));
        if (revision === draft_revision) {
            hide_editors();
        }
        await load_runners();
    } catch {
        if (owns_editor(token, editor, "runner", runner.id)) {
            announce($t({defaultMessage: "Device metadata was not saved. Refresh and retry."}));
        }
    }
}
type RepositoryForm = {alias: string; origin: string; ref: string};
// Pure DOM read: the new-repository form's own three fields.
export function read_repository_form(): RepositoryForm {
    return {
        alias: value("#agent-repository-alias"),
        origin: value("#agent-repository-origin"),
        ref: value("#agent-repository-ref"),
    };
}
// Pure: computes the create-repository request body from an already-read
// form and the runner it registers against.
export function build_repository_payload(
    form: RepositoryForm,
    runner: {id: string},
): Record<string, unknown> {
    return {
        runner_id: runner.id,
        workspace_alias: form.alias,
        canonical_origin: form.origin || null,
        allowed_refs: [form.ref],
        required_checks: [],
    };
}
export async function save_repository(): Promise<void> {
    const runner = selected_runner;
    if (!runner) {
        return;
    }
    const token = visit;
    const editor = form_visit;
    const submitted_draft = draft_revision;
    try {
        await api.create_repository(build_repository_payload(read_repository_form(), runner));
        if (!owns_editor(token, editor, "repository", runner.id)) {
            return;
        }
        if (submitted_draft === draft_revision) {
            hide_editors();
            announce(
                $t({
                    defaultMessage: "Repository registered. Add explicit grants before shared use.",
                }),
            );
        } else {
            $("#agent-repository-result").text(
                $t({defaultMessage: "Repository registered. Your newer changes remain unsaved."}),
            );
        }
        await load_choices();
    } catch {
        if (owns_editor(token, editor, "repository", runner.id)) {
            $("#agent-repository-result").text(
                $t({
                    defaultMessage:
                        "Repository registration failed. Check the device catalog and origin.",
                }),
            );
        }
    }
}
// The pairing id and code a preview last succeeded for; Approve pairing
// stays disabled until the current form fields match this pair exactly.
let pairing_preview: {pairing_id: string; user_code: string} | undefined;
export function update_pairing_approve(): void {
    $("#agent-pairing-approve").prop(
        "disabled",
        !(
            pairing_preview?.pairing_id === value("#agent-pairing-id") &&
            pairing_preview?.user_code === value("#agent-pairing-code")
        ),
    );
}
export const pairing_mismatch_sentence = (): string =>
    $t({
        defaultMessage:
            "This pairing code does not match or has expired. Start pairing again on the device.",
    });
export async function check_pairing(): Promise<void> {
    const token = visit;
    const editor = begin_editor("pairing");
    const pairing_id = value("#agent-pairing-id");
    const user_code = value("#agent-pairing-code");
    if (!pairing_id || !user_code) {
        return;
    }
    $("#agent-pairing-added").prop("hidden", true);
    $("#agent-pairing-preview").empty().prop("hidden", true);
    pairing_preview = undefined;
    update_pairing_approve();
    $("#agent-pairing-result").text($t({defaultMessage: "Checking pairing code…"}));
    try {
        const result = await api.preview_pairing(pairing_id, user_code);
        if (
            !owns_editor(token, editor, "pairing") ||
            value("#agent-pairing-id") !== pairing_id ||
            value("#agent-pairing-code") !== user_code
        ) {
            return;
        }
        const box = $("#agent-pairing-preview").empty().prop("hidden", false);
        line(box, $t({defaultMessage: "Device name"}), result.pairing.device_name);
        line(box, $t({defaultMessage: "Device fingerprint"}), result.pairing.fingerprint_prefix);
        line(box, $t({defaultMessage: "Organization"}), result.pairing.realm_name);
        $("<p>")
            .text(
                $t({
                    defaultMessage: "Approve only if this matches the device you are setting up.",
                }),
            )
            .appendTo(box);
        $("#agent-pairing-result").text("");
        pairing_preview = {pairing_id, user_code};
        update_pairing_approve();
    } catch {
        if (owns_editor(token, editor, "pairing")) {
            $("#agent-pairing-result").text(pairing_mismatch_sentence());
        }
    }
}
export async function approve_pairing(): Promise<void> {
    const token = visit;
    const editor = begin_editor("pairing");
    const pairing_id = value("#agent-pairing-id");
    const code = value("#agent-pairing-code");
    const known_runner_ids = new Set(runners.map((item) => item.id));
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
        pairing_preview = undefined;
        update_pairing_approve();
        $("#agent-pairing-preview").empty().prop("hidden", true);
        announce($t({defaultMessage: "Device connected. You can add an agent that runs on it."}));
        $("#agent-pairing-added").prop("hidden", false);
        await load_runners();
        if (owns_editor(token, editor, "pairing")) {
            // The device has not necessarily exchanged its pairing for a
            // runner yet, so there may be no new id to find here; "Add
            // agent on this device" then opens the wizard unselected.
            pairing_runner_hint = runners.find((item) => !known_runner_ids.has(item.id))?.id;
        }
    } catch {
        if (owns_editor(token, editor, "pairing")) {
            announce(pairing_mismatch_sentence());
        }
    }
}
export function render_connect_steps(): void {
    const list = $("#agent-connect-steps").empty();
    for (const text of [
        $t({defaultMessage: "Install the Grow Agent runner on the device."}),
        $t(
            {defaultMessage: "On the device, run: grow-agent connect {realm_url}"},
            {realm_url: realm.realm_url},
        ),
        $t({
            defaultMessage:
                "Enter the pairing ID and the code that the device shows, then choose Check code.",
        }),
        $t({defaultMessage: "After you approve, run on the device: grow-agent run"}),
    ]) {
        $("<li>").text(text).appendTo(list);
    }
}

export function bind_handlers(): void {
    const root = $(document);
    root.on("submit", "#agent-pairing-form", (event) => {
        event.preventDefault();
        void approve_pairing();
    });
    root.on("click", "#agent-pairing-check", () => {
        void check_pairing();
    });
    root.on("input change", "#agent-pairing-id, #agent-pairing-code", () => {
        update_pairing_approve();
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
    root.on("click", "[data-agent-action]", function () {
        const action = $(this).attr("data-agent-action") ?? "";
        const id = $(this).attr("data-agent-id") ?? "";
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
            if (
                !window.confirm(
                    $t({defaultMessage: "Revoke pairing for {name}?"}, {name: runner.name}),
                )
            ) {
                return;
            }
            const token = visit;
            void api
                .revoke_runner(id, runner.revision)
                .then(() => {
                    if (current(token)) {
                        announce($t({defaultMessage: "Pairing revoked."}));
                        void load_runners();
                    }
                })
                .catch(failed(token, $t({defaultMessage: "Pairing revocation failed."})));
        }
    });
}
