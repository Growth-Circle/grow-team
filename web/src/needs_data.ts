import * as z from "zod/mini";

import * as agent_api from "./agent_api.ts";
import {new_client_key} from "./agent_ui_state.ts";
import * as channel from "./channel.ts";

// Data and actions for the Needs you inbox (spec 03, 09 C2). The view
// (needs_ui.ts) renders what this module fetches, and calls back here
// to commit or cancel a delayed action. The Today page reuses the
// commit functions and the delay and undo scheduler for the same
// three kinds of item.

export const need_actor_schema = z.object({
    type: z.string(),
    id: z.number(),
    name: z.string(),
    agent_role: z.nullable(z.string()),
    shape: z.nullable(z.string()),
    color: z.nullable(z.string()),
    initials: z.string(),
});

export const need_attachment_schema = z.object({
    kind: z.string(),
    id: z.string(),
    filename: z.string(),
});

export const need_item_schema = z.object({
    id: z.string(),
    kind: z.enum(["approval", "mention", "decision"]),
    actor: need_actor_schema,
    stream_id: z.nullable(z.number()),
    topic: z.nullable(z.string()),
    time: z.string(),
    text: z.string(),
    attachment: z.nullable(need_attachment_schema),
    actions: z.array(z.string()),
    expires_at: z.string(),
    expired: z.boolean(),
    resolved_at: z.nullable(z.string()),
    resolved_action: z.nullable(z.string()),
    job_id: z.nullable(z.string()),
    approval_version: z.nullable(z.number()),
    operation_hash: z.nullable(z.string()),
    nonce: z.nullable(z.string()),
    job_version: z.nullable(z.number()),
});

export const need_counts_schema = z.object({
    approval: z.number(),
    mention: z.number(),
    decision: z.number(),
    all: z.number(),
});

const needs_result_schema = z.object({
    items: z.array(need_item_schema),
    counts: need_counts_schema,
});

export type NeedActor = z.infer<typeof need_actor_schema>;
export type NeedItem = z.infer<typeof need_item_schema>;
export type NeedCounts = z.infer<typeof need_counts_schema>;
export type NeedsResult = z.infer<typeof needs_result_schema>;
export type NeedKind = NeedItem["kind"];
export type NeedTab = NeedKind | "all";

export async function fetch_open(): Promise<NeedsResult> {
    const result: unknown = await channel.get({url: "/json/needs"});
    return needs_result_schema.parse(result);
}

export async function fetch_done_today(): Promise<NeedsResult> {
    const result: unknown = await channel.get({url: "/json/needs?status=resolved&since=today"});
    return needs_result_schema.parse(result);
}

export function items_for_tab(items: NeedItem[], tab: NeedTab): NeedItem[] {
    if (tab === "all") {
        return items;
    }
    return items.filter((item) => item.kind === tab);
}

// The number on a tab is what the tab shows, so it changes at once
// when the person handles an item.
export function count_for_tab(items: NeedItem[], tab: NeedTab): number {
    return items_for_tab(items, tab).length;
}

// The choices an agent offers with a decision. An agent can also ask
// an open question with no choices.
export function decision_options(item: NeedItem): string[] {
    return item.actions.filter((option) => option.trim() !== "");
}

// Each item's id is "<kind>:<raw id>". A decision's raw id is also its
// job_id, an approval's is the approval id, and a mention's is the
// message id.
export function raw_id(need_id: string): string {
    return need_id.slice(need_id.indexOf(":") + 1);
}

export async function commit_approve(item: NeedItem): Promise<void> {
    await agent_api.decide_approval(raw_id(item.id), {
        expected_version: item.approval_version,
        operation_hash: item.operation_hash,
        nonce: item.nonce,
        decision: "approved",
    });
}

export async function commit_decision(item: NeedItem, choice: string): Promise<void> {
    if (item.job_id === null) {
        throw new Error("A decision need must have a job id.");
    }
    await agent_api.job_action(item.job_id, "inputs", {
        expected_version: item.job_version,
        client_key: new_client_key(),
        text: choice,
        input_type: "answer",
    });
}

export async function commit_mention_done(item: NeedItem): Promise<void> {
    await channel.post({url: `/json/needs/mentions/${raw_id(item.id)}/resolve`});
}

// A committed action cannot be undone, so "Undo" only ever cancels a
// call that has not gone out yet. Each pending need_id maps to one
// timer; scheduling the same id again replaces it.
export const UNDO_DELAY_MS = 5000;

const timers = new Map<string, ReturnType<typeof setTimeout>>();

export function schedule_pending(
    need_id: string,
    commit: () => Promise<void>,
    on_settle: (error: unknown) => void,
): void {
    cancel_pending(need_id);
    const timer = setTimeout(() => {
        timers.delete(need_id);
        void (async () => {
            try {
                await commit();
                on_settle(undefined);
            } catch (error: unknown) {
                on_settle(error);
            }
        })();
    }, UNDO_DELAY_MS);
    timers.set(need_id, timer);
}

export function cancel_pending(need_id: string): boolean {
    const timer = timers.get(need_id);
    if (timer === undefined) {
        return false;
    }
    clearTimeout(timer);
    timers.delete(need_id);
    return true;
}

export function is_pending(need_id: string): boolean {
    return timers.has(need_id);
}
