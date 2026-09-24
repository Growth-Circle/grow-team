/* eslint-disable no-bitwise -- UUID version and variant bits must be set on random bytes. */
import {$t} from "./i18n.ts";

export type DetailResponseFence = {
    active: boolean;
    request: number;
    accepted_request: number;
    requested_operation_offset: number;
    requested_artifact_offset: number;
    current_operation_offset: number;
    current_artifact_offset: number;
    incoming_version: number;
    current_version: number | undefined;
};

export function new_client_key(): string {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6]! & 0x0f) | 0x40;
    bytes[8] = (bytes[8]! & 0x3f) | 0x80;
    const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function accepts_detail_response(fence: DetailResponseFence): boolean {
    return (
        fence.active &&
        fence.request >= fence.accepted_request &&
        fence.requested_operation_offset === fence.current_operation_offset &&
        fence.requested_artifact_offset === fence.current_artifact_offset &&
        (fence.current_version === undefined || fence.incoming_version >= fence.current_version)
    );
}

export function accepts_auxiliary_response(args: {
    active: boolean;
    request: number;
    latest_request: number;
    attempt_id: string | undefined;
    current_attempt_id: string | undefined;
    requested_job_version: number | undefined;
    current_job_version: number | undefined;
}): boolean {
    return (
        args.active &&
        args.request === args.latest_request &&
        args.attempt_id === args.current_attempt_id &&
        args.requested_job_version === args.current_job_version
    );
}

export function merge_event_sequences<T extends {sequence: number}>(
    existing: T[],
    incoming: T[],
): T[] {
    return [...new Map([...existing, ...incoming].map((item) => [item.sequence, item])).values()]
        .toSorted((left, right) => left.sequence - right.sequence)
        .slice(-1000);
}

export type InputIntent = {
    job_id: string;
    text: string;
    key: string;
    expected_version: number;
    draft_revision: number;
};

export function input_intent_for_draft(
    current: InputIntent | undefined,
    job_id: string,
    text: string,
    expected_version: number,
    draft_revision: number,
    new_key: () => string,
): InputIntent {
    if (
        current?.job_id === job_id &&
        current.text === text &&
        current.draft_revision === draft_revision
    ) {
        return current;
    }
    return {job_id, text, key: new_key(), expected_version, draft_revision};
}

export function clears_input_on_ack(intent: InputIntent, current_revision: number): boolean {
    return intent.draft_revision === current_revision;
}

export function input_delivery_label(state: string): string {
    switch (state) {
        case "pending":
            return $t({defaultMessage: "Pending delivery"});
        case "delivered":
            return $t({defaultMessage: "The agent received it and has not used it yet."});
        case "applied":
            return $t({defaultMessage: "Applied by the agent"});
        case "delivery_uncertain":
            return $t({defaultMessage: "Delivery uncertain; status updates automatically"});
        case "cancelled":
            return $t({defaultMessage: "Cancelled before the agent used it."});
        default:
            return $t({defaultMessage: "Delivery status unknown"});
    }
}

// Maps an AgentSelectionReason code from zerver/lib/agent_selection.py to a
// sentence that says whether the agent can start and what to do next.
export function agent_selection_label(reason: string): string {
    switch (reason) {
        case "cleared":
            return $t({defaultMessage: "No agent is chosen. Choose an agent from the list."});
        case "no_eligible_default":
            // A hidden or paused default may still exist; this must not
            // claim the team has none, only that this task cannot use one.
            return $t({
                defaultMessage:
                    "No default agent is available for this task. Choose an agent from the list.",
            });
        case "unavailable":
            return $t({
                defaultMessage:
                    "That agent is not available to you. Choose another agent from the list.",
            });
        case "repository_unavailable":
            return $t({
                defaultMessage:
                    "This agent works only on its own repository. Choose another agent.",
            });
        case "profile_unavailable":
            return $t({
                defaultMessage:
                    "This agent is turned off. Choose another agent, or ask its owner to turn it on.",
            });
        case "runner_unavailable":
            return $t({
                defaultMessage:
                    "This agent lost access to its runner. Ask the agent owner to pair the runner again.",
            });
        case "coding_unavailable":
            return $t({
                defaultMessage:
                    "This agent cannot do coding work. Choose Answer, or choose another agent.",
            });
        case "access_denied":
            return $t({
                defaultMessage:
                    "You cannot use this agent in this conversation. Choose another agent.",
            });
        case "queue_full":
            return $t({
                defaultMessage:
                    "This agent has too many tasks that wait. Try again in a few minutes.",
            });
        case "profile_not_ready":
            return $t({
                defaultMessage:
                    "This agent is not ready to start. Choose another agent, or ask its owner to check it.",
            });
        case "coding_needs_input":
            return $t({
                defaultMessage:
                    "This agent is not ready for coding work. Choose Answer, or ask the agent owner to complete its repository and required checks.",
            });
        case "runner_offline":
            return $t({
                defaultMessage:
                    "The agent's device is offline. You can create the task now. It waits until the device connects or until its start deadline passes.",
            });
        case "runner_unknown":
            return $t({
                defaultMessage:
                    "The agent runner did not report recently. You can create the task now. It starts when the runner reports again.",
            });
        case "available":
            return $t({
                defaultMessage:
                    "This agent is ready to start the task. Enter your request, then choose Create task.",
            });
        default:
            return $t({
                defaultMessage:
                    "This agent cannot start the task now. Choose another agent, or try again later.",
            });
    }
}

