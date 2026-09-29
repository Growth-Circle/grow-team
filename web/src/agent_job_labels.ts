import {agent_activity_label} from "./agent_ui_state.ts";
import {$t} from "./i18n.ts";

/*
    The words of the agent job card in a message (RM-35..47) and of the
    job drawer (DR-48..65). Both show the same job, so both read their
    status, steps, and reasons from this module.
*/

// The five looks of a card (map-mockup-a.md 7.6). Several job statuses
// share one look.
export type Bucket = "queued" | "working" | "review" | "done" | "stopped";

export function bucket_for_status(status: string): Bucket {
    switch (status) {
        case "queued":
        case "draft":
            return "queued";
        case "running":
        case "verifying":
        case "cancel_requested":
            return "working";
        case "waiting_for_approval":
        case "waiting_for_input":
            return "review";
        case "completed":
            return "done";
        default:
            // cancelled, failed, interrupted, blocked, and a status that
            // this client does not know all read as stopped.
            return "stopped";
    }
}

export function pill_text(status: string): string {
    switch (bucket_for_status(status)) {
        case "queued":
            return $t({defaultMessage: "QUEUED"});
        case "working":
            return $t({defaultMessage: "WORKING"});
        case "review":
            return status === "waiting_for_input"
                ? $t({defaultMessage: "WAITING FOR A DECISION"})
                : $t({defaultMessage: "WAITING FOR APPROVAL"});
        case "done":
            return $t({defaultMessage: "DONE"});
        default:
            return $t({defaultMessage: "STOPPED"});
    }
}

// Why a job stopped, in words a reader can act on. The text names no
// device, and no internal code (PLAN.md 3.5, P-28). The codes come from
// card_reason_code() on the server.
export function stopped_reason_text(reason_code: string | null, status: string): string {
    switch (reason_code) {
        case "runner_offline":
            return $t({defaultMessage: "Stopped because the owner's device is offline."});
        case "budget_exceeded":
            return $t({defaultMessage: "Stopped because the budget ran out."});
        case "tool_error":
            return $t({defaultMessage: "Stopped because a step failed."});
        case "timeout":
            return $t({defaultMessage: "Stopped because no one approved in time."});
        case "grant_revoked":
            return $t({defaultMessage: "Stopped because access to the agent changed."});
        case "verification_failed":
            return $t({defaultMessage: "Stopped because a required check failed."});
        case "approval_rejected":
            return $t({defaultMessage: "Stopped because the action was rejected."});
        case "profile_needs_action":
            return $t({defaultMessage: "Stopped because the agent needs a fix first."});
        case "profile_paused":
            return $t({defaultMessage: "Stopped because the agent is paused."});
        case "publication_blocked":
            return $t({defaultMessage: "Stopped. The result is saved but not posted here."});
        case "audience_changed":
            return $t({defaultMessage: "Stopped because the people in this chat changed."});
        default:
            return status === "cancelled"
                ? $t({defaultMessage: "Paused. Open the details to continue."})
                : $t({defaultMessage: "Stopped."});
    }
}

// The line at the left of the card footer (RM-44).
export function card_step_text(card: {
    status: string;
    step_label: string;
    reason_code: string | null;
}): string {
    switch (bucket_for_status(card.status)) {
        case "queued":
            return card.reason_code === "runner_offline"
                ? $t({
                      defaultMessage:
                          "The owner's device is offline. The job continues when the device turns on.",
                  })
                : $t({defaultMessage: "Waiting in line"});
        case "working":
            return agent_activity_label(card.step_label);
        case "review":
            return card.status === "waiting_for_input"
                ? $t({defaultMessage: "Waiting for a decision"})
                : $t({defaultMessage: "Waiting for approval"});
        case "done":
            return $t({defaultMessage: "Finished"});
        default:
            return stopped_reason_text(card.reason_code, card.status);
    }
}

// DR-58..61: the five steps of a job.
export function drawer_step_labels(): string[] {
    return [
        $t({defaultMessage: "Reading the brief and the room"}),
        $t({defaultMessage: "Reading the related Drive files"}),
        $t({defaultMessage: "Doing the work"}),
        $t({defaultMessage: "Send to Matcha for a check"}),
        $t({defaultMessage: "Ask a person to approve"}),
    ];
}

// The step that is active: min(4, floor(percent / 22)). A finished job
// has passed every step, so it returns 5.
export function drawer_step_index(progress: number, status: string): number {
    if (status === "completed") {
        return 5;
    }
    return Math.min(4, Math.floor((Math.max(0, Math.min(1, progress)) * 100) / 22));
}

// The share of the work that is done, from the status and the phase of
// the job. The server uses the same table for the card (02-D3).
const PHASE_PROGRESS = new Map([
    ["inspect", 0.15],
    ["plan", 0.3],
    ["edit", 0.55],
    ["verify", 0.75],
    ["review", 0.9],
    ["deliver", 0.95],
]);

export function job_progress(status: string, phase: string): number {
    if (status === "queued" || status === "draft") {
        return 0.05;
    }
    if (status === "completed") {
        return 1;
    }
    return PHASE_PROGRESS.get(phase) ?? 0.15;
}
