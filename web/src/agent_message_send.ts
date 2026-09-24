import $ from "jquery";

import render_agent_dispatch_receipts from "../templates/agent/dispatch_receipts.hbs";
import render_agent_dispatch_receipt_banner from "../templates/compose_banner/agent_dispatch_receipt_banner.hbs";

import * as api from "./agent_api.ts";
import {job_hash, valid_job_id} from "./agent_job_panel.ts";
import {dispatch_receipt_reason_label} from "./agent_ui_state.ts";
import * as compose_banner from "./compose_banner.ts";
import * as dialog_widget from "./dialog_widget.ts";
import {$t} from "./i18n.ts";
import * as markdown from "./markdown.ts";
import * as people from "./people.ts";
import {current_user} from "./state_data.ts";

type Destination =
    | {kind: "stream"; stream_id: number; topic: string}
    | {kind: "direct"; participant_user_ids: number[]};

export type MessageSnapshot = Readonly<{
    type: "stream" | "private";
    content: string;
    stream_id?: number;
    topic: string;
    recipient_ids?: number[];
}>;

export function needs_target_lookup(snapshot: MessageSnapshot): boolean {
    if (snapshot.type === "private") {
        return (snapshot.recipient_ids ?? []).some((id) => people.is_valid_bot_user(id));
    }
    const $rendered = $("<div>").html(markdown.render(snapshot.content).content);
    return $rendered
        .find(".user-mention:not(.silent)[data-user-id]")
        .get()
        .some((element) => people.is_valid_bot_user(Number(element.getAttribute("data-user-id"))));
}

function destination(snapshot: MessageSnapshot): Destination {
    if (snapshot.type === "stream") {
        return {kind: "stream", stream_id: snapshot.stream_id!, topic: snapshot.topic};
    }
    return {
        kind: "direct",
        participant_user_ids: [
            ...new Set([current_user.user_id, ...(snapshot.recipient_ids ?? [])]),
        ],
    };
}

export type PreparedTargets = {
    profile_ids: string[];
    metadata_safe: boolean;
    // The preflight decision for each targeted profile, in the same order as
    // `profile_ids`. Empty when there were no targets or the preflight
    // request failed; a failure never blocks the send (contract 13.4).
    decisions: {profile_id: string; decision: string}[];
    // Display names for the profiles in `decisions`, built from the same
    // list `prepare` already fetched, for the preflight banner.
    names: Map<string, string>;
};

async function list_all_profiles(): Promise<api.AgentProfile[]> {
    const profiles: api.AgentProfile[] = [];
    let offset = 0;
    for (;;) {
        const result = await api.list_profiles({offset, limit: 100});
        profiles.push(...result.profiles);
        offset += result.profiles.length;
        if (offset >= result.count || result.profiles.length === 0) {
            break;
        }
    }
    return profiles;
}

export async function prepare(snapshot: MessageSnapshot): Promise<PreparedTargets> {
    const profiles = await list_all_profiles();
    const mentioned = new Set(
        $("<div>")
            .html(markdown.render(snapshot.content).content)
            .find(".user-mention:not(.silent)[data-user-id]")
            .get()
            .map((element) => Number(element.getAttribute("data-user-id"))),
    );
    const recipients = new Set(snapshot.recipient_ids);
    const direct_bot = snapshot.type === "private" && recipients.size === 1;
    const targeted = profiles.filter(
        (profile) =>
            mentioned.has(profile.bot_user_id) ||
            (direct_bot && recipients.has(profile.bot_user_id)),
    );
    const ids = targeted.map((profile) => profile.id);
    let decisions: {profile_id: string; decision: string}[] = [];
    if (ids.length > 0) {
        try {
            decisions = (await api.preflight_message(ids, destination(snapshot))).decisions;
        } catch {
            // The message server makes the final admission decision.
        }
    }
    // A client renderer can omit a server target. Metadata is safe only for a
    // two-person direct message to its known bot with no personal mentions.
    return {
        profile_ids: ids,
        metadata_safe: direct_bot && mentioned.size === 0 && ids.length === 1,
        decisions,
        names: new Map(targeted.map((profile) => [profile.id, profile.name])),
    };
}

// Contract 13.4: every targeted agent was rejected, so the caller must not
// send the task as one. An empty decision list (no targets, or a failed
// preflight request) never counts as "every target rejected".
export function all_targets_rejected(targets: PreparedTargets): boolean {
    return (
        targets.decisions.length > 0 &&
        targets.decisions.every((item) => item.decision === "rejected")
    );
}

