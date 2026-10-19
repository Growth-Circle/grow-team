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

// The card a notification is about. It is null when the person can no
// longer see the card.
export const need_task_schema = z.object({
    id: z.number(),
    display_id: z.string(),
    title: z.string(),
});

// The call an external MCP client made to an Ask first tool, with what the
// person approves: the tool, where it goes, and the whole content.
export const need_mcp_call_schema = z.object({
    call_id: z.string(),
    grant_name: z.string(),
    tool: z.string(),
    target: z.object({
        type: z.enum(["channel", "direct", "message", "task", "column", "file"]),
        name: z.string(),
        topic: z.nullable(z.string()),
        message_id: z.nullable(z.number()),
    }),
    content: z.string(),
    mentions: z.array(z.string()),
    digest: z.string(),
});

// The unread messages of a followed topic since the person last wrote
// there. The first one is where the topic opens.
export const need_reply_schema = z.object({
    count: z.number(),
    first_unread_message_id: z.number(),
    sender_names: z.array(z.string()),
});

const NEED_KINDS = [
    "approval",
    "mention",
    "decision",
    "card_mention",
    "card_assigned",
    "reply",
] as const;

export const need_item_schema = z.object({
    id: z.string(),
    kind: z.enum(NEED_KINDS),
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
    task: z.nullable(need_task_schema),
    // A mention that names three people or more, or the whole channel.
    broadcast: z.boolean(),
    // Only on an approval for an external MCP client's call.
    mcp_call: z.optional(need_mcp_call_schema),
    // Only on a reply.
    reply: z.optional(need_reply_schema),
});

export const need_counts_schema = z.object({
    approval: z.number(),
    mention: z.number(),
    decision: z.number(),
    card_mention: z.number(),
    card_assigned: z.number(),
    reply: z.number(),
    all: z.number(),
});

const needs_result_schema = z.object({
    items: z.array(need_item_schema),
    // The counts leave out team-wide items, which broadcast_counts has.
    counts: need_counts_schema,
    broadcast_counts: need_counts_schema,
});

export type NeedActor = z.infer<typeof need_actor_schema>;
export type NeedMcpCall = z.infer<typeof need_mcp_call_schema>;
export type NeedItem = z.infer<typeof need_item_schema>;
export type NeedCounts = z.infer<typeof need_counts_schema>;
export type NeedsResult = z.infer<typeof needs_result_schema>;
export type NeedKind = NeedItem["kind"];
// A tab can hold more than one kind: a mention in a message and a
// mention in a card note are both "mentions".
export type NeedTab = "all" | "mentions" | "replies" | "assigned" | "approval" | "decision";
export const NEED_TABS: NeedTab[] = [
    "all",
    "mentions",
    "replies",
    "assigned",
    "approval",
    "decision",
];

const TAB_FOR_KIND: Record<NeedKind, Exclude<NeedTab, "all">> = {
    approval: "approval",
    mention: "mentions",
    decision: "decision",
    card_mention: "mentions",
    card_assigned: "assigned",
    reply: "replies",
};

export function tab_for_kind(kind: NeedKind): Exclude<NeedTab, "all"> {
    return TAB_FOR_KIND[kind];
}

// An address can name a tab, and the ones from before the tabs merged
// named a kind: #needs/mention, #needs/card_mention, #needs/card_assigned.
export function tab_for_hash_arg(arg: string | undefined): NeedTab | undefined {
    const tab = NEED_TABS.find((value) => value === arg);
    if (tab !== undefined) {
        return tab;
    }
    const kind = NEED_KINDS.find((value) => value === arg);
    return kind === undefined ? undefined : tab_for_kind(kind);
}

export async function fetch_open(): Promise<NeedsResult> {
    const result: unknown = await channel.get({url: "/json/needs"});
    return needs_result_schema.parse(result);
}

