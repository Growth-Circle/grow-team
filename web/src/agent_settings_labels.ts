import {$t} from "./i18n.ts";

// Maps server-side agent settings codes to the sentences and words a person
// reads in the Agents settings panel (contract 12.4, 12.5). No function here
// ever returns a code, a revision, or a raw enum value: an unrecognized
// input always falls back to a generic label, never the input itself.
function unavailable_label(): string {
    return $t({defaultMessage: "Not available"});
}

export function desired_state_label(state: string): string {
    switch (state) {
        case "draft":
            return $t({defaultMessage: "Draft (not taking tasks)"});
        case "enabled":
            return $t({defaultMessage: "On"});
        case "paused":
            return $t({defaultMessage: "Paused"});
        case "archived":
            return $t({defaultMessage: "Archived"});
        default:
            return unavailable_label();
    }
}

export function readiness_label(state: string): string {
    switch (state) {
        case "unchecked":
            return $t({defaultMessage: "Not checked"});
        case "checking":
            return $t({defaultMessage: "Checking"});
        case "ready":
            return $t({defaultMessage: "Ready"});
        case "needs_action":
            return $t({defaultMessage: "Needs a fix"});
        case "error":
            return $t({defaultMessage: "Check failed"});
        default:
            return unavailable_label();
    }
}

export function presence_label(presence: string): string {
    switch (presence) {
        case "online":
            return $t({defaultMessage: "Connected"});
        case "offline":
            return $t({defaultMessage: "Offline"});
        case "unknown":
            return $t({defaultMessage: "Status unknown"});
        case "revoked":
            return $t({defaultMessage: "Removed"});
        default:
            return unavailable_label();
    }
}

export function host_kind_label(kind: string): string {
    switch (kind) {
        case "workstation":
            return $t({defaultMessage: "Personal computer"});
        case "server":
            return $t({defaultMessage: "Server"});
        case "unknown":
            return $t({defaultMessage: "Device type not set"});
        default:
            return unavailable_label();
    }
}

export function setup_phase_label(phase: string): string {
    switch (phase) {
        case "pending":
            return $t({defaultMessage: "Waiting for the device"});
        case "probing":
            return $t({defaultMessage: "Checking"});
        case "ready":
            return $t({defaultMessage: "Check passed"});
        case "needs_action":
            return $t({defaultMessage: "Needs a fix"});
        case "failed":
            return $t({defaultMessage: "Check failed"});
        case "cancelled":
            return $t({defaultMessage: "Check replaced"});
        default:
            return unavailable_label();
    }
}

export function auth_state_label(state: string): string {
    switch (state) {
        case "unchecked":
            return $t({defaultMessage: "Sign-in not checked"});
        case "ready":
            return $t({defaultMessage: "Signed in"});
        case "login_required":
            return $t({defaultMessage: "Sign-in needed"});
        case "expired":
            return $t({defaultMessage: "Sign-in expired"});
        case "error":
            return $t({defaultMessage: "Sign-in check failed"});
        default:
            return unavailable_label();
    }
}

export function default_mode_label(mode: string): string {
    switch (mode) {
        case "answer":
            return $t({defaultMessage: "Answer"});
        case "code":
            return $t({defaultMessage: "Coding"});
        case "manage":
            return $t({defaultMessage: "Team management"});
        default:
            return unavailable_label();
    }
}

export function model_location_label(location: string): string {
    switch (location) {
        case "runner_local":
            return $t({defaultMessage: "Model: on the device"});
        case "private_network":
            return $t({defaultMessage: "Model: private network"});
        case "external":
            return $t({defaultMessage: "Model: external service"});
        default:
            return unavailable_label();
    }
}

export function data_scope_label(scope: string): string {
    switch (scope) {
        case "synthetic":
            return $t({defaultMessage: "Test data"});
        case "selected_chat":
            return $t({defaultMessage: "Selected chat messages"});
        case "selected_repository":
            return $t({defaultMessage: "Selected repository files"});
        default:
            return unavailable_label();
    }
}

