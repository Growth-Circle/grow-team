/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, no-jquery/no-append-html, promise/prefer-await-to-then, promise/always-return, @typescript-eslint/consistent-type-assertions -- The panel uses static jQuery markup and fenced callbacks. */
import $ from "jquery";

import render_panel from "../templates/agent/job_panel.hbs";

import * as api from "./agent_api.ts";
import {
    accepts_auxiliary_response,
    accepts_detail_response,
    agent_activity_label,
    agent_job_status_sentence,
    clears_input_on_ack,
    input_delivery_label,
    input_intent_for_draft,
    job_reason_sentence,
    job_status_label,
    merge_event_sequences,
    new_client_key,
    resume_unavailable_sentence,
} from "./agent_ui_state.ts";
import type {InputIntent} from "./agent_ui_state.ts";
import * as browser_history from "./browser_history.ts";
import {$t} from "./i18n.ts";
import * as overlays from "./overlays.ts";
import * as people from "./people.ts";
import {current_user} from "./state_data.ts";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const poll_interval_ms = 5000;
const executing_states = new Set([
    "queued",
    "running",
    "verifying",
    "waiting_for_input",
    "waiting_for_approval",
    "cancel_requested",
]);
let target = "";
let visit = 0;
let actor = "";
let timer: ReturnType<typeof setTimeout> | undefined;
let detail: api.AgentJobDetail | undefined;
let operation_offset = 0;
let artifact_offset = 0;
let event_after = 0;
let events: {sequence: number; attempt_id: string | null; type: string; occurred_at: string}[] = [];
const loaded_operations = new Map<string, api.AgentJobDetail["operations"][number]>();
const loaded_artifacts = new Map<string, api.AgentJobDetail["artifacts"][number]>();
let handlers_bound = false;
let detail_request = 0;
let accepted_request = 0;
let event_request = 0;
let input_request = 0;
let input_revision = 0;
let input_intent: InputIntent | undefined;
// Holds an unresolved input intent while its job is not the open one.
// The key is the actor and the job ID. A visit to another job keeps this retry key.
const unresolved_inputs = new Map<string, InputIntent>();
// Profile names change rarely, so one lookup serves every job of that profile.
const profile_names = new Map<string, string>();

export function valid_job_id(id: string): boolean {
    return uuid.test(id);
}
export function job_hash(id: string): string {
    if (!valid_job_id(id)) {
        throw new Error("Invalid job ID");
    }
    return `#agent-jobs/${id}`;
}
function session(): string {
    return `${window.location.origin}:${current_user.user_id}`;
}
// A logout or a realm change produces a new actor string, so a stale
// intent from a previous session can never be restored.
function input_slot(id: string): string {
    return `${actor}:${id}`;
}
function active(id: string, token: number): boolean {
    return (
        target === id &&
        visit === token &&
        actor === session() &&
        $("#agent-job-overlay").hasClass("show")
    );
}
function printable(value: unknown): string {
    return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : $t({defaultMessage: "Unknown"});
}
function textline(parent: JQuery, label: string, value: unknown): void {
    $("<p class='agent-job-line'>")
        .text(`${label}: ${printable(value)}`)
        .appendTo(parent);
}
function fact(parent: JQuery, label: string, value: unknown, wide = false): void {
    const item = $("<div class='agent-job-fact'>").toggleClass("wide", wide).appendTo(parent);
    $("<dt>").text(label).appendTo(item);
    $("<dd>").text(printable(value)).appendTo(item);
}
function link_fact(parent: JQuery, label: string, href: string, text: string): void {
    const item = $("<div class='agent-job-fact'>").appendTo(parent);
    $("<dt>").text(label).appendTo(item);
    $("<dd>").append($("<a>").attr("href", href).text(text)).appendTo(item);
}
// An operation's `arguments` field is typed `unknown` (its shape depends on
// `action`), so every read narrows it defensively instead of asserting it.
function args_record(value: unknown): Record<string, unknown> {
    return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}