// Maps a selection_source code to the short phrase that explains why this
// agent, and no other, is in the task form.
export function selection_origin_label(origin: string): string {
    switch (origin) {
        case "explicit":
            return $t({defaultMessage: "chosen by you"});
        case "team_default":
            return $t({defaultMessage: "your team default agent"});
        default:
            return $t({defaultMessage: "chosen for you"});
    }
}

// Maps a JobState code to the short phrase for a summary row.
export function job_status_label(status: string): string {
    switch (status) {
        case "draft":
            return $t({defaultMessage: "Draft"});
        case "queued":
            return $t({defaultMessage: "Waiting to start"});
        case "running":
            return $t({defaultMessage: "Running"});
        case "waiting_for_input":
            return $t({defaultMessage: "Waiting for your input"});
        case "waiting_for_approval":
            return $t({defaultMessage: "Waiting for your approval"});
        case "blocked":
            return $t({defaultMessage: "Blocked"});
        case "verifying":
            return $t({defaultMessage: "Running checks"});
        case "cancel_requested":
            return $t({defaultMessage: "Stopping"});
        case "cancelled":
            return $t({defaultMessage: "Stopped"});
        case "interrupted":
            return $t({defaultMessage: "Interrupted"});
        case "failed":
            return $t({defaultMessage: "Failed"});
        case "completed":
            return $t({defaultMessage: "Finished"});
        default:
            return $t({defaultMessage: "Unknown"});
    }
}

// Shared between job_reason_sentence's "runtime_stopped" code and
// agent_job_status_sentence's default "interrupted" text (contract 12.2):
// both describe the same outcome in the same words.
function stopped_before_finish_sentence(resume_available: boolean): string {
    return resume_available
        ? $t({
              defaultMessage:
                  "This task stopped before it finished. Resume the task, or create a new task.",
          })
        : $t({defaultMessage: "This task stopped before it finished. Create a new task."});
}

// Maps a job reason_code (set for a blocked, interrupted, verifying, or
// failed job) to the sentence that explains what happened and what to do
// next. Returns undefined for an unset or unrecognized code, so the caller
// keeps its generic per-status sentence. When resume_available is false, no
// returned sentence contains the word "Resume" or "resume" (RL-5).
export function job_reason_sentence(
    reason_code: string | null | undefined,
    resume_available: boolean,
): string | undefined {
    switch (reason_code) {
        case "stop_unconfirmed":
            return resume_available
                ? $t({
                      defaultMessage:
                          "The stop request went to the device, but the device has not confirmed it stopped. Wait for the device to come back online, then resume or create a new task.",
                  })
                : $t({
                      defaultMessage:
                          "The stop request went to the device, but the device has not confirmed it stopped. Wait for the device to come back online.",
                  });
        case "start_failed":
            return resume_available
                ? $t({
                      defaultMessage:
                          "The device could not start this task. Resume the task, or create a new task.",
                  })
                : $t({defaultMessage: "The device could not start this task. Create a new task."});
        case "runtime_stopped":
            return stopped_before_finish_sentence(resume_available);
        case "result_invalid":
            return $t({
                defaultMessage: "The agent finished with no usable result. Create a new task.",
            });
        case "verification_failed":
            return $t({
                defaultMessage:
                    "The required checks failed. Read the check output below, then create a new task.",
            });
        case "budget_exhausted":
            return $t({
                defaultMessage:
                    "This task used its full budget. Create a new task with a higher budget.",
            });
        case "lease_lost":
            return resume_available
                ? $t({
                      defaultMessage:
                          "The device stopped responding. Resume the task, or create a new task.",
                  })
                : $t({defaultMessage: "The device stopped responding. Create a new task."});
        case "start_deadline_expired":
            return resume_available
                ? $t({
                      defaultMessage:
                          "This task did not start before its start deadline. Resume the task to queue it again, or create a new task.",
                  })
                : $t({
                      defaultMessage:
                          "This task did not start before its start deadline. Create a new task.",
                  });
        case "approval_expired":
            return resume_available
                ? $t({
                      defaultMessage:
                          "No one decided on the requested action within 15 minutes, so the task stopped. Resume the task, or create a new task.",
                  })
                : $t({
                      defaultMessage:
                          "No one decided on the requested action within 15 minutes, so the task stopped. Create a new task.",
                  });
        case "approval_rejected":
            return $t({
                defaultMessage:
                    "The requested action was rejected, so the task stopped. Create a new task to try another way.",
            });
        case "authority_changed":
            return $t({
                defaultMessage:
                    "Access to this agent or its resources changed, so the task stopped. Ask the agent owner to check access.",
            });
        case "profile_needs_action":
            return $t({
                defaultMessage:
                    "This agent needs a fix before it can start. Ask its owner to check the agent.",
            });
        case "publication_blocked":
            return $t({
                defaultMessage:
                    "The result is saved, but it cannot be posted to the conversation yet.",
            });
        case "audience_changed":
            return $t({
                defaultMessage:
                    "The result is saved, but it was not posted because the conversation changed. You can read it below.",
            });
        default:
            return undefined;
    }
}

