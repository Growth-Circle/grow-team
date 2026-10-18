import * as z from "zod/mini";

import * as channel from "./channel.ts";

// The data side of the Playbooks page (playbooks_ui.ts): what the
// server sends, the address of each screen, and the small decisions
// that need no page to test.

export const MAX_TITLE_LENGTH = 100;
export const MAX_CONTENT_LENGTH = 100_000;
export const MAX_SUMMARY_LENGTH = 200;

const revision_summary_schema = z.object({
    number: z.number(),
    author_id: z.number(),
    date_created: z.number(),
    summary: z.string(),
});
export type RevisionSummary = z.infer<typeof revision_summary_schema>;

const revision_schema = z.object({
    ...revision_summary_schema.shape,
    content: z.string(),
    rendered_content: z.string(),
});
export type Revision = z.infer<typeof revision_schema>;

const playbook_fields = {
    id: z.number(),
    title: z.string(),
    slug: z.string(),
    scope: z.enum(["shared", "personal"]),
    owner_id: z.number(),
    archived: z.boolean(),
    date_created: z.number(),
};

export const playbook_schema = z.object({
    ...playbook_fields,
    latest_revision: revision_summary_schema,
});
export type Playbook = z.infer<typeof playbook_schema>;

const playbook_detail_schema = z.object({
    ...playbook_fields,
    latest_revision: revision_schema,
    can_edit: z.boolean(),
    can_archive: z.boolean(),
});
export type PlaybookDetail = z.infer<typeof playbook_detail_schema>;

const list_response_schema = z.object({playbooks: z.array(playbook_schema)});
const detail_response_schema = z.object({playbook: playbook_detail_schema});
const get_response_schema = z.object({
    playbook: playbook_detail_schema,
    revisions: z.array(revision_summary_schema),
});
const revision_response_schema = z.object({revision: revision_schema});
const diff_response_schema = z.object({diff: z.string()});
const preview_response_schema = z.object({rendered_content: z.string()});

// ----- Addresses -----

// "mine" lists the playbooks that only this person can open.
export type Tab = "shared" | "mine";
export type Scope = "shared" | "personal";
export type Panel = "view" | "edit" | "history";

export type Route =
    | {kind: "list"; tab: Tab}
    | {kind: "new"; scope: Scope}
    | {kind: "detail"; id: number; panel: Panel; revision: number | undefined};

function positive_integer(text: string | undefined): number | undefined {
    if (text === undefined || !/^[1-9]\d*$/.test(text)) {
        return undefined;
    }
    return Number(text);
}

// `args` is the hash after "playbooks": #playbooks, #playbooks/mine,
// #playbooks/new[/mine], #playbooks/<id>, #playbooks/<id>/edit and
// #playbooks/<id>/history[/<revision>]. An address that names nothing
// real opens the list.
export function parse_route(args: string[]): Route {
    const [first, second, third] = args;
    if (first === "new") {
        return {kind: "new", scope: second === "mine" ? "personal" : "shared"};
    }
    const id = positive_integer(first);
    if (id === undefined) {
        return {kind: "list", tab: first === "mine" ? "mine" : "shared"};
    }
    if (second === "edit") {
        return {kind: "detail", id, panel: "edit", revision: undefined};
    }
    if (second === "history") {
        return {kind: "detail", id, panel: "history", revision: positive_integer(third)};
    }
    return {kind: "detail", id, panel: "view", revision: undefined};
}

export function route_hash(route: Route): string {
    if (route.kind === "list") {
        return route.tab === "mine" ? "#playbooks/mine" : "#playbooks";
    }
    if (route.kind === "new") {
        return route.scope === "personal" ? "#playbooks/new/mine" : "#playbooks/new";
    }
    const base = `#playbooks/${route.id}`;
    if (route.panel === "edit") {
        return `${base}/edit`;
    }
    if (route.panel === "history") {
        return route.revision === undefined
            ? `${base}/history`
            : `${base}/history/${route.revision}`;
    }
    return base;
}

export function detail_hash(id: number, panel: Panel = "view", revision?: number): string {
    return route_hash({kind: "detail", id, panel, revision});
}

// ----- Lists -----

export function tab_of(playbook: Playbook): Tab {
    return playbook.scope === "personal" ? "mine" : "shared";
}