function str_field(record: Record<string, unknown>, key: string): string {
    const value = record[key];
    return typeof value === "string" ? value : "";
}
function list_field(record: Record<string, unknown>, key: string): string[] {
    const value = record[key];
    return Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string")
        : [];
}
function row(parent: JQuery, icon: string, text: string, meta = ""): JQuery {
    const item = $("<div class='agent-job-row'>").appendTo(parent);
    $("<i class='agent-job-row-icon zulip-icon' aria-hidden='true'>")
        .addClass(`zulip-icon-${icon}`)
        .appendTo(item);
    $("<span class='agent-job-row-text'>").text(text).appendTo(item);
    if (meta) {
        $("<span class='agent-job-row-meta'>").text(meta).appendTo(item);
    }
    return item;
}
function clock_time(value: string): string {
    const time = new Date(value);
    return Number.isNaN(time.getTime())
        ? ""
        : time.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
}
function byte_size(bytes: number): string {
    return bytes < 1024
        ? $t({defaultMessage: "{count} B"}, {count: bytes})
        : $t({defaultMessage: "{count} KB"}, {count: Math.ceil(bytes / 1024)});
}
// The pill color follows the job state machine, never the text of a reply.
function state_tone(state: string): string {
    switch (state) {
        case "running":
        case "verifying":
        case "completed":
            return "progress";
        case "queued":
        case "draft":
        case "waiting_for_input":
        case "waiting_for_approval":
        case "cancel_requested":
            return "attention";
        case "blocked":
        case "failed":
        case "interrupted":
            return "danger";
        default:
            return "neutral";
    }
}
function action(
    parent: JQuery,
    label: string,
    name: string,
    id = "",
    variant = "subtle-neutral",
): void {
    $("<button type='button'>")
        .addClass(`action-button action-button-${variant}`)
        .text(label)
        .attr("data-job-action", name)
        .attr("data-job-id", id)
        .appendTo(parent);
}
function render_header(job: api.AgentJobDetail["job"]): void {
    $("#agent-job-heading")
        .text($t({defaultMessage: "Task #{id}"}, {id: job.id.slice(0, 8)}))
        .attr("title", job.id);
    $("#agent-job-state")
        .text(job_status_label(job.status))
        .attr("data-tone", state_tone(job.status))
        .prop("hidden", false);
    const requester = people.maybe_get_user_by_id(job.requester_id, true)?.full_name;
    const parts = [
        requester
            ? $t({defaultMessage: "Requested by {name}"}, {name: requester})
            : $t({defaultMessage: "Requested by a former member"}),
    ];
    const profile_name = profile_names.get(job.profile_id);
    if (profile_name) {
        parts.push(profile_name);
    }
    $("#agent-job-subtitle").text(parts.join(" · "));
}
async function load_profile_name(profile_id: string, id: string, token: number): Promise<void> {
    if (profile_names.has(profile_id)) {
        return;
    }
    try {
        const {profile} = await api.get_profile(profile_id);
        profile_names.set(profile_id, profile.name);
        if (active(id, token) && detail) {
            render_header(detail.job);
        }
    } catch {
        // The subtitle keeps the requester when the profile is not visible to this member.
    }
}
function status(message: string): void {
    $("#agent-job-status").text(message);
}
function selected_attempt(
    data: api.AgentJobDetail,
): api.AgentJobDetail["attempts"][number] | undefined {
    return data.attempts.toReversed().find((item) => item.active) ?? data.attempts.at(-1);
}
// Renders a non-manage operation's title in plain words from its
// `arguments`, with the hash, state, and approval moved into a Technical
// details block (contract 13.1). An action this drawer does not special-case
// keeps the old, generic title.
function render_operation_card(box: JQuery, item: api.AgentJobDetail["operations"][number]): void {
    const args = args_record(item.arguments);
    switch (item.action) {
        case "git.push":
            $("<p class='agent-job-operation-title'>")
                .text(
                    $t(
                        {defaultMessage: "Push commit {commit7} to branch {branch} on {remote}"},
                        {
                            commit7: str_field(args, "commit").slice(0, 7),
                            branch: str_field(args, "branch"),
                            remote: str_field(args, "remote"),
                        },
                    ),
                )
                .appendTo(box);
            break;
        case "git.draft_pr": {
            $("<p class='agent-job-operation-title'>")
                .text(
                    $t(
                        {
                            defaultMessage:
                                "Open a draft pull request from {head} into {base} on {remote}",
                        },
                        {
                            head: str_field(args, "head"),
                            base: str_field(args, "base"),
                            remote: str_field(args, "remote"),
                        },
                    ),
                )
                .appendTo(box);
            textline(box, $t({defaultMessage: "Title"}), str_field(args, "title"));
            const pr_text = $("<details class='agent-job-card'>").appendTo(box);
            $("<summary class='agent-job-card-title'>")
                .text($t({defaultMessage: "Pull request text"}))
                .appendTo(pr_text);
            $("<p class='agent-job-line'>").text(str_field(args, "body")).appendTo(pr_text);
            break;
        }
        case "shell.run":
            $("<p class='agent-job-operation-title'>")
                .text(
                    $t(
                        {defaultMessage: "Run a command: {argv}"},
                        {argv: list_field(args, "argv").join(" ")},
                    ),
                )
                .appendTo(box);
            textline(box, $t({defaultMessage: "Folder"}), str_field(args, "cwd"));
            break;
        case "dependencies.install":
            $("<p class='agent-job-operation-title'>")
                .text(
                    $t(
                        {defaultMessage: "Install dependencies: {argv}"},
                        {argv: list_field(args, "argv").join(" ")},
                    ),
                )
                .appendTo(box);
            break;
        case "checks.run":
            $("<p class='agent-job-operation-title'>")
                .text(
                    $t(
                        {defaultMessage: "Run required checks: {ids}"},
                        {ids: list_field(args, "check_ids").join(", ")},
                    ),
                )
                .appendTo(box);
            break;
        default:
            $("<p class='agent-job-operation-title'>").text(item.action).appendTo(box);
    }
    const technical = $("<details class='agent-job-card'>").appendTo(box);
    $("<summary class='agent-job-card-title'>")
        .text($t({defaultMessage: "Technical details"}))
        .appendTo(technical);
    textline(technical, $t({defaultMessage: "State"}), item.status);
    textline(technical, $t({defaultMessage: "Operation hash"}), item.operation_hash.slice(0, 12));
    textline(
        technical,
        $t({defaultMessage: "Approval"}),
        item.approval_decision ?? $t({defaultMessage: "None"}),
    );
}
function render(data: api.AgentJobDetail): void {
    const {job} = data;
    const attempt = selected_attempt(data);
    const attempt_id = attempt?.id;
    render_header(job);
    // Only an answer-mode job gets this note; a manage job answers by
    // taking team actions, not by refusing to write code.
    $("#agent-job-answer-note").prop("hidden", job.job_kind !== "answer");
    const summary = $("#agent-job-summary").empty();
    fact(
        summary,
        $t({defaultMessage: "Task type"}),
        job.job_kind === "code"
            ? $t({defaultMessage: "Coding"})
            : job.job_kind === "manage"
              ? $t({defaultMessage: "Team management"})
              : $t({defaultMessage: "Answer"}),
    );
    fact(
        summary,
        $t({defaultMessage: "Attempt"}),
        attempt ? attempt.number : $t({defaultMessage: "Not started"}),
    );
    fact(summary, $t({defaultMessage: "Request"}), job.request, true);
    // Each fact below is new in release 25 and optional until the
    // team-backend release lane ships it, so it shows only when present.
    if (job.repository) {
        fact(summary, $t({defaultMessage: "Repository"}), job.repository.alias);
    }
    if (job.base_ref) {
        fact(summary, $t({defaultMessage: "Base branch"}), job.base_ref);
    }
    if (job.budget) {
        fact(
            summary,
            $t({defaultMessage: "Budget"}),
            $t(
                {defaultMessage: "{minutes} min · {rounds} tool steps"},
                {
                    minutes: Math.round(job.budget.active_seconds / 60),
                    rounds: job.budget.tool_rounds,
                },
            ),
        );
    }
    if (job.instructions && job.instructions.team_revision !== null) {
        fact(
            summary,
            $t({defaultMessage: "Team instructions"}),
            $t({defaultMessage: "Revision {revision}"}, {revision: job.instructions.team_revision}),
        );
    }
    if (job.instructions && job.instructions.profile_revision !== null) {
        fact(
            summary,
            $t({defaultMessage: "Agent instructions"}),
            $t(
                {defaultMessage: "Revision {revision}"},
                {revision: job.instructions.profile_revision},
            ),
        );
    }
    if (job.follows_job_id) {
        link_fact(
            summary,
            $t({defaultMessage: "Follows task"}),
            job_hash(job.follows_job_id),
            `#${job.follows_job_id.slice(0, 8)}`,
        );
    }
    const process = $("#agent-job-attempt").empty();
    // The phase, process state, reason code, and hashes are internal detail.
    // The status sentence above already says what happened in plain words.
    textline(process, $t({defaultMessage: "Phase"}), job.phase);
    if (job.blocked_reason) {
        textline(process, $t({defaultMessage: "Block"}), job.blocked_reason);
    }
    if (job.reason_code) {
        textline(process, $t({defaultMessage: "Reason code"}), job.reason_code);
    }
    if (!attempt) {
        textline(
            process,
            $t({defaultMessage: "Attempt"}),
            $t({defaultMessage: "No attempt started"}),
        );
    } else {
        textline(process, $t({defaultMessage: "Attempt"}), attempt.number);
        textline(process, $t({defaultMessage: "Process"}), attempt.process_state);
        textline(
            process,
            $t({defaultMessage: "Active"}),
            attempt.active ? $t({defaultMessage: "Yes"}) : $t({defaultMessage: "No"}),
        );
        textline(process, $t({defaultMessage: "Base commit"}), attempt.base_commit);
        textline(process, $t({defaultMessage: "Final tree"}), attempt.tree_hash);
        if (job.status === "cancel_requested" && attempt.process_state !== "stopped") {
            textline(
                process,
                $t({defaultMessage: "Stop"}),
                $t({defaultMessage: "Requested. The agent has not confirmed it."}),
            );
        }
        if (attempt.process_state === "stopped") {
            textline(
                process,
                $t({defaultMessage: "Stop"}),
                $t({defaultMessage: "Process stop confirmed."}),
            );
        }
    }
    const checks = $("#agent-job-checks").empty();
    const current_checks = data.required_checks.filter(
        (item) => !item.attempt_id || item.attempt_id === attempt_id,
    );
    if (current_checks.length === 0) {
        $("<p class='agent-job-empty'>")
            .text($t({defaultMessage: "No required check evidence for this attempt."}))
            .appendTo(checks);
    }
    for (const check of current_checks) {
        const item = row(checks, "file-check", `${check.check_id} · ${check.outcome}`);
        if (check.command) {
            $("<code class='agent-job-row-detail'>").text(check.command.join(" ")).appendTo(item);
        }
        if (check.output_artifact_id && valid_job_id(check.output_artifact_id)) {
            $("<a class='agent-job-row-link'>")
                .attr("href", `/json/agent/artifacts/${check.output_artifact_id}`)
                .text($t({defaultMessage: "Download check output"}))
                .appendTo(item);
        }
    }
    if (current_checks.length > 0) {
        $("<p class='agent-job-note'>")
            .text(
                $t({
                    defaultMessage:
                        "Results refer to the final tree of this attempt. A new edit cancels them.",
                }),
            )
            .appendTo(checks);
    }
    const operations = $("#agent-job-operations");
    operations.empty();
    const current_operations = [...loaded_operations.values()].filter(
        (item) => item.attempt_id === attempt_id,
    );
    if (current_operations.length === 0) {
        $("<p class='agent-job-empty'>")
            .text($t({defaultMessage: "No operations for this attempt."}))
            .appendTo(operations);
    }
    for (const item of current_operations) {
        const decidable =
            item.can_decide && item.approval_id && item.nonce && item.approval_version !== null;
        // A team.manage step never shows its hash or its action ID; the
        // server writes a summary in the reader's own words instead.
        const is_manage_step = item.action === "team.manage";
        const box = $("<div class='agent-job-operation'>")
            .toggleClass("needs-decision", Boolean(decidable))
            .appendTo(operations);
        if (is_manage_step) {
            $("<p class='agent-job-operation-title'>")
                .text(item.summary ?? $t({defaultMessage: "Team management step"}))
                .appendTo(box);
        } else {
            render_operation_card(box, item);
        }
        if (decidable) {
            const payload = JSON.stringify({
                approval_id: item.approval_id,
                expected_version: item.approval_version,
                operation_hash: item.operation_hash,
                nonce: item.nonce,
            });
            const buttons = $("<div class='agent-job-decision'>").appendTo(box);
            action(buttons, $t({defaultMessage: "Approve"}), "approve", payload, "solid-brand");
            action(buttons, $t({defaultMessage: "Reject"}), "reject", payload);
            $("<p class='agent-job-note'>")
                .text(
                    $t({
                        defaultMessage: "The approval covers this operation only and works once.",
                    }),
                )
                .appendTo(box);
        } else if (is_manage_step && item.approval_id && item.approval_decision === "pending") {
            const requester = people.maybe_get_user_by_id(job.requester_id, true)?.full_name;
            $("<p class='agent-job-note'>")
                .text(
                    requester
                        ? $t({defaultMessage: "Waiting for {name} to approve."}, {name: requester})
                        : $t({defaultMessage: "Waiting for the requester to approve."}),
                )
                .appendTo(box);
        } else if (is_manage_step && item.status === "started") {
            // The runner never retries an execute call after a crash, so a
            // step that stayed "started" with no receipt is genuinely
            // unclear, not merely slow.
            $("<p class='agent-job-note'>")
                .text(
                    $t({
                        defaultMessage:
                            "This step may have finished. Check the channel before you try again.",
                    }),
                )
                .appendTo(box);
        } else if (
            (item.action === "git.push" || item.action === "git.draft_pr") &&
            item.status === "outcome_unknown"
        ) {
            $("<p class='agent-job-note'>")
                .text(
                    $t({
                        defaultMessage:
                            "This action may have finished. Check the repository before you try again.",
                    }),
                )
                .appendTo(box);
        }
    }
    $("#agent-job-more-operations")
        .prop("hidden", !data.operations_cursor.truncated)
        .text(
            data.operations_cursor.truncated
                ? $t({defaultMessage: "Show the next operations"})
                : $t({defaultMessage: "No more operations"}),
        );
    $("#agent-job-first-operations").remove();
    if (operation_offset > 0) {
        $(
            "<button type='button' id='agent-job-first-operations' class='action-button action-button-text-brand agent-job-more'>",
        )
            .text($t({defaultMessage: "Show the first operations"}))
            .insertBefore("#agent-job-more-operations");
    }
    if (operation_offset > 0) {
        $("<p class='agent-job-note'>")
            .text(
                $t({
                    defaultMessage:
                        'This list does not show earlier operations. Choose "Show the first operations" to see them.',
                }),
            )
            .appendTo(operations);
    }
    const artifacts = $("#agent-job-artifacts");
    artifacts.empty();
    const current_artifacts = [...loaded_artifacts.values()].filter(
        (item) => item.attempt_id === attempt_id,
    );
    if (current_artifacts.length === 0) {
        $("<p class='agent-job-empty'>")
            .text($t({defaultMessage: "No files for this attempt."}))
            .appendTo(artifacts);
    }
    for (const item of current_artifacts) {
        const line = row(artifacts, "file-text", item.filename, byte_size(item.size));
        if (valid_job_id(item.id)) {
            $("<a class='agent-job-row-link'>")
                .attr("href", `/json/agent/artifacts/${item.id}`)
                .attr("aria-label", $t({defaultMessage: "Download {file}"}, {file: item.filename}))
                .text($t({defaultMessage: "Download"}))
                .appendTo(line);
        }
        if (item.kind === "diff" || item.media_type.startsWith("text/")) {
            action(line, $t({defaultMessage: "Preview"}), "preview", item.id, "text-brand");
        }
    }
    $("#agent-job-more-artifacts")
        .prop("hidden", !data.artifacts_cursor.truncated)
        .text(
            data.artifacts_cursor.truncated
                ? $t({defaultMessage: "Show more artifacts"})
                : $t({defaultMessage: "All artifacts shown"}),
        );
    const resume_available = job.resume_available ?? false;
    const controls = $("#agent-job-controls").empty();
    if (job.allowed_actions.includes("cancel")) {
        action(controls, $t({defaultMessage: "Request stop"}), "cancel");
    }
    if (job.allowed_actions.includes("resume")) {
        if (resume_available) {
            action(controls, $t({defaultMessage: "Resume job"}), "resume", "", "subtle-brand");
            if (job.job_kind === "code") {
                $("<p class='agent-job-note'>")
                    .text(
                        $t({
                            defaultMessage:
                                "Resume continues on the same device, from its saved work.",
                        }),
                    )
                    .appendTo(controls);
            }
        } else {
            $("<p class='agent-job-note'>")
                .text(resume_unavailable_sentence(job.resume_unavailable_reason))
                .appendTo(controls);
        }
    }
    if (job.allowed_actions.includes("deliver_privately")) {
        action(
            controls,
            $t({defaultMessage: "Send the result to me privately"}),
            "deliver-privately",
        );
    }
    if (job.allowed_actions.includes("follow_up")) {
        action(controls, $t({defaultMessage: "Create follow-up task"}), "follow-up");
    }
    const input_allowed =
        job.allowed_actions.includes("input") &&
        (job.status === "queued" ||
            attempt?.process_state === "starting" ||
            attempt?.process_state === "active");
    $("#agent-job-input-form").prop("hidden", !input_allowed);
    const finished = ["completed", "cancelled", "failed"].includes(job.status);
    // blocked, interrupted, verifying, and failed jobs carry a reason_code
    // that explains what happened in more detail than the status alone.
    const reason_sentence = ["blocked", "interrupted", "failed", "verifying"].includes(job.status)
        ? job_reason_sentence(job.reason_code, resume_available)
        : undefined;
    const status_extra = {
        start_deadline: job.start_deadline,
        job_kind: job.job_kind,
        delivery_target: job.delivery_target,
    };
    status(
        finished
            ? `${reason_sentence ?? agent_job_status_sentence(job.status, resume_available, status_extra)} ${$t({defaultMessage: "Create a new task for further work."})}`
            : (reason_sentence ??
                  agent_job_status_sentence(job.status, resume_available, status_extra)),
    );
    $("#agent-job-status").attr("data-tone", state_tone(job.status));
    $("#agent-job-updated").text(
        $t({defaultMessage: "Updated {time}"}, {time: new Date().toLocaleTimeString()}),
    );
}
async function fetch_detail(id: string, token: number): Promise<void> {
    detail_request += 1;
    const request = detail_request;
    const requested_operation_offset = operation_offset;
    const requested_artifact_offset = artifact_offset;
    try {
        const fresh = await api.get_job(id, requested_operation_offset, requested_artifact_offset);
        if (
            !accepts_detail_response({
                active: active(id, token),
                request,
                accepted_request,
                requested_operation_offset,
                requested_artifact_offset,
                current_operation_offset: operation_offset,
                current_artifact_offset: artifact_offset,
                incoming_version: fresh.job.version,
                current_version: detail?.job.version,
            })
        ) {
            return;
        }
        accepted_request = request;
        const old_attempt = detail ? selected_attempt(detail)?.id : undefined;
        const new_attempt = selected_attempt(fresh)?.id;
        if (old_attempt && old_attempt !== new_attempt) {
            operation_offset = 0;
            artifact_offset = 0;
            event_after = 0;
            events = [];
            loaded_operations.clear();
            loaded_artifacts.clear();
            detail = undefined;
            await fetch_detail(id, token);
            return;
        }
        // A later page cannot keep older decision controls authoritative.
        loaded_operations.clear();
        for (const item of fresh.operations) {
            loaded_operations.set(item.operation_id, item);
        }
        for (const item of fresh.artifacts) {
            loaded_artifacts.set(item.id, item);
        }
        detail = fresh;
        render(fresh);
        void load_profile_name(fresh.job.profile_id, id, token);
        await fetch_events(id, token);
        await fetch_inputs(id, token);
    } catch {
        if (active(id, token) && request >= accepted_request) {
            status($t({defaultMessage: "Job status is unknown. Retry this panel."}));
        }
    }
}
async function fetch_events(id: string, token: number): Promise<void> {
    if (!active(id, token)) {
        return;
    }
    event_request += 1;
    const request = event_request;
    const attempt_id = detail && selected_attempt(detail)?.id;
    const job_version = detail?.job.version;
    try {
        const result = await api.get_job_events(id, event_after);
        if (
            !accepts_auxiliary_response({
                active: active(id, token),
                request,
                latest_request: event_request,
                attempt_id,
                current_attempt_id: detail && selected_attempt(detail)?.id,
                requested_job_version: job_version,
                current_job_version: detail?.job.version,
            })
        ) {
            return;
        }
        const incoming: typeof events = [];
        for (const event of result.events) {
            event_after = Math.max(event_after, event.sequence);
            // Event payloads can contain diagnostics. Show only the safe envelope.
            incoming.push({
                sequence: event.sequence,
                attempt_id: event.attempt_id,
                type: event.type,
                occurred_at: event.occurred_at,
            });
        }
        events = merge_event_sequences(events, incoming);
        const box = $("#agent-job-events").empty();
        const shown = events.filter((item) => item.attempt_id === attempt_id).slice(-100);
        // The newest event still waits for the next step while the job runs.
        const running = executing_states.has(detail?.job.status ?? "");
        for (const [index, event] of shown.entries()) {
            const pending = running && index === shown.length - 1;
            row(
                box,
                pending ? "clock" : "check",
                agent_activity_label(event.type),
                clock_time(event.occurred_at),
            ).toggleClass("pending", pending);
        }
        if (shown.length === 0) {
            $("<p class='agent-job-empty'>")
                .text($t({defaultMessage: "No events for this attempt."}))
                .appendTo(box);
        }
        $("#agent-job-more-events").prop("hidden", result.events.length < 100);
    } catch {
        if (
            active(id, token) &&
            request === event_request &&
            attempt_id === (detail && selected_attempt(detail)?.id)
        ) {
            $("#agent-job-events").text($t({defaultMessage: "Event status is unknown."}));
        }
    }
}
async function fetch_inputs(id: string, token: number): Promise<void> {
    if (!active(id, token)) {
        return;
    }
    input_request += 1;
    const request = input_request;
    const attempt_id = detail && selected_attempt(detail)?.id;
    const job_version = detail?.job.version;
    try {
        const result = await api.get_job_inputs(id);
        if (
            !accepts_auxiliary_response({
                active: active(id, token),
                request,
                latest_request: input_request,
                attempt_id,
                current_attempt_id: detail && selected_attempt(detail)?.id,
                requested_job_version: job_version,
                current_job_version: detail?.job.version,
            })
        ) {
            return;
        }
        const box = $("#agent-job-inputs").empty();
        for (const input of result.inputs) {
            const item = $("<div class='agent-job-input'>").appendTo(box);
            textline(
                item,
                $t({defaultMessage: "Input {sequence}"}, {sequence: input.sequence}),
                input.text,
            );
            textline(
                item,
                $t({defaultMessage: "Delivery"}),
                input_delivery_label(input.delivery_state),
            );
        }
        if (result.inputs.length === 0) {
            $("<p class='agent-job-empty'>")
                .text($t({defaultMessage: "No input yet."}))
                .appendTo(box);
        }
        if (result.count > result.inputs.length) {
            textline(
                box,
                $t({defaultMessage: "History"}),
                $t({defaultMessage: "Earlier inputs are not shown."}),
            );
        }
    } catch {
        if (
            active(id, token) &&
            request === input_request &&
            attempt_id === (detail && selected_attempt(detail)?.id)
        ) {
            $("#agent-job-inputs").text($t({defaultMessage: "Input delivery status is unknown."}));
        }
    }
}
function schedule(id: string, token: number): void {
    if (timer) {
        clearTimeout(timer);
    }
    if (!active(id, token)) {
        return;
    }
    timer = setTimeout(() => {
        void fetch_detail(id, token).finally(() => {
            schedule(id, token);
        });
    }, poll_interval_ms);
}
function clear(): void {
    visit += 1;
    target = "";
    detail = undefined;
    events = [];
    event_after = 0;
    operation_offset = 0;
    artifact_offset = 0;
    loaded_operations.clear();
    loaded_artifacts.clear();
    detail_request = 0;
    accepted_request = 0;
    event_request = 0;
    input_request = 0;
    input_revision = 0;
    if (input_intent) {
        unresolved_inputs.set(input_slot(input_intent.job_id), input_intent);
    }
    input_intent = undefined;
    if (timer) {
        clearTimeout(timer);
    }
    timer = undefined;
    $("#agent-job-input").val("");
}

