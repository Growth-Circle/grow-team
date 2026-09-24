import $ from "jquery";

import * as api from "./agent_api.ts";
import {
    agent_selection_label,
    job_status_label,
    model_location_label,
    new_client_key,
    runner_host_kind_label,
    runner_presence_label,
    selection_origin_label,
} from "./agent_ui_state.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as message_lists from "./message_lists.ts";
import * as message_store from "./message_store.ts";
import * as settings_agents from "./settings_agents.ts";
import {current_user, realm} from "./state_data.ts";

type ChoiceState = "unset" | "explicit" | "cleared";
let visit = 0;
let actor = "";
let source_id: number | undefined;
let selected_id = "";
let selection_state: ChoiceState = "unset";
let selection_origin = "none";
let form_revision = 0;
let key = "";
let profiles: api.AgentProfile[] = [];
let kind_dirty = false;
let bound = false;
// Set only by open_for_followup, and carried into the create_job payload.
let follows_job_id: string | undefined;

function identity(): string {
    return `${window.location.origin}:${realm.realm_url}:${current_user.user_id}`;
}
function current(token: number): boolean {
    return token === visit && actor === identity() && $("#agent-task-dialog").is(":open");
}
function notice(value: string): void {
    $("#agent-task-status").text(value);
}
function selected_kind(): "answer" | "code" {
    return $("#agent-task-kind").val() === "code" ? "code" : "answer";
}
function dialog_element(): HTMLDialogElement | undefined {
    const element = document.querySelector("#agent-task-dialog");
    return element instanceof HTMLDialogElement ? element : undefined;
}
function append_element(parent: HTMLElement, tag: keyof HTMLElementTagNameMap): HTMLElement {
    const element = document.createElement(tag);
    parent.append(element);
    return element;
}
function source(): number | undefined {
    const message = message_lists.current?.selected_message();
    return message && message.id > 0 && !message.locally_echoed ? message.id : undefined;
}
function profile(): api.AgentProfile | undefined {
    return profiles.find((item) => item.id === selected_id);
}
// `model_location` is added to the provider schema by the settings-backend
// release lane (contract 4.7 keeps that schema out of this lane's scope), so
// this lane's own worktree does not type it yet. Read it defensively: this
// picks it up once that lane merges, and shows nothing before that.
function provider_model_location(provider: api.AgentProvider | null): string | undefined {
    if (!provider) {
        return undefined;
    }
    // eslint-disable-next-line @typescript-eslint/consistent-type-assertions -- forward-compat read of a field this lane's schema does not type yet
    const value = (provider as unknown as Record<string, unknown>)["model_location"];
    return typeof value === "string" ? value : undefined;
}
function details_box(): HTMLElement | undefined {
    const element = document.querySelector("#agent-task-details");
    return element instanceof HTMLElement ? element : undefined;
}
// Contract 13.3: the resolved runner's device, device type, and connection,
// the model connection's location, and, for Coding, the resolved
// repository. Each line shows only when its data exists.
function render_runner_summary(
    item: api.AgentProfile | undefined,
    repository: {alias: string; base_ref: string} | null | undefined,
): void {
    const box = details_box();
    if (!box) {
        return;
    }
    box.replaceChildren();
    if (!item) {
        return;
    }
    function add_line(container: HTMLElement, text: string): void {
        const line = append_element(container, "p");
        line.className = "agent-task-detail";
        line.textContent = text;
    }
    if (item.runner) {
        add_line(
            box,
            $t(
                {defaultMessage: "Runs on {device} · {device_type} · {status}"},
                {
                    device: item.runner.name,
                    device_type: runner_host_kind_label(item.runner.host_kind),
                    status: runner_presence_label(item.runner.observed_presence),
                },
            ),
        );
    }
    const model_location = model_location_label(provider_model_location(item.provider));
    if (model_location) {
        add_line(box, model_location);
    }
    if (selected_kind() === "code" && repository) {
        add_line(
            box,
            $t(
                {defaultMessage: "Repository: {alias} · base {branch}"},
                {alias: repository.alias, branch: repository.base_ref},
            ),
        );
    }
}
function render_choice(repository?: {alias: string; base_ref: string} | null): void {
    $("#agent-task-profile").val(selected_id);
    const item = profile();
    $("#agent-task-choice").text(
        item
            ? $t(
                  {defaultMessage: "{name} · {origin}"},
                  {name: item.name, origin: selection_origin_label(selection_origin)},
              )
            : $t({defaultMessage: "No agent selected"}),
    );
    if (!kind_dirty && item) {
        $("#agent-task-kind").val(item.default_mode === "code" ? "code" : "answer");
    }
    render_runner_summary(item, repository);
}
// Reads a 4xx rejection's server message from a jQuery ajax failure, with no
// type assertion: each step narrows the unknown value through `in` checks.
// Returns undefined for anything else, since that outcome is genuinely
// unknown and must not be retried with a new idempotency key.
function definite_rejection_message(error: unknown): string | undefined {
    if (typeof error !== "object" || error === null || !("status" in error)) {
        return undefined;
    }
    const {status} = error;
    if (typeof status !== "number" || status < 400 || status >= 500) {
        return undefined;
    }
    if (!("responseJSON" in error) || typeof error.responseJSON !== "object") {
        return undefined;
    }
    const response = error.responseJSON;
    return response !== null && "msg" in response && typeof response.msg === "string"
        ? response.msg
        : undefined;
}
// Coding needs a repository. Disable that option, with its reason, whenever
// the resolved agent has none, and move a current Coding choice back to
// Answer so the person is not left on a choice that cannot submit.
function apply_repository_gate(repository: unknown): void {
    const unavailable = !repository;
    // Read the current choice before disabling the option: disabling the
    // selected option can itself clear the select's value as a side effect.
    const was_code = selected_kind() === "code";
    $("#agent-task-kind option[value='code']").prop("disabled", unavailable);
    if (unavailable && was_code) {
        $("#agent-task-kind").val("answer");
        notice($t({defaultMessage: "This agent has no repository set up."}));
    }
}
function context(): number | undefined {
    const message = source_id === undefined ? undefined : message_store.get(source_id);
    if (!message || message.locally_echoed) {
        notice(
            $t({defaultMessage: "Choose a message that finished sending, then create the task."}),
        );
        return undefined;
    }
    return message.id;
}
async function resolve(
    token: number,
    revision: number,
): Promise<api.AgentSelectionResolution | undefined> {
    const id = context();
    if (id === undefined) {
        return undefined;
    }
    const choice = selected_id;
    const state = selection_state;
    const kind = kind_dirty ? selected_kind() : undefined;
    try {
        const result = await api.resolve_selection({
            source_message_id: id,
            selection_state: state,
            ...(state === "explicit" ? {explicit_profile_id: choice} : {}),
            ...(kind ? {job_kind: kind} : {}),
        });
        if (!current(token) || revision !== form_revision || source_id !== id) {
            return undefined;
        }
        if (state === "unset" && result.profile_id && result.eligible) {
            selected_id = result.profile_id;
            selection_origin = result.selection_source;
            selection_state = "explicit";
        }
        render_choice(result.repository);
        // Show the general selection reason first: apply_repository_gate
        // overrides it only when it actually reverts a Coding choice, and
        // that message must be the one the person reads last.
        notice(agent_selection_label(result.reason));
        apply_repository_gate(result.repository);
        return result;
    } catch {
        if (current(token) && revision === form_revision) {
            notice($t({defaultMessage: "Agent selection could not be checked. Try again."}));
        }
        return undefined;
    }
}
function ensure_dialog(): void {
    if ($("#agent-task-dialog").length > 0) {
        return;
    }
    const dialog = document.createElement("dialog");
    dialog.id = "agent-task-dialog";
    dialog.className = "agent-task-dialog";
    document.body.append(dialog);
    const form = document.createElement("form");
    form.id = "agent-task-form";
    dialog.append(form);
    append_element(form, "h2").id = "agent-task-heading";
    append_element(form, "p").id = "agent-task-source";
    const profile_label = append_element(form, "label");
    profile_label.className = "settings-field-label";
    profile_label.setAttribute("for", "agent-task-profile");
    profile_label.textContent = $t({defaultMessage: "Agent"});
    const profile_choice = append_element(form, "select");
    profile_choice.id = "agent-task-profile";
    profile_choice.className = "settings_select bootstrap-focus-style";
    append_element(form, "p").id = "agent-task-choice";
    append_element(form, "div").id = "agent-task-details";
    const kind_label = append_element(form, "label");
    kind_label.className = "settings-field-label";
    kind_label.setAttribute("for", "agent-task-kind");
    kind_label.textContent = $t({defaultMessage: "Task type"});
    const kind = append_element(form, "select");
    kind.id = "agent-task-kind";
    kind.className = "settings_select bootstrap-focus-style";
    const answer = append_element(kind, "option");
    answer.setAttribute("value", "answer");
    answer.textContent = $t({defaultMessage: "Answer"});
    const code = append_element(kind, "option");
    code.setAttribute("value", "code");
    code.textContent = $t({defaultMessage: "Coding"});
    const request_label = append_element(form, "label");
    request_label.className = "settings-field-label";
    request_label.setAttribute("for", "agent-task-request");
    request_label.textContent = $t({defaultMessage: "Task request"});
    const request = append_element(form, "textarea");
    request.id = "agent-task-request";
    request.className = "settings-textarea";
    request.setAttribute("required", "");
    request.setAttribute("maxlength", "20000");
    const status = append_element(form, "p");
    status.id = "agent-task-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const actions = append_element(form, "div");
    actions.className = "agent-actions";
    const submit_button = append_element(actions, "button");
    submit_button.id = "agent-task-submit";
    submit_button.className = "action-button action-button-solid-brand";
    submit_button.setAttribute("type", "submit");
    submit_button.textContent = $t({defaultMessage: "Create task"});
    const close_button = append_element(actions, "button");
    close_button.id = "agent-task-close";
    close_button.className = "action-button action-button-subtle-neutral";
    close_button.setAttribute("type", "button");
    close_button.textContent = $t({defaultMessage: "Close"});
    dialog.addEventListener("close", () => {
        visit += 1;
    });
    close_button.addEventListener("click", () => {
        dialog.close();
    });
    $("#agent-task-profile").on("change", () => {
        selected_id = String($("#agent-task-profile").val() ?? "");
        selection_state = selected_id ? "explicit" : "cleared";
        selection_origin = selected_id ? "explicit" : "none";
        form_revision += 1;
        key = "";
        render_choice();
        void resolve(visit, form_revision);
    });
    $("#agent-task-kind").on("change", () => {
        kind_dirty = true;
        form_revision += 1;
        key = "";
        void resolve(visit, form_revision);
    });
    $("#agent-task-request").on("input", () => {
        form_revision += 1;
        key = "";
        if (selection_state === "unset" && !selected_id) {
            void resolve(visit, form_revision);
        }
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        void submit();
    });
}
async function submit(): Promise<void> {
    const token = visit;
    const revision = form_revision;
    const id = context();
    const request = String($("#agent-task-request").val() ?? "").trim();
    if (id === undefined || !request || !selected_id) {
        notice(
            $t({
                defaultMessage:
                    "Select a conversation message and an agent, then enter a task request.",
            }),
        );
        return;
    }
    const profile_id = selected_id;
    const job_kind = selected_kind();
    const resolved = await api
        .resolve_selection({
            source_message_id: id,
            selection_state: "explicit",
            explicit_profile_id: profile_id,
            job_kind,
        })
        .catch(() => undefined);
    if (!current(token) || revision !== form_revision || source_id !== id) {
        return;
    }
    if (!resolved?.eligible || resolved.profile_id !== profile_id) {
        notice(
            resolved === undefined
                ? $t({defaultMessage: "Task was not created. Check agent access and retry."})
                : $t(
                      {defaultMessage: "Task was not created. {detail}"},
                      {detail: agent_selection_label(resolved.reason)},
                  ),
        );
        return;
    }
    if (job_kind === "code" && !resolved.repository) {
        notice($t({defaultMessage: "This agent has no repository set up."}));
        return;
    }
    key ||= new_client_key();
    const intent_key = key;
    $("#agent-task-form button[type='submit']").prop("disabled", true);
    try {
        const result = await api.create_job({
            profile_id,
            source_message_id: id,
            request,
            idempotency_key: intent_key,
            job_kind,
            ...(job_kind === "code" && resolved.repository
                ? {
                      delivery_target: "patch",
                      repository_id: resolved.repository.id,
                      base_ref: resolved.repository.base_ref,
                  }
                : {delivery_target: "answer"}),
            ...(follows_job_id ? {follows_job_id} : {}),
        });
        if (current(token) && revision === form_revision && key === intent_key) {
            notice(
                $t(
                    {defaultMessage: "Task created. {detail}"},
                    {detail: job_status_label(result.job.status)},
                ),
            );
            window.location.hash = `#agent-jobs/${result.job.id}`;
            dialog_element()?.close();
        } else if (current(token)) {
            const $status = $("#agent-task-status")
                .empty()
                .text(
                    $t({
                        defaultMessage: "Earlier task accepted. Your edited request remains here. ",
                    }),
                );
            $(document.createElement("a"))
                .attr("href", `#agent-jobs/${result.job.id}`)
                .text($t({defaultMessage: "Open task"}))
                .appendTo($status);
        }
    } catch (error) {
        if (current(token) && revision === form_revision && key === intent_key) {
            // A 4xx response means the server rejected the request before it
            // did anything, so its reason is safe to show and act on right
            // away. Any other failure leaves the outcome unknown, so the
            // idempotency key must be reused rather than retried blindly.
            const server_message = definite_rejection_message(error);
            notice(
                server_message !== undefined
                    ? $t(
                          {defaultMessage: "Task was not created: {detail}"},
                          {detail: server_message},
                      )
                    : $t({
                          defaultMessage:
                              "Task status is unknown. Send the same request again. It will not create a second task.",
                      }),
            );
        }
    } finally {
        if (current(token)) {
            $("#agent-task-form button[type='submit']").prop("disabled", false);
        }
    }
}
export function open_for_message(
    id: number,
    profile_id = "",
    options?: {follows_job_id?: string; heading?: string; source_note?: string},
): void {
    ensure_dialog();
    visit += 1;
    actor = identity();
    source_id = id > 0 ? id : undefined;
    selected_id = profile_id;
    selection_state = profile_id ? "explicit" : "unset";
    selection_origin = profile_id ? "explicit" : "none";
    form_revision = 0;
    key = "";
    kind_dirty = false;
    profiles = [];
    follows_job_id = options?.follows_job_id;
    $("#agent-task-heading").text(options?.heading ?? $t({defaultMessage: "Create agent task"}));
    $("#agent-task-request").val("");
    $("#agent-task-kind").val("answer");
    const message = source_id === undefined ? undefined : message_store.get(source_id);
    const has_source = Boolean(message && !message.locally_echoed);
    $("#agent-task-submit").prop("disabled", !has_source);
    const $source_box = $("#agent-task-source").empty();
    if (message && has_source) {
        $(document.createElement("a"))
            .attr("href", hash_util.by_conversation_and_time_url(message))
            .text($t({defaultMessage: "Source message {id}"}, {id: message.id}))
            .appendTo($source_box);
        const recipients = message.display_recipient;
        const destination =
            message.type === "stream" && typeof recipients === "string"
                ? `#${recipients} > ${message.topic}`
                : Array.isArray(recipients)
                  ? $t(
                        {defaultMessage: "Direct message with {names}"},
                        {
                            names: recipients
                                .filter((recipient) => recipient.id !== current_user.user_id)
                                .map((recipient) => recipient.full_name)
                                .join(", "),
                        },
                    )
                  : $t({defaultMessage: "Direct message"});
        $(document.createElement("span")).text(` · ${destination}`).appendTo($source_box);
        if (options?.source_note) {
            $(document.createElement("p"))
                .addClass("agent-task-follows-note")
                .text(options.source_note)
                .appendTo($source_box);
        }
    } else {
        $source_box.text(
            $t({
                defaultMessage:
                    "Open a conversation and select a message first. The task starts from that message.",
            }),
        );
    }
    dialog_element()?.showModal();
    const token = visit;
    notice($t({defaultMessage: "Checking agent selection…"}));
    void (async () => {
        const available: api.AgentProfile[] = [];
        let offset = 0;
        try {
            for (;;) {
                const result = await api.list_profiles({offset, limit: 100});
                available.push(...result.profiles);
                offset += result.profiles.length;
                if (offset >= result.count || result.profiles.length === 0) {
                    break;
                }
            }
        } catch {
            if (current(token)) {
                notice($t({defaultMessage: "Agent list is unavailable. Try again."}));
            }
            return;
        }
        if (!current(token)) {
            return;
        }
        profiles = available;
        const $select = $("#agent-task-profile").empty();
        $(document.createElement("option"))
            .val("")
            .text($t({defaultMessage: "Choose an agent"}))
            .appendTo($select);
        for (const item of profiles) {
            // Two agents can share a name; the owner and device tell them apart.
            $(document.createElement("option"))
                .val(item.id)
                .text(
                    $t(
                        {defaultMessage: "{name} · {owner} · {runner}"},
                        {
                            name: item.name,
                            owner: item.owner.name,
                            runner: item.runner?.name ?? $t({defaultMessage: "No device"}),
                        },
                    ),
                )
                .appendTo($select);
        }
        render_choice();
        void resolve(token, form_revision);
    })();
}
// `result` is typed `unknown` (its shape belongs to the job-backend release
// lane), so this reads `message_id` defensively instead of asserting it.
function result_message_id(result: unknown): number | undefined {
    if (typeof result !== "object" || result === null) {
        return undefined;
    }
    // eslint-disable-next-line @typescript-eslint/consistent-type-assertions -- `result`'s shape belongs to the job-backend release lane
    const value = (result as Record<string, unknown>)["message_id"];
    return typeof value === "number" ? value : undefined;
}
// Contract 13.3: a follow-up task starts from the followed task's result
// message when it has one, else from its own source message, with that
// task's agent preselected and its ID carried into the new task's payload.
export function open_for_followup(job: {
    id: string;
    profile_id: string;
    source_message_id: number | null;
    result?: unknown;
}): void {
    const source_message_id = result_message_id(job.result) ?? job.source_message_id ?? -1;
    open_for_message(source_message_id, job.profile_id, {
        follows_job_id: job.id,
        heading: $t({defaultMessage: "Create follow-up task"}),
        source_note: $t({defaultMessage: "Follows task #{id}"}, {id: job.id.slice(0, 8)}),
    });
}
export function initialize(): void {
    if (bound) {
        return;
    }
    bound = true;
    settings_agents.register_create_task_handler(({profile_id}) => {
        open_for_message(source() ?? -1, profile_id);
    });
}