export function playbooks_in_tab(playbooks: Playbook[], tab: Tab): Playbook[] {
    return playbooks.filter((playbook) => tab_of(playbook) === tab);
}

export function sort_playbooks(playbooks: Playbook[]): Playbook[] {
    return playbooks.toSorted(
        (a, b) => b.latest_revision.date_created - a.latest_revision.date_created || b.id - a.id,
    );
}

// ----- Editing -----

export type Draft = {title: string; content: string; summary: string};

export function is_dirty(saved: {title: string; content: string}, draft: Draft): boolean {
    return draft.title.trim() !== saved.title || draft.content !== saved.content;
}

// A title is required, and a blank one cannot be saved. A new playbook
// needs some text too.
export function can_save(
    saved: {title: string; content: string} | undefined,
    draft: Draft,
): boolean {
    if (draft.title.trim() === "" || draft.content.length > MAX_CONTENT_LENGTH) {
        return false;
    }
    if (saved === undefined) {
        return draft.content.trim() !== "";
    }
    return is_dirty(saved, draft);
}

// ----- Diff -----

export type DiffLineKind = "added" | "removed" | "context" | "hunk";
export type DiffLine = {kind: DiffLineKind; text: string};

// Reads the unified diff that the server sends. Its two header lines
// name the revisions, and the page names them in its own words.
export function parse_diff(diff: string): DiffLine[] {
    if (diff === "") {
        return [];
    }
    const lines = diff.split("\n");
    const body = lines[0]?.startsWith("--- revision ") === true ? lines.slice(2) : lines;
    return body.map((line): DiffLine => {
        if (line.startsWith("@@")) {
            return {kind: "hunk", text: line};
        }
        if (line.startsWith("+")) {
            return {kind: "added", text: line.slice(1)};
        }
        if (line.startsWith("-")) {
            return {kind: "removed", text: line.slice(1)};
        }
        return {kind: "context", text: line.slice(1)};
    });
}

// ----- Requests -----

export async function fetch_playbooks(options: {
    archived: boolean;
    search: string;
}): Promise<Playbook[]> {
    const data: Record<string, string> = {archived: JSON.stringify(options.archived)};
    if (options.search.trim() !== "") {
        data["search"] = options.search.trim();
    }
    const result: unknown = await channel.get({url: "/json/playbooks", data});
    return list_response_schema.parse(result).playbooks;
}

export async function fetch_playbook(
    id: number,
): Promise<{playbook: PlaybookDetail; revisions: RevisionSummary[]}> {
    const result: unknown = await channel.get({url: `/json/playbooks/${id}`});
    return get_response_schema.parse(result);
}

export async function create_playbook(params: {
    title: string;
    content: string;
    scope: Scope;
    summary: string;
}): Promise<PlaybookDetail> {
    const result: unknown = await channel.post({url: "/json/playbooks", data: params});
    return detail_response_schema.parse(result).playbook;
}

export async function save_playbook(
    id: number,
    params: {base_revision: number; title: string; content: string; summary: string},
): Promise<PlaybookDetail> {
    const result: unknown = await channel.patch({url: `/json/playbooks/${id}`, data: params});
    return detail_response_schema.parse(result).playbook;
}

export async function set_archived(id: number, archived: boolean): Promise<PlaybookDetail> {
    const action = archived ? "archive" : "unarchive";
    const result: unknown = await channel.post({url: `/json/playbooks/${id}/${action}`});
    return detail_response_schema.parse(result).playbook;
}

export async function fetch_revision(id: number, number: number): Promise<Revision> {
    const result: unknown = await channel.get({url: `/json/playbooks/${id}/revisions/${number}`});
    return revision_response_schema.parse(result).revision;
}

export async function fetch_diff(id: number, from: number, to: number): Promise<DiffLine[]> {
    const result: unknown = await channel.get({
        url: `/json/playbooks/${id}/diff`,
        data: {from_revision: from, to_revision: to},
    });
    return parse_diff(diff_response_schema.parse(result).diff);
}

export async function render_preview(content: string): Promise<string> {
    const result: unknown = await channel.post({url: "/json/playbooks/render", data: {content}});
    return preview_response_schema.parse(result).rendered_content;
}