async function preview_artifact(
    artifact_id: string,
    job_id: string,
    token: number,
    box: JQuery,
): Promise<void> {
    if (!valid_job_id(artifact_id)) {
        return;
    }
    try {
        const response = await fetch(`/json/agent/artifacts/${artifact_id}`, {
            credentials: "same-origin",
        });
        if (!response.ok || !response.body) {
            throw new Error("Artifact unavailable");
        }
        const reader = response.body.getReader();
        const chunks: Uint8Array[] = [];
        let total = 0;
        let truncated = false;
        while (total < 65536) {
            const next = await reader.read();
            if (next.done) {
                break;
            }
            const available = Math.min(next.value.length, 65536 - total);
            chunks.push(next.value.slice(0, available));
            total += available;
            if (available < next.value.length || total === 65536) {
                truncated = true;
                break;
            }
        }
        await reader.cancel();
        if (!active(job_id, token)) {
            return;
        }
        const bytes = new Uint8Array(total);
        let offset = 0;
        for (const chunk of chunks) {
            bytes.set(chunk, offset);
            offset += chunk.length;
        }
        box.text(
            new TextDecoder().decode(bytes) +
                (truncated ? $t({defaultMessage: "\n… Preview truncated at 64 KB."}) : ""),
        );
    } catch {
        if (active(job_id, token)) {
            box.text($t({defaultMessage: "Artifact preview is unavailable."}));
        }
    }
}
function bind(): void {
    if (handlers_bound) {
        return;
    }
    handlers_bound = true;
    $("body").on("input", "#agent-job-input", () => {
        input_revision += 1;
    });
    $("body").on("submit", "#agent-job-input-form", (event) => {
        event.preventDefault();
        const id = target;
        const token = visit;
        const job = detail?.job;
        const text = String($("#agent-job-input").val() ?? "").trim();
        if (!job?.allowed_actions.includes("input") || !text || !active(id, token)) {
            return;
        }
        input_intent = input_intent_for_draft(
            input_intent,
            id,
            text,
            job.version,
            input_revision,
            new_client_key,
        );
        const intent = input_intent;
        status($t({defaultMessage: "Input pending acceptance for this job…"}));
        $("#agent-job-input-form button").prop("disabled", true);
        void api
            .job_action(id, "inputs", {
                expected_version: intent.expected_version,
                client_key: intent.key,
                text,
                input_type: "steering",
            })
            .then(() => {
                if (!active(id, token)) {
                    return;
                }
                if (input_intent?.key === intent.key) {
                    input_intent = undefined;
                    unresolved_inputs.delete(input_slot(id));
                }
                if (clears_input_on_ack(intent, input_revision)) {
                    $("#agent-job-input").val("");
                }
                status($t({defaultMessage: "Input accepted. Delivery status will update."}));
                void fetch_detail(id, token);
            })
            .catch(async () => {
                if (active(id, token)) {
                    try {
                        const inputs = await api.get_job_inputs(id);
                        if (!active(id, token)) {
                            return;
                        }
                        if (inputs.inputs.some((item) => item.client_key === intent.key)) {
                            if (input_intent?.key === intent.key) {
                                input_intent = undefined;
                                unresolved_inputs.delete(input_slot(id));
                            }
                            if (clears_input_on_ack(intent, input_revision)) {
                                $("#agent-job-input").val("");
                            }
                            status(
                                $t({
                                    defaultMessage: "Input accepted. Delivery status will update.",
                                }),
                            );
                            void fetch_detail(id, token);
                        } else {
                            status(
                                $t({
                                    defaultMessage:
                                        "Input status is unknown. Send the same text again. It will not create a second input.",
                                }),
                            );
                        }
                    } catch {
                        if (active(id, token)) {
                            status(
                                $t({
                                    defaultMessage:
                                        "Input status is unknown. Send the same text again. It will not create a second input.",
                                }),
                            );
                        }
                    }
                }
            })
            .finally(() => {
                if (active(id, token)) {
                    $("#agent-job-input-form button").prop("disabled", false);
                }
            });
    });
    $("body").on(
        "click",
        "#agent-job-more-operations, #agent-job-first-operations, #agent-job-more-artifacts",
        function (this: HTMLElement) {
            if (!detail) {
                return;
            }
            if (this.id === "agent-job-first-operations") {
                operation_offset = 0;
            } else if (this.id === "agent-job-more-operations") {
                operation_offset = detail.operations_cursor.next_offset;
            } else {
                artifact_offset = detail.artifacts_cursor.next_offset;
            }
            void fetch_detail(target, visit);
        },
    );
    $("body").on("click", "#agent-job-more-events", () => {
        void fetch_events(target, visit);
    });
    $("body").on("click", "[data-job-action]", function () {
        const id = target;
        const token = visit;
        const job = detail?.job;
        if (!job || !active(id, token)) {
            return;
        }
        const name = $(this).attr("data-job-action");
        if (name === "preview") {
            const artifact_id = $(this).attr("data-job-id") ?? "";
            const artifact = loaded_artifacts.get(artifact_id);
            const attempt_id = detail && selected_attempt(detail)?.id;
            if (artifact && artifact.attempt_id === attempt_id) {
                const box = $("<pre class='agent-artifact-preview'>").text(
                    $t({defaultMessage: "Loading preview…"}),
                );
                $(this).after(box);
                void preview_artifact(artifact_id, id, token, box);
            }
            return;
        }
        const on_success = (): void => {
            if (active(id, token)) {
                void fetch_detail(id, token);
            }
        };
        const on_error = (): void => {
            if (active(id, token)) {
                status($t({defaultMessage: "Action failed. Refresh the current job state."}));
            }
        };
        if (name === "cancel" && job.allowed_actions.includes("cancel")) {
            void api
                .job_action(id, "cancel", {expected_version: job.version})
                .then(on_success)
                .catch(on_error);
        }
        if (name === "resume" && job.allowed_actions.includes("resume")) {
            void api
                .job_action(id, "resume", {expected_version: job.version, checkpoint_id: null})
                .then(on_success)
                .catch(on_error);
        }
        if (name === "deliver-privately" && job.allowed_actions.includes("deliver_privately")) {
            void api
                .job_action(id, "deliver-privately", {expected_version: job.version})
                .then(async () => {
                    if (!active(id, token)) {
                        return;
                    }
                    await fetch_detail(id, token);
                    if (active(id, token)) {
                        status(
                            $t({
                                defaultMessage: "The result was sent to you in a direct message.",
                            }),
                        );
                    }
                })
                .catch(on_error);
        }
        if (name === "approve" || name === "reject") {
            const data = $(this).attr("data-job-id");
            if (!data) {
                return;
            }
            const parsed: unknown = JSON.parse(data);
            if (!parsed || typeof parsed !== "object") {
                return;
            }
            const payload = parsed as {
                approval_id: string;
                expected_version: number;
                operation_hash: string;
                nonce: string;
            };
            if (!valid_job_id(payload.approval_id)) {
                return;
            }
            void api
                .decide_approval(payload.approval_id, {
                    expected_version: payload.expected_version,
                    operation_hash: payload.operation_hash,
                    nonce: payload.nonce,
                    decision: name === "approve" ? "approved" : "rejected",
                })
                .then(on_success)
                .catch(on_error);
        }
    });
}
export let change_target = (id: string): void => {
    if (!valid_job_id(id)) {
        return;
    }
    clear();
    target = id;
    actor = session();
    input_intent = unresolved_inputs.get(input_slot(id));
    if (input_intent) {
        input_revision = input_intent.draft_revision;
        $("#agent-job-input").val(input_intent.text);
    }
    const token = visit;
    status($t({defaultMessage: "Loading current job status…"}));
    $("#agent-job-status").removeAttr("data-tone");
    $("#agent-job-heading")
        .text($t({defaultMessage: "Agent task"}))
        .removeAttr("title");
    $("#agent-job-subtitle, #agent-job-updated").text("");
    $("#agent-job-state").prop("hidden", true);
    $("#agent-job-answer-note").prop("hidden", true);
    $(
        "#agent-job-summary, #agent-job-attempt, #agent-job-checks, #agent-job-operations, #agent-job-artifacts, #agent-job-inputs, #agent-job-events, #agent-job-controls",
    ).empty();
    $("#agent-job-input-form").prop("hidden", true);
    void fetch_detail(id, token);
    schedule(id, token);
    $("#agent-job-heading").trigger("focus");
};
export let open = (id: string): void => {
    if (!valid_job_id(id)) {
        return;
    }
    if ($("#agent-job-overlay").length === 0) {
        $("body").append($(render_panel({})));
    }
    bind();
    overlays.open_overlay({
        name: "agent-jobs",
        $overlay: $("#agent-job-overlay"),
        on_close() {
            clear();
            browser_history.exit_overlay();
        },
    });
    change_target(id);
};

export function rewire_change_target(value: typeof change_target): void {
    change_target = value;
}
export function rewire_open(value: typeof open): void {
    open = value;
}
