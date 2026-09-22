/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, no-jquery/no-append-html, promise/prefer-await-to-then, promise/always-return, @typescript-eslint/consistent-type-assertions -- The panel uses static jQuery markup and fenced callbacks. */
import $ from "jquery";

import render_panel from "../templates/agent/job_panel.hbs";

import * as api from "./agent_api.ts";
import {
    accepts_auxiliary_response,
    accepts_detail_response,
    clears_input_on_ack,
    input_intent_for_draft,
    merge_event_sequences,
    new_client_key,
} from "./agent_ui_state.ts";
import type {InputIntent} from "./agent_ui_state.ts";
import * as browser_history from "./browser_history.ts";
import * as overlays from "./overlays.ts";
import {current_user} from "./state_data.ts";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const poll_interval_ms = 5000;
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
function active(id: string, token: number): boolean {
    return (
        target === id &&
        visit === token &&
        actor === session() &&
        $("#agent-job-overlay").hasClass("show")
    );
}
function textline(parent: JQuery, label: string, value: unknown): void {
    const printable =
        typeof value === "string" || typeof value === "number" || typeof value === "boolean"
            ? String(value)
            : "Unknown";
    $("<p>").text(`${label}: ${printable}`).appendTo(parent);
}
function action(parent: JQuery, label: string, name: string, id = ""): void {
    $("<button type='button'>")
        .text(label)
        .attr("data-job-action", name)
        .attr("data-job-id", id)
        .appendTo(parent);
}
function status(message: string): void {
    $("#agent-job-status").text(message);
}
function selected_attempt(
    data: api.AgentJobDetail,
): api.AgentJobDetail["attempts"][number] | undefined {
    return data.attempts.toReversed().find((item) => item.active) ?? data.attempts.at(-1);
}
function render(data: api.AgentJobDetail): void {
    const {job} = data;
    const attempt = selected_attempt(data);
    const attempt_id = attempt?.id;
    const summary = $("#agent-job-summary").empty();
    $("#agent-job-heading").text(`Job ${job.id}`);
    textline(summary, "State", job.status);
    textline(summary, "Phase", job.phase);
    textline(summary, "Task type", job.job_kind);
    textline(summary, "Request", job.request);
    if (job.blocked_reason) {
        textline(summary, "Block", job.blocked_reason);
    }
    if (job.source_message_id) {
        textline(summary, "Source message ID", job.source_message_id);
    }
    const process = $("#agent-job-attempt").empty();
    if (!attempt) {
        textline(process, "Attempt", "No attempt started");
    } else {
        textline(process, "Attempt", attempt.number);
        textline(process, "Process", attempt.process_state);
        textline(process, "Active", attempt.active ? "Yes" : "No");
        textline(process, "Base commit", attempt.base_commit);
        textline(process, "Final tree", attempt.tree_hash);
        if (job.status === "cancel_requested" && attempt.process_state !== "stopped") {
            textline(process, "Stop", "Requested. Process stop is not confirmed.");
        }
        if (attempt.process_state === "stopped") {
            textline(process, "Stop", "Process stop confirmed.");
        }
    }
    const checks = $("#agent-job-checks").empty();
    const current_checks = data.required_checks.filter(
        (item) => !item.attempt_id || item.attempt_id === attempt_id,
    );
    if (current_checks.length === 0) {
        textline(checks, "Checks", "No required check evidence for this attempt");
    }
    for (const check of current_checks) {
        const row = $("<div class='agent-card'>").appendTo(checks);
        textline(row, check.check_id, check.outcome);
        if (check.tree_hash) {
            textline(row, "Tree", check.tree_hash);
        }
        if (check.command) {
            textline(row, "Command", check.command.join(" "));
        }
        if (check.output_artifact_id && valid_job_id(check.output_artifact_id)) {
            $("<a>")
                .attr("href", `/json/agent/artifacts/${check.output_artifact_id}`)
                .text("Download check output")
                .appendTo(row);
        }
    }
    const operations = $("#agent-job-operations");
    operations.empty();
    const current_operations = [...loaded_operations.values()].filter(
        (item) => item.attempt_id === attempt_id,
    );
    if (current_operations.length === 0) {
        textline(operations, "Operations", "No operations for this attempt");
    }
    for (const item of current_operations) {
        const row = $("<div class='agent-card'>").appendTo(operations);
        textline(row, "Action", item.action);
        textline(row, "State", item.status);
        textline(row, "Operation hash", item.operation_hash);
        textline(row, "Approval", item.approval_decision ?? "None");
        if (item.can_decide && item.approval_id && item.nonce && item.approval_version !== null) {
            const payload = JSON.stringify({
                approval_id: item.approval_id,
                expected_version: item.approval_version,
                operation_hash: item.operation_hash,
                nonce: item.nonce,
            });
            action(row, "Approve", "approve", payload);
            action(row, "Reject", "reject", payload);
        }
    }
    $("#agent-job-more-operations")
        .prop("hidden", !data.operations_cursor.truncated)
        .text(
            data.operations_cursor.truncated
                ? "Next operation page (list incomplete)"
                : "Last operation page",
        );
    $("#agent-job-first-operations").remove();
    if (operation_offset > 0) {
        $("<button type='button' id='agent-job-first-operations'>")
            .text("Refresh first operation page")
            .insertBefore("#agent-job-more-operations");
    }
    if (operation_offset > 0) {
        textline(
            operations,
            "Evidence",
            "Only this current server page is shown. Refresh the first page for earlier decisions.",
        );
    }
    const artifacts = $("#agent-job-artifacts");
    artifacts.empty();
    const current_artifacts = [...loaded_artifacts.values()].filter(
        (item) => item.attempt_id === attempt_id,
    );
    if (current_artifacts.length === 0) {
        textline(artifacts, "Artifacts", "No artifacts for this attempt");
    }
    for (const item of current_artifacts) {
        const row = $("<div class='agent-card'>").appendTo(artifacts);
        textline(row, "File", item.filename);
        textline(row, "Attempt", item.attempt_id);
        textline(row, "Kind", item.kind);
        textline(row, "Bytes", item.size);
        if (valid_job_id(item.id)) {
            $("<a>")
                .attr("href", `/json/agent/artifacts/${item.id}`)
                .text("Download authorized artifact")
                .appendTo(row);
        }
        if (item.kind === "diff" || item.media_type.startsWith("text/")) {
            action(row, "Preview first 64 KB", "preview", item.id);
        }
    }
    $("#agent-job-more-artifacts")
        .prop("hidden", !data.artifacts_cursor.truncated)
        .text(
            data.artifacts_cursor.truncated
                ? "Load more artifacts (list incomplete)"
                : "All artifacts shown",
        );
    const controls = $("#agent-job-controls").empty();
    if (job.allowed_actions.includes("cancel")) {
        action(controls, "Request stop", "cancel");
    }
    if (job.allowed_actions.includes("resume")) {
        action(controls, "Resume job", "resume");
    }
    const input_allowed =
        job.allowed_actions.includes("input") &&
        (job.status === "queued" ||
            attempt?.process_state === "starting" ||
            attempt?.process_state === "active");
    $("#agent-job-input-form").prop("hidden", !input_allowed);
    if (["completed", "cancelled", "failed"].includes(job.status)) {
        textline(controls, "Next step", "Create a new task for further work.");
    }
    status(`Current job state: ${job.status}. Updated from server.`);
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
        await fetch_events(id, token);
        await fetch_inputs(id, token);
    } catch {
        if (active(id, token) && request >= accepted_request) {
            status("Job status is unknown. Retry this panel.");
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
        for (const event of events.filter((item) => item.attempt_id === attempt_id).slice(-100)) {
            textline(box, event.occurred_at, event.type);
        }
        if (box.children().length === 0) {
            textline(box, "Events", "No events for this attempt");
        }
        $("#agent-job-more-events").prop("hidden", result.events.length < 100);
    } catch {
        if (
            active(id, token) &&
            request === event_request &&
            attempt_id === (detail && selected_attempt(detail)?.id)
        ) {
            $("#agent-job-events").text("Event status is unknown.");
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
            const row = $("<div class='agent-card'>").appendTo(box);
            textline(row, `Input ${input.sequence}`, input.text);
            textline(row, "Delivery", input.delivery_state);
        }
        if (result.inputs.length === 0) {
            textline(box, "Input", "No input yet");
        }
        if (result.count > result.inputs.length) {
            textline(box, "History", "Input list is incomplete");
        }
    } catch {
        if (
            active(id, token) &&
            request === input_request &&
            attempt_id === (detail && selected_attempt(detail)?.id)
        ) {
            $("#agent-job-inputs").text("Input delivery status is unknown.");
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
            new TextDecoder().decode(bytes) + (truncated ? "\n… Preview truncated at 64 KB." : ""),
        );
    } catch {
        if (active(job_id, token)) {
            box.text("Artifact preview is unavailable.");
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
                }
                if (clears_input_on_ack(intent, input_revision)) {
                    $("#agent-job-input").val("");
                }
                status("Input accepted. Delivery status will update.");
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
                            }
                            if (clears_input_on_ack(intent, input_revision)) {
                                $("#agent-job-input").val("");
                            }
                            status("Input accepted. Delivery status will update.");
                            void fetch_detail(id, token);
                        } else {
                            status(
                                "Input acceptance is unknown. Retry to reuse the same intent key.",
                            );
                        }
                    } catch {
                        if (active(id, token)) {
                            status(
                                "Input acceptance is unknown. Retry to reuse the same intent key.",
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
                const box = $("<pre class='agent-artifact-preview'>").text("Loading preview…");
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
                status("Action failed. Refresh the current job state.");
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
export function change_target(id: string): void {
    if (!valid_job_id(id)) {
        return;
    }
    clear();
    target = id;
    actor = session();
    const token = visit;
    status("Loading current job status…");
    $(
        "#agent-job-summary, #agent-job-attempt, #agent-job-checks, #agent-job-operations, #agent-job-artifacts, #agent-job-inputs, #agent-job-events, #agent-job-controls",
    ).empty();
    $("#agent-job-input-form").prop("hidden", true);
    void fetch_detail(id, token);
    schedule(id, token);
    $("#agent-job-heading").trigger("focus");
}
export function open(id: string): void {
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
}
