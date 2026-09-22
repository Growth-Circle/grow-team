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
            return $t({
                defaultMessage: "Your team has no default agent. Choose an agent from the list.",
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
                defaultMessage: "You cannot use this agent in this conversation. Choose another agent.",
            });
        case "queue_full":
            return $t({
                defaultMessage: "This agent has too many tasks that wait. Try again in a few minutes.",
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
                    "The agent runner is offline. You can create the task now. It starts when the runner comes back.",
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
                defaultMessage: "This agent cannot start the task now. Choose another agent, or try again later.",
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

// Maps a JobState code to the sentence for the job panel's live status
// region: what is happening now, and what to do next.
export function agent_job_status_sentence(status: string): string {
    switch (status) {
        case "draft":
            return $t({defaultMessage: "This task is not started. Send your request to start it."});
        case "queued":
            return $t({defaultMessage: "This task waits for a free runner."});
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
            return $t({
                defaultMessage:
                    "This task stopped before it finished. Resume the task, or create a new task.",
            });
        case "failed":
            return $t({
                defaultMessage: "This task failed. Read the event summaries below, then create a new task.",
            });
        case "completed":
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