// Three variants only: an owner with no shares, an owner who shared with at
// least one principal, and a reader who is not the owner.
export function sharing_label(is_owner: boolean, shared_with_count: number): string {
    if (!is_owner) {
        return $t({defaultMessage: "Shared with you"});
    }
    return shared_with_count > 0
        ? $t({defaultMessage: "Shared by you"})
        : $t({defaultMessage: "Private to you"});
}

export function team_default_badge_label(): string {
    return $t({defaultMessage: "Team default"});
}

export function action_button_label(action: string): string {
    switch (action) {
        case "edit":
            return $t({defaultMessage: "Edit"});
        case "probe":
            return $t({defaultMessage: "Run check"});
        case "enable":
            return $t({defaultMessage: "Turn on"});
        case "pause":
            return $t({defaultMessage: "Pause"});
        case "archive":
            return $t({defaultMessage: "Archive"});
        case "test_task":
            return $t({defaultMessage: "Send test task"});
        default:
            return unavailable_label();
    }
}

export function grant_action_label(action: string): string {
    switch (action) {
        case "profile.use":
            return $t({defaultMessage: "Use this agent"});
        case "context.read":
            return $t({defaultMessage: "Read the task conversation"});
        case "repository.read":
            return $t({defaultMessage: "Read the repository"});
        case "repository.edit":
            return $t({defaultMessage: "Edit files in a task copy"});
        case "checks.run":
            return $t({defaultMessage: "Run required checks"});
        case "shell.run":
            return $t({defaultMessage: "Run commands"});
        case "dependencies.install":
            return $t({defaultMessage: "Install dependencies"});
        case "git.commit":
            return $t({defaultMessage: "Make local commits"});
        case "git.push":
            return $t({defaultMessage: "Push task branches"});
        case "git.draft_pr":
            return $t({defaultMessage: "Open draft pull requests"});
        case "job.control":
            return $t({defaultMessage: "Stop and resume tasks"});
        case "job.review":
            return $t({defaultMessage: "Review results"});
        case "provider.use":
            return $t({defaultMessage: "Use the model connection"});
        case "runner.use":
            return $t({defaultMessage: "Use the device"});
        case "profile.manage":
            return $t({defaultMessage: "Manage this agent"});
        case "team.manage":
            return $t({defaultMessage: "Give team management tasks"});
        default:
            return unavailable_label();
    }
}

// Maps a probe or setup requirement code to the sentence that tells the
// reader what to fix and what to do next (contract 12.5). The "other" row
// is the contract's own generic fallback, so an unrecognized code returns
// that same sentence instead of the shared unavailable_label().
export function requirement_sentence(code: string): string {
    switch (code) {
        case "runtime_missing":
            return $t({
                defaultMessage:
                    "The agent program is not installed on the device. Install it on the device, then run the check again.",
            });
        case "runtime_unsupported":
            return $t({
                defaultMessage:
                    "This agent program version is not supported. Install a supported version on the device, then run the check again.",
            });
        case "auth_required":
            return $t({
                defaultMessage:
                    "The agent program must sign in on the device. Sign in on the device, then run the check again.",
            });
        case "auth_unknown":
            return $t({
                defaultMessage:
                    "The device did not report whether the agent program is signed in. Sign in on the device, then run the check again.",
            });
        case "sandbox_unavailable":
            return $t({
                defaultMessage:
                    "The device has not approved the work environment for this agent. Approve it on the device, then run the check again.",
            });
        case "controlled_provider_required":
            return $t({defaultMessage: "Choose a model connection for this agent."});
        case "probe_incomplete":
            return $t({
                defaultMessage:
                    "The check did not finish. Run the check again. If it fails again, ask the device owner.",
            });
        case "profile_needs_action":
            return $t({defaultMessage: "This agent needs a new check. Run the check again."});
        case "runner_offline":
            return $t({
                defaultMessage: "The device is not connected. Start the runner on the device.",
            });
        default:
            return $t({defaultMessage: "This agent needs a fix before it can run. Ask its owner."});
    }
}