// Maps a resume_unavailable_reason code to the sentence that explains why
// the Resume control is hidden for an interrupted task.
export function resume_unavailable_sentence(reason: string | null | undefined): string {
    switch (reason) {
        case "attempt_active":
            return $t({
                defaultMessage: "This task is still active on its device. Wait, then check again.",
            });
        case "runner_offline":
            return $t({
                defaultMessage:
                    "The device for this task is offline. You can resume when it comes back online.",
            });
        case "runner_revoked":
            return $t({
                defaultMessage: "The device for this task was removed. Create a new task.",
            });
        default:
            return $t({defaultMessage: "This task cannot resume right now. Create a new task."});
    }
}

// Maps a dispatch receipt reason code to the sentence for a rejected or
// needs_input row, or undefined when the caller should keep its own text.
export function dispatch_receipt_reason_label(reason: string): string | undefined {
    switch (reason) {
        case "not_shared":
            return $t({defaultMessage: "Ask its owner to share it with you."});
        case "queue_full":
            return $t({
                defaultMessage:
                    "This agent has too many tasks that wait. Try again in a few minutes.",
            });
        case "runner_offline":
        case "runner_unknown":
            return $t({defaultMessage: "The agent runner is offline. Try again later."});
        case "command_not_allowed":
            return $t({
                defaultMessage:
                    "You cannot give tasks to this agent. Ask an organization administrator for access.",
            });
        default:
            return undefined;
    }
}

// Maps a runner's host_kind to the composer's device-type word (contract
// 12.4). This lane's own worktree branches before the settings-backend lane
// adds `web/src/agent_settings_labels.ts`, so the composer keeps this small
// copy of the one row it needs instead of importing that file.
export function runner_host_kind_label(host_kind: string): string {
    switch (host_kind) {
        case "workstation":
            return $t({defaultMessage: "Personal computer"});
        case "server":
            return $t({defaultMessage: "Server"});
        default:
            return $t({defaultMessage: "Device type not set"});
    }
}

// Maps a runner's observed_presence to the composer's connection word
// (contract 12.4), for the same reason as runner_host_kind_label above.
export function runner_presence_label(presence: string): string {
    switch (presence) {
        case "online":
            return $t({defaultMessage: "Connected"});
        case "offline":
            return $t({defaultMessage: "Offline"});
        case "revoked":
            return $t({defaultMessage: "Removed"});
        default:
            return $t({defaultMessage: "Status unknown"});
    }
}

// Maps a provider's model_location to the composer's short label (contract
// 12.4), for the same reason as runner_host_kind_label above. Returns
// undefined for an absent or unrecognized value, so the caller shows
// nothing rather than a guess.
export function model_location_label(value: string | undefined): string | undefined {
    switch (value) {
        case "runner_local":
            return $t({defaultMessage: "Model: on the device"});
        case "private_network":
            return $t({defaultMessage: "Model: private network"});
        case "external":
            return $t({defaultMessage: "Model: external service"});
        default:
            return undefined;
    }
}

// Maps a model connection's limits to the profile's default token budget.
// A profile with no model connection gets a fixed budget.
export function derived_budget_defaults(
    provider: {context_window_tokens: number; max_output_tokens: number} | undefined,
): {input_tokens: number; output_tokens: number} {
    if (!provider) {
        return {input_tokens: 400000, output_tokens: 16000};
    }
    return {
        input_tokens: Math.min(Math.max(provider.context_window_tokens * 10, 200000), 4000000),
        output_tokens: Math.min(Math.max(provider.max_output_tokens * 4, 16000), 256000),
    };
}

function local_time(value: string): string {
    const time = new Date(value);
    return Number.isNaN(time.getTime())
        ? ""
        : time.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
}