export async function fetch_done_today(): Promise<NeedsResult> {
    const result: unknown = await channel.get({url: "/json/needs?status=resolved&since=today"});
    return needs_result_schema.parse(result);
}

function items_in_tab(items: NeedItem[], tab: NeedTab): NeedItem[] {
    if (tab === "all") {
        return items;
    }
    return items.filter((item) => tab_for_kind(item.kind) === tab);
}

// What a tab shows. Team-wide mentions stay out of the way until the
// person asks for them.
export function items_for_tab(
    items: NeedItem[],
    tab: NeedTab,
    {show_team_wide}: {show_team_wide: boolean},
): NeedItem[] {
    const in_tab = items_in_tab(items, tab);
    return show_team_wide ? in_tab : in_tab.filter((item) => !item.broadcast);
}

// The number on a tab counts what the tab shows, so it never promises
// more cards than the person can see. It changes at once when the
// person handles an item or turns team-wide mentions on.
export function count_for_tab(
    items: NeedItem[],
    tab: NeedTab,
    options: {show_team_wide: boolean},
): number {
    return items_for_tab(items, tab, options).length;
}

// The number in the sidebar and in the title of the page: what waits
// on the person themself, whether or not the team-wide mentions show.
export function personal_count(items: NeedItem[]): number {
    return items.filter((item) => !item.broadcast).length;
}

export function team_wide_count_for_tab(items: NeedItem[], tab: NeedTab): number {
    return items_in_tab(items, tab).filter((item) => item.broadcast).length;
}

// Open mentions from the same topic of a channel make one row, so a
// topic that mentions the person three times is not three cards.
export type NeedGroup = {
    key: string;
    stream_id: number;
    topic: string;
    // Newest first, as the list is.
    items: NeedItem[];
};

export type NeedRow = {group: NeedGroup} | {item: NeedItem};

export function group_key_of(item: NeedItem): string | undefined {
    if (item.kind !== "mention" || item.stream_id === null || item.topic === null) {
        return undefined;
    }
    return `${item.stream_id}/${item.topic}`;
}

export function group_key_for_item(item: NeedItem, items: NeedItem[]): string | undefined {
    const key = group_key_of(item);
    if (key === undefined) {
        return undefined;
    }
    return items.filter((other) => group_key_of(other) === key).length > 1 ? key : undefined;
}

// A group sits where its newest item sat. A topic with one item stays
// a plain card.
export function rows_for_items(items: NeedItem[]): NeedRow[] {
    const groups = new Map<string, NeedItem[]>();
    for (const item of items) {
        const key = group_key_of(item);
        if (key !== undefined) {
            groups.set(key, [...(groups.get(key) ?? []), item]);
        }
    }
    const rows: NeedRow[] = [];
    const placed = new Set<string>();
    for (const item of items) {
        const key = group_key_of(item);
        const members = key === undefined ? undefined : groups.get(key);
        if (key === undefined || members === undefined || members.length < 2) {
            rows.push({item});
        } else if (!placed.has(key)) {
            placed.add(key);
            // Both are set when a key exists.
            rows.push({
                group: {key, stream_id: item.stream_id!, topic: item.topic!, items: members},
            });
        }
    }
    return rows;
}

// A note that mentions the person, or a card they are now responsible
// for. Neither waits on an agent, and both are answered on the card.
export function is_card_kind(kind: NeedKind): boolean {
    return kind === "card_mention" || kind === "card_assigned";
}

// A mention in a note shows the card title, then the note as far as it
// fits. A new responsibility is only the title, which is its text.
export function card_text(item: NeedItem): {text: string; excerpt: string} {
    if (item.kind === "card_mention" && item.task !== null) {
        return {text: item.task.title, excerpt: item.text};
    }
    return {text: item.text, excerpt: ""};
}

// The choices an agent offers with a decision. An agent can also ask
// an open question with no choices.
export function decision_options(item: NeedItem): string[] {
    return item.actions.filter((option) => option.trim() !== "");
}