// The names for the preflight banner's "You cannot give this task to
// {names}." sentence, in decision order.
export function rejected_target_names(targets: PreparedTargets): string {
    return targets.decisions
        .map((item) => targets.names.get(item.profile_id) ?? $t({defaultMessage: "an agent"}))
        .join(", ");
}

// Best-effort display names for a dispatch receipt list, or undefined
// when the list is unavailable.
async function profile_names(): Promise<Map<string, string> | undefined> {
    try {
        return new Map((await list_all_profiles()).map((profile) => [profile.id, profile.name]));
    } catch {
        return undefined;
    }
}

export type ReceiptRow = {name: string; outcome: string; job_url?: string | undefined};

// Contract 12.1: an accepted receipt's sentence depends on why the runner
// took the task, and whether the job it created already needs a fix.
function accepted_receipt_sentence(
    reason: string | undefined,
    job_status: string | null | undefined,
): string {
    switch (reason) {
        case "runner_offline":
        case "runner_unknown":
            return $t({
                defaultMessage: "Task saved. It starts when the agent's device connects.",
            });
        case "runner_busy":
            return $t({
                defaultMessage: "Task saved. It starts after the agent finishes its current task.",
            });
        default:
            return job_status === "blocked"
                ? $t({
                      defaultMessage:
                          "Task saved, but this agent needs a fix before it can start. Ask its owner.",
                  })
                : $t({defaultMessage: "This agent started a task."});
    }
}

export function receipt_rows(
    receipts: {
        profile_id: string;
        decision: string;
        reason?: string;
        job_id: string | null;
        job_status?: string | null;
    }[],
    names: Map<string, string> | undefined,
): ReceiptRow[] {
    return receipts.map((receipt) => {
        // The profile list holds every agent that is shared with the viewer.
        const not_shared = names !== undefined && !names.has(receipt.profile_id);
        const name =
            names?.get(receipt.profile_id) ??
            (not_shared
                ? $t({defaultMessage: "An agent that is not shared with you"})
                : $t({defaultMessage: "An agent"}));
        let outcome;
        switch (receipt.decision) {
            case "accepted":
                outcome = accepted_receipt_sentence(receipt.reason, receipt.job_status);
                break;
            case "needs_input":
                outcome = $t({defaultMessage: "Choose a repository or complete the task first."});
                break;
            case "rejected":
                // A missing name already means "not shared with you"; that
                // sentence outranks a server reason meant for other cases.
                outcome = not_shared
                    ? $t({defaultMessage: "Ask its owner to share it with you."})
                    : receipt.reason === "admission_denied"
                      ? $t({
                            defaultMessage:
                                "Your message was sent, but the task is not allowed. Check your access or choose another agent.",
                        })
                      : (dispatch_receipt_reason_label(receipt.reason ?? "") ??
                        $t({defaultMessage: "This agent started no task."}));
                break;
            default:
                outcome = $t({defaultMessage: "The task status for this agent is unknown."});
        }
        return {
            name,
            outcome,
            // A malformed job ID must hide the link rather than build a dead one.
            job_url:
                receipt.job_id !== null && valid_job_id(receipt.job_id)
                    ? job_hash(receipt.job_id)
                    : undefined,
        };
    });
}

function show_banner(rows: ReceiptRow[], unavailable: boolean): void {
    compose_banner.clear_agent_task_receipt_banner();
    const $banner = $(
        render_agent_dispatch_receipt_banner({
            banner_type:
                unavailable || rows.some((row) => row.job_url === undefined)
                    ? compose_banner.WARNING
                    : compose_banner.SUCCESS,
            classname: compose_banner.CLASSNAMES.agent_task_receipt_banner,
            rows,
            has_rows: rows.length > 0,
            unavailable,
        }),
    );
    compose_banner.append_compose_banner_to_banner_list($banner, $("#compose_banners"));
}

export async function report_dispatch(message_id: number): Promise<void> {
    try {
        const result = await api.message_dispatch(message_id);
        if (result.dispatch_receipts.length === 0) {
            return;
        }
        show_banner(receipt_rows(result.dispatch_receipts, await profile_names()), false);
    } catch {
        show_banner([], true);
    }
}

export async function show_receipts(message_id: number): Promise<void> {
    let content_html;
    try {
        const result = await api.message_dispatch(message_id);
        const rows = receipt_rows(result.dispatch_receipts, await profile_names());
        content_html = render_agent_dispatch_receipts({rows, has_rows: rows.length > 0});
    } catch {
        content_html = render_agent_dispatch_receipts({has_rows: false, unavailable: true});
    }
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "Agent task receipt"}),
        modal_content_html: content_html,
        modal_submit_button_text: $t({defaultMessage: "Close"}),
        single_footer_button: true,
        close_on_submit: true,
        on_click() {
            return;
        },
    });
}