// The extra fields agent_job_status_sentence needs beyond status and
// resume_available: a queued job's start deadline, and a completed code
// job's delivery target.
export type JobStatusExtra = {
    // `| undefined` is explicit, not decorative: exactOptionalPropertyTypes
    // otherwise rejects a job's own start_deadline (string | null |
    // undefined) as an argument for this optional field.
    start_deadline?: string | null | undefined;
    job_kind?: string;
    delivery_target?: string;
};

// Maps a JobState code to the sentence for the job panel's live status
// region: what is happening now, and what to do next. When resume_available
// is false, no returned sentence contains the word "Resume" or "resume"
// (RL-5).
export function agent_job_status_sentence(
    status: string,
    resume_available: boolean,
    extra: JobStatusExtra = {},
): string {
    switch (status) {
        case "draft":
            return $t({defaultMessage: "This task is not started. Send your request to start it."});
        case "queued":
            return extra.start_deadline
                ? $t(
                      {
                          defaultMessage:
                              "This task waits for the agent's device. If it does not start by {time}, it stops waiting.",
                      },
                      {time: local_time(extra.start_deadline)},
                  )
                : $t({defaultMessage: "This task waits for a free runner."});
        case "running":
            return $t({defaultMessage: "The agent works on this task now."});
        case "waiting_for_input":
            return $t({
                defaultMessage:
                    "The agent waits for more information from you. Write your answer in the input box, then choose Send input.",
            });
        case "waiting_for_approval":
            return $t({
                defaultMessage:
                    "The agent waits for your decision on an operation. Approve or reject the operation below.",
            });
        case "blocked":
            return $t({
                defaultMessage:
                    "This task stopped and cannot continue. Ask the agent owner to check the agent, then create a new task.",
            });
        case "verifying":
            return $t({defaultMessage: "The agent runs the required checks."});
        case "cancel_requested":
            return $t({defaultMessage: "Your stop request went to the agent."});
        case "cancelled":
            return $t({
                defaultMessage: "You stopped this task. Create a new task to continue the work.",
            });
        case "interrupted":
            return stopped_before_finish_sentence(resume_available);
        case "failed":
            return $t({
                defaultMessage:
                    "This task failed. Read the event summaries below, then create a new task.",
            });
        case "completed":
            if (extra.job_kind === "code" && extra.delivery_target === "patch") {
                return $t({defaultMessage: "Done. The diff is ready for review."});
            }
            if (extra.job_kind === "code" && extra.delivery_target === "draft_pr") {
                return $t({defaultMessage: "Done. The draft pull request is ready for review."});
            }
            return $t({
                defaultMessage: "This task finished. Read the artifacts and diff above.",
            });
        default:
            return $t({
                defaultMessage:
                    "The status of this task is unclear. Wait for the next refresh, or open the task again.",
            });
    }
}

// Maps a job event's type (zerver/actions/agent_jobs.py's activity log) to
// the plain-language label for an activity row. Never show the raw event
// type: it is an internal name, not a sentence a reader chose.
export function agent_activity_label(event_type: string): string {
    switch (event_type) {
        case "job.queued":
            return $t({defaultMessage: "Task queued"});
        case "attempt.starting":
            return $t({defaultMessage: "Preparing the task"});
        case "workspace.prepared":
            return $t({defaultMessage: "Code checked out"});
        case "attempt.started":
            return $t({defaultMessage: "Agent started"});
        case "input.received":
            return $t({defaultMessage: "Your input was saved"});
        case "input.applied":
            return $t({defaultMessage: "The agent used your input"});
        case "input.delivery_uncertain":
            return $t({defaultMessage: "Input delivery is not confirmed"});
        case "input.requested":
            return $t({defaultMessage: "The agent asked for your input"});
        case "tool.started":
            return $t({defaultMessage: "Step started"});
        case "tool.finished":
            return $t({defaultMessage: "Step finished"});
        case "verification.finished":
            return $t({defaultMessage: "Check finished"});
        case "approval.requested":
            return $t({defaultMessage: "Approval requested"});
        case "approval.resolved":
            return $t({defaultMessage: "Approval decided"});
        case "team.executed":
            return $t({defaultMessage: "Team action done"});
        case "attempt.stop_requested":
            return $t({defaultMessage: "Stop requested"});
        case "attempt.stopped":
            return $t({defaultMessage: "Agent stopped"});
        case "attempt.interrupted":
            return $t({defaultMessage: "Agent interrupted"});
        case "result.prepared":
            return $t({defaultMessage: "Result ready for checks"});
        case "result.published":
            return $t({defaultMessage: "Result posted"});
        case "publication.blocked":
            return $t({defaultMessage: "Result not posted"});
        case "job.completed":
            return $t({defaultMessage: "Task finished"});
        default:
            return $t({defaultMessage: "Other activity"});
    }
}