// Each item's id is "<kind>:<raw id>". A decision's raw id is also its
// job_id, an approval's is the approval id, a mention's is the message
// id, and a card notification's is the notification id.
export function raw_id(need_id: string): string {
    return need_id.slice(need_id.indexOf(":") + 1);
}

// How much of a call's content the card shows before "Show all". The
// server cuts the item's text at the same length.
export const MCP_CALL_TEXT_LIMIT = 600;

export function is_mcp_call(item: NeedItem): boolean {
    return item.mcp_call !== undefined;
}

export function mcp_call_content(
    call: NeedMcpCall,
    expanded: boolean,
): {text: string; can_expand: boolean} {
    const can_expand = call.content.length > MCP_CALL_TEXT_LIMIT;
    return {
        text: can_expand && !expanded ? call.content.slice(0, MCP_CALL_TEXT_LIMIT) : call.content,
        can_expand,
    };
}

// An approval is bound to the digest and nonce the person saw, so a call
// that changed in between is refused by the server.
async function decide_mcp_call(item: NeedItem, decision: "approve" | "reject"): Promise<void> {
    await channel.post({
        url: `/json/mcp/calls/${raw_id(item.id)}/decision`,
        data: {decision, argument_digest: item.operation_hash, nonce: item.nonce},
    });
}

export async function commit_reject(item: NeedItem): Promise<void> {
    if (!is_mcp_call(item)) {
        throw new Error("Only a call of an external client can be rejected here.");
    }
    await decide_mcp_call(item, "reject");
}

export async function commit_approve(item: NeedItem): Promise<void> {
    if (is_mcp_call(item)) {
        await decide_mcp_call(item, "approve");
        return;
    }
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

export async function commit_card_seen(item: NeedItem): Promise<void> {
    await channel.post({url: `/json/needs/card_notifications/${raw_id(item.id)}/resolve`});
}

// A mention or a card item can be handled with others in one call.
// Approvals and decisions are answered one at a time.
export function is_bulk_resolvable(item: NeedItem): boolean {
    return item.kind === "mention" || is_card_kind(item.kind);
}

// What the person can mark done together in a tab: the cards they can
// see in it.
export function resolvable_items_for_tab(
    items: NeedItem[],
    tab: NeedTab,
    options: {show_team_wide: boolean},
): NeedItem[] {
    return items_for_tab(items, tab, options).filter((item) => is_bulk_resolvable(item));
}

// How long "Undo" stays on the toast after a bulk action.
export const BULK_UNDO_MS = 10_000;

const bulk_result_schema = z.object({
    done: z.array(z.string()),
    failed: z.array(z.string()),
});
export type BulkResult = z.infer<typeof bulk_result_schema>;

const snooze_result_schema = z.object({until: z.string()});

function ids_data(items: NeedItem[]): {need_ids: string} {
    return {need_ids: JSON.stringify(items.map((item) => item.id))};
}

// The server handles each item on its own: the ones it could not
// handle come back in `failed`, and the rest stay handled.
export async function commit_bulk_done(items: NeedItem[]): Promise<BulkResult> {
    const result: unknown = await channel.post({url: "/json/needs/resolve", data: ids_data(items)});
    return bulk_result_schema.parse(result);
}

export async function commit_bulk_undo(items: NeedItem[]): Promise<BulkResult> {
    const result: unknown = await channel.post({
        url: "/json/needs/unresolve",
        data: ids_data(items),
    });
    return bulk_result_schema.parse(result);
}

// Returns when the items list again.
export async function commit_snooze(items: NeedItem[]): Promise<Date> {
    const result: unknown = await channel.post({url: "/json/needs/snooze", data: ids_data(items)});
    return new Date(snooze_result_schema.parse(result).until);
}

export async function commit_unsnooze(items: NeedItem[]): Promise<void> {
    await channel.post({url: "/json/needs/unsnooze", data: ids_data(items)});
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
