import $ from "jquery";
import assert from "minimalistic-assert";
import * as z from "zod/mini";

import render_playbook_detail_page from "../templates/playbook_detail_page.hbs";
import render_playbook_editor_banners from "../templates/playbook_editor_banners.hbs";
import render_playbooks_list from "../templates/playbooks_list.hbs";
import render_playbooks_list_page from "../templates/playbooks_list_page.hbs";

import * as browser_history from "./browser_history.ts";
import * as compose_closed_ui from "./compose_closed_ui.ts";
import * as confirm_dialog from "./confirm_dialog.ts";
import * as feedback_widget from "./feedback_widget.ts";
import {$t, $t_html} from "./i18n.ts";
import * as left_sidebar_navigation_area from "./left_sidebar_navigation_area.ts";
import * as live_updates from "./live_updates.ts";
import * as people from "./people.ts";
import type {
    DiffLine,
    Draft,
    Playbook,
    PlaybookDetail,
    Revision,
    RevisionSummary,
    Route,
    Scope,
    Tab,
} from "./playbooks_data.ts";
import * as playbooks_data from "./playbooks_data.ts";
import * as sanji_format from "./sanji_format.ts";
import {current_user} from "./state_data.ts";
import * as views_util from "./views_util.ts";

// The Playbooks page (#playbooks, #playbooks/mine, #playbooks/new,
// #playbooks/<id>, #playbooks/<id>/edit, #playbooks/<id>/history): the
// shared and personal playbooks of the workspace, one playbook with
// its history, and the editor for a new or a changed one.

const SEARCH_DELAY_MS = 250;
const EVENT_REFRESH_DELAY_MS = 400;

type LoadState = "loading" | "ready" | "failed";
type DetailState = LoadState | "missing";

let visible = false;
let initialized = false;
let route: Route = {kind: "list", tab: "shared"};
// Raised by every change of address, so that a response for an address
// the person has left is dropped.
let visit = 0;

// The list.
let items: Playbook[] = [];
let list_state: LoadState = "loading";
let search = "";
let show_archived = false;
let search_timer: ReturnType<typeof setTimeout> | undefined;
let refresh_timer: ReturnType<typeof setTimeout> | undefined;

// The playbook that is open.
let detail: PlaybookDetail | undefined;
let revisions: RevisionSummary[] = [];
let detail_state: DetailState = "loading";
// The history panel: the revision on screen and what it shows.
let revision_state: LoadState = "loading";
let revision_content: Revision | undefined;
let revision_diff: DiffLine[] = [];

// The editor.
let editing = false;
let draft: Draft = {title: "", content: "", summary: ""};
let new_scope: Scope = "shared";
let base_revision = 0;
let previewing = false;
let preview_state: LoadState = "ready";
let preview_html = "";
let saving = false;
let error_text = "";
let notice_text = "";
let conflict_revision: number | undefined;
// Text that the person left in an editor without saving, by editor.
const kept_drafts = new Map<string, Draft>();

function set_visible(value: boolean): void {
    visible = value;
}

function $view(): JQuery {
    return $("#playbooks-view");
}

// ----- Small helpers -----

function time_of(seconds: number): {time: string; full_time: string} {
    const date = new Date(seconds * 1000);
    return {
        time: sanji_format.format_relative(date),
        full_time: sanji_format.format_date_time(date),
    };
}

function name_of(user_id: number): string {
    return (
        people.maybe_get_user_by_id(user_id, true)?.full_name ??
        $t({defaultMessage: "a former member"})
    );
}

const error_schema = z.object({
    status: z.number(),
    responseJSON: z.optional(z.object({msg: z.optional(z.string())})),
});

function error_status(error: unknown): number | undefined {
    const parsed = error_schema.safeParse(error);
    return parsed.success ? parsed.data.status : undefined;
}

function error_message(error: unknown, fallback: string): string {
    const parsed = error_schema.safeParse(error);
    return (parsed.success ? parsed.data.responseJSON?.msg : undefined) ?? fallback;
}

function editor_key(): string | undefined {
    if (route.kind === "new") {
        return `new:${route.scope}`;
    }
    if (route.kind === "detail" && route.panel === "edit") {
        return `edit:${route.id}`;
    }
    return undefined;
}

// What the editor is compared with. A new playbook has nothing saved.
function saved_state(): {title: string; content: string} | undefined {
    if (route.kind === "new" || detail === undefined) {
        return undefined;
    }
    return {title: detail.title, content: detail.latest_revision.content};
}

function is_dirty(): boolean {
    if (!editing) {
        return false;
    }
    const saved = saved_state();
    if (saved === undefined) {
        return draft.title.trim() !== "" || draft.content !== "";
    }
    return playbooks_data.is_dirty(saved, draft);
}

function can_save(): boolean {
    return !saving && playbooks_data.can_save(saved_state(), draft);
}

// ----- The list -----

function visible_items(): Playbook[] {
    return playbooks_data.sort_playbooks(items);
}

function list_context(): Record<string, unknown> {
    const tab = route.kind === "list" ? route.tab : "shared";
    const rows = playbooks_data.playbooks_in_tab(visible_items(), tab).map((playbook) => ({
        title: playbook.title,
        hash: playbooks_data.detail_hash(playbook.id),
        edited_by: $t(
            {defaultMessage: "Edited by {name}"},
            {name: name_of(playbook.latest_revision.author_id)},
        ),
        ...time_of(playbook.latest_revision.date_created),
        revision_label: $t(
            {defaultMessage: "Revision {number}"},
            {number: playbook.latest_revision.number},
        ),
        archived: playbook.archived,
    }));
    const searching = search.trim() !== "";
    let empty_title: string;
    let empty_description: string;
    let empty_action_label: string | undefined;
    if (searching) {
        empty_title = $t(
            {defaultMessage: "No playbooks match “{search}”."},
            {search: search.trim()},
        );
        empty_description = $t({defaultMessage: "Try another word."});
    } else if (show_archived) {
        empty_title = $t({defaultMessage: "No archived playbooks."});
        empty_description = $t({defaultMessage: "A playbook you archive waits here."});
    } else if (tab === "mine") {
        empty_title = $t({defaultMessage: "You have no playbooks of your own."});
        empty_description = $t({defaultMessage: "A playbook of your own is for you alone."});
        empty_action_label = $t({defaultMessage: "New playbook"});
    } else {
        empty_title = $t({defaultMessage: "No shared playbooks yet."});
        empty_description = $t({
            defaultMessage: "Write a routine down once, and the whole team can follow it.",
        });
        empty_action_label = $t({defaultMessage: "New playbook"});
    }
    return {
        is_guest: current_user.is_guest,
        rows,
        is_empty: rows.length === 0,
        loading: list_state === "loading",
        load_failed: list_state === "failed",
        empty_title,
        empty_description,
        empty_action_label,
    };
}

const TABS: Tab[] = ["shared", "mine"];

function tab_label(tab: Tab): string {
    return tab === "shared" ? $t({defaultMessage: "Shared"}) : $t({defaultMessage: "Mine"});
}

function list_page_context(): Record<string, unknown> {
    const current_tab = route.kind === "list" ? route.tab : "shared";
    const tabs = TABS.map((value) => ({
        value,
        label: tab_label(value),
        count: sanji_format.format_number(playbooks_data.playbooks_in_tab(items, value).length),
        active: value === current_tab,
    }));
    return {...list_context(), search, show_archived, tabs};
}

function render_list_page(): void {
    $view().html(render_playbooks_list_page(list_page_context()));
}

// Draws what changes while the person types or while a load ends. The
// search box and the tabs stay where they are, with their focus.
function render_list_region(): void {
    if (route.kind !== "list") {
        return;
    }
    $view().find(".playbooks-list-region").html(render_playbooks_list(list_page_context()));
    for (const tab of TABS) {
        $view()
            .find(`.playbooks-tab[data-tab="${tab}"] .playbooks-tab-count`)
            .text(sanji_format.format_number(playbooks_data.playbooks_in_tab(items, tab).length));
    }
}

function load_list(): void {
    const load_visit = visit;
    const query = {archived: show_archived, search};
    void (async () => {
        try {
            const loaded = await playbooks_data.fetch_playbooks(query);
            if (load_visit !== visit || query.search !== search) {
                return;
            }
            items = loaded;
            list_state = "ready";
        } catch {
            if (load_visit !== visit) {
                return;
            }
            list_state = "failed";
        }
        render_list_region();
    })();
}

// ----- One playbook -----

function scope_label(playbook: PlaybookDetail): string {
    return playbook.scope === "shared"
        ? $t({defaultMessage: "Shared playbook"})
        : $t({defaultMessage: "My playbook"});
}

function history_entries(): Record<string, unknown>[] {
    const selected = selected_revision_number();
    const latest = revisions[0]?.number;
    return revisions.map((revision) => ({
        label: $t({defaultMessage: "Revision {number}"}, {number: revision.number}),
        author_name: name_of(revision.author_id),
        ...time_of(revision.date_created),
        summary: revision.summary,
        is_latest: revision.number === latest,
        selected: revision.number === selected,
        hash:
            detail === undefined
                ? "#playbooks"
                : playbooks_data.detail_hash(detail.id, "history", revision.number),
    }));
}

function selected_revision_number(): number | undefined {
    if (route.kind !== "detail" || route.panel !== "history") {
        return undefined;
    }
    const wanted = route.revision;
    if (wanted !== undefined && revisions.some((revision) => revision.number === wanted)) {
        return wanted;
    }
    return revisions[0]?.number;
}

function editor_context(): Record<string, unknown> {
    const is_new = route.kind === "new";
    return {
        is_new,
        scope_options: [
            {
                value: "shared",
                label: tab_label("shared"),
                hint: $t({
                    defaultMessage:
                        "Everyone in the workspace, except guests, can open and change it.",
                }),
                checked: new_scope === "shared",
            },
            {
                value: "personal",
                label: tab_label("mine"),
                hint: $t({defaultMessage: "Only you can open it."}),
                checked: new_scope === "personal",
            },
        ],
        draft_title: draft.title,
        content: draft.content,
        summary: draft.summary,
        previewing,
        preview_loading: preview_state === "loading",
        preview_failed: preview_state === "failed",
        preview_empty: draft.content.trim() === "",
        preview_html,
        max_title_length: playbooks_data.MAX_TITLE_LENGTH,
        max_content_length: playbooks_data.MAX_CONTENT_LENGTH,
        max_summary_length: playbooks_data.MAX_SUMMARY_LENGTH,
        can_save: can_save(),
        save_label: save_label(),
        ...banner_context(),
    };
}

function save_label(): string {
    if (saving) {
        return $t({defaultMessage: "Saving…"});
    }
    return route.kind === "new"
        ? $t({defaultMessage: "Create playbook"})
        : $t({defaultMessage: "Save"});
}

function banner_context(): Record<string, unknown> {
    return {
        conflict_text:
            conflict_revision === undefined
                ? undefined
                : $t(
                      {
                          defaultMessage:
                              "Someone saved revision {number} while you were editing. Your text is still here. Copy it before you reload, because reloading replaces it.",
                      },
                      {number: conflict_revision},
                  ),
        error_text: error_text === "" ? undefined : error_text,
        notice_text: notice_text === "" ? undefined : notice_text,
    };
}

function detail_context(): Record<string, unknown> {
    const is_new = route.kind === "new";
    const showing_history = route.kind === "detail" && route.panel === "history";
    const playbook = detail_state === "ready" ? detail : undefined;
    const archived = detail?.archived ?? false;
    const selected = selected_revision_number();
    const first_selected = selected === 1;
    return {
        is_new,
        back_to_mine: is_new ? new_scope === "personal" : detail?.scope === "personal",
        scope_label: detail === undefined || is_new ? undefined : scope_label(detail),
        title: is_new
            ? $t({defaultMessage: "New playbook"})
            : (detail?.title ?? $t({defaultMessage: "Playbook"})),
        byline:
            playbook === undefined
                ? undefined
                : [
                      $t(
                          {defaultMessage: "Revision {number}"},
                          {number: playbook.latest_revision.number},
                      ),
                      $t(
                          {defaultMessage: "Edited by {name}"},
                          {name: name_of(playbook.latest_revision.author_id)},
                      ),
                      time_of(playbook.latest_revision.date_created).time,
                  ].join(" · "),
        editing: editing || is_new,
        showing_history,
        can_edit: playbook?.can_edit ?? false,
        can_open_history: playbook !== undefined,
        can_archive: playbook?.can_archive ?? false,
        edit_disabled_hint: $t({defaultMessage: "You can't change this playbook."}),
        archive_disabled_hint: $t({
            defaultMessage: "Only an administrator or the person who made it can archive it.",
        }),
        archive_label: archived ? $t({defaultMessage: "Restore"}) : $t({defaultMessage: "Archive"}),
        archive_icon: archived ? "unarchive" : "archive",
        load_failed: !is_new && detail_state === "failed",
        missing: !is_new && detail_state === "missing",
        loading: !is_new && detail_state === "loading",
        archived,
        rendered_content: detail?.latest_revision.rendered_content ?? "",
        revision_rendered_content: revision_content?.rendered_content ?? "",
        revisions: history_entries(),
        detail_heading: first_selected
            ? $t({defaultMessage: "Revision 1"})
            : $t({defaultMessage: "Changes in revision {number}"}, {number: selected ?? 0}),
        detail_loading: revision_state === "loading",
        detail_failed: revision_state === "failed",
        is_first: first_selected,
        no_changes: revision_state === "ready" && !first_selected && revision_diff.length === 0,
        diff_lines: revision_diff.map((line) => ({
            kind: line.kind,
            text: line.text,
            is_hunk: line.kind === "hunk",
            is_added: line.kind === "added",
            is_removed: line.kind === "removed",
            sign: {added: "+", removed: "−", context: "", hunk: ""}[line.kind],
        })),
        ...editor_context(),
    };
}

// The control that has focus now, as a selector that finds the same
// control after the page is drawn again.
function focus_selector(): string | undefined {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement) || $view().has(element).length === 0) {
        return undefined;
    }
    const mode = element.dataset["mode"];
    if (mode !== undefined) {
        return `.playbook-mode[data-mode="${mode}"]`;
    }
    for (const class_name of [
        "playbook-action-edit",
        "playbook-action-history",
        "playbook-action-archive",
        "playbook-retry",
        "playbook-reload",
        "playbook-title-input",
        "playbook-content-input",
        "playbook-summary-input",
    ]) {
        if (element.classList.contains(class_name)) {
            return `.${class_name}`;
        }
    }
    return undefined;
}

function render_detail(): void {
    const focused = focus_selector();
    $view().html(render_playbook_detail_page(detail_context()));
    if (focused !== undefined) {
        $(focused, $view()).first().trigger("focus");
    }
}

function render_banners(): void {
    $view().find(".playbook-banners").html(render_playbook_editor_banners(banner_context()));
}

function update_save_state(): void {
    $view().find(".playbook-save").prop("disabled", !can_save());
    $view().find(".playbook-save .action-button-label").text(save_label());
}

function load_detail(id: number, {quiet}: {quiet: boolean}): void {
    const load_visit = visit;
    if (!quiet) {
        detail_state = "loading";
    }
    void (async () => {
        try {
            const loaded = await playbooks_data.fetch_playbook(id);
            if (load_visit !== visit) {
                return;
            }
            detail = loaded.playbook;
            revisions = loaded.revisions;
            detail_state = "ready";
            if (quiet && editing) {
                // The page must not be drawn again under the cursor.
                if (detail.latest_revision.number > base_revision) {
                    conflict_revision = detail.latest_revision.number;
                    render_banners();
                }
                return;
            }
        } catch (error: unknown) {
            if (load_visit !== visit) {
                return;
            }
            detail_state = error_status(error) === 400 ? "missing" : "failed";
        }
        enter_panel();
    })();
}

function load_revision(): void {
    const load_visit = visit;
    const selected = selected_revision_number();
    if (detail === undefined || selected === undefined) {
        return;
    }
    const id = detail.id;
    revision_state = "loading";
    void (async () => {
        try {
            if (selected === 1) {
                revision_content = await playbooks_data.fetch_revision(id, 1);
                revision_diff = [];
            } else {
                revision_diff = await playbooks_data.fetch_diff(id, selected - 1, selected);
            }
            if (load_visit !== visit) {
                return;
            }
            revision_state = "ready";
        } catch {
            if (load_visit !== visit) {
                return;
            }
            revision_state = "failed";
        }
        render_detail();
    })();
}

// The playbook is loaded: show the panel that the address names.
function enter_panel(): void {
    if (route.kind !== "detail") {
        return;
    }
    if (route.panel === "edit" && detail !== undefined) {
        if (!editing) {
            start_editing_saved(detail);
        }
    } else {
        editing = false;
    }
    render_detail();
    if (route.panel === "history" && detail_state === "ready") {
        load_revision();
    }
    if (route.panel === "edit" && detail_state === "ready") {
        $view().find(".playbook-title-input").trigger("focus");
    }
}

function start_editing_saved(playbook: PlaybookDetail): void {
    const key = `edit:${playbook.id}`;
    const kept = kept_drafts.get(key);
    kept_drafts.delete(key);
    editing = true;
    base_revision = playbook.latest_revision.number;
    conflict_revision = undefined;
    error_text = "";
    notice_text = "";
    previewing = false;
    saving = false;
    if (kept === undefined) {
        draft = {title: playbook.title, content: playbook.latest_revision.content, summary: ""};
    } else {
        draft = kept;
        notice_text = $t({defaultMessage: "Your unsaved text is back."});
    }
}

function start_editing_new(scope: Scope): void {
    const key = `new:${scope}`;
    const kept = kept_drafts.get(key);
    kept_drafts.delete(key);
    editing = true;
    new_scope = scope;
    conflict_revision = undefined;
    error_text = "";
    notice_text = "";
    previewing = false;
    saving = false;
    draft = kept ?? {title: "", content: "", summary: ""};
    if (kept !== undefined) {
        notice_text = $t({defaultMessage: "Your unsaved text is back."});
    }
}

// ----- Going to an address -----

function keep_unsaved_text(): void {
    const key = editor_key();
    if (key !== undefined && is_dirty() && !saving) {
        kept_drafts.set(key, draft);
    }
}

function apply_route(args: string[]): void {
    // A person who leaves an editor by the back button keeps the text.
    keep_unsaved_text();
    // A guest has no playbooks. The page says so, and asks the server
    // for nothing.
    const next: Route = current_user.is_guest
        ? {kind: "list", tab: "shared"}
        : playbooks_data.parse_route(args);
    const same_playbook = route.kind === "detail" && next.kind === "detail" && route.id === next.id;
    route = next;
    visit += 1;
    editing = false;

    switch (route.kind) {
        case "list":
            detail = undefined;
            render_list_page();
            if (!current_user.is_guest) {
                load_list();
            }
            break;
        case "new":
            detail = undefined;
            start_editing_new(route.scope);
            detail_state = "ready";
            render_detail();
            $view().find(".playbook-title-input").trigger("focus");
            break;
        case "detail":
            if (same_playbook && detail !== undefined) {
                // The same playbook: keep what is loaded, and look again
                // in the background, in case it changed.
                detail_state = "ready";
                enter_panel();
                load_detail(route.id, {quiet: true});
            } else {
                detail = undefined;
                revisions = [];
                detail_state = "loading";
                render_detail();
                load_detail(route.id, {quiet: false});
            }
            break;
    }
}

function go_to(hash: string): void {
    browser_history.go_to_location(hash);
}

// Runs `action` at once when nothing would be lost, and otherwise
// after the person agrees to leave the unsaved text.
function leave_editor_then(action: () => void): void {
    if (!is_dirty() || saving) {
        action();
        return;
    }
    confirm_dialog.launch({
        modal_title_text: $t({defaultMessage: "Discard your changes?"}),
        modal_content_html: $t_html({
            defaultMessage: "The text you changed has not been saved.",
        }),
        modal_submit_button_text: $t({defaultMessage: "Discard"}),
        modal_exit_button_text: $t({defaultMessage: "Keep editing"}),
        on_click() {
            editing = false;
            action();
        },
    });
}

function leave_editor_for_view(): void {
    if (route.kind === "new") {
        go_to("#playbooks");
    } else if (route.kind === "detail") {
        go_to(playbooks_data.detail_hash(route.id));
    }
}

// ----- Saving -----

function save(): void {
    if (!can_save()) {
        return;
    }
    const current = route;
    const save_visit = visit;
    saving = true;
    error_text = "";
    render_banners();
    update_save_state();
    void (async () => {
        try {
            const saved =
                current.kind === "new"
                    ? await playbooks_data.create_playbook({
                          title: draft.title.trim(),
                          content: draft.content,
                          scope: new_scope,
                          summary: draft.summary.trim(),
                      })
                    : await playbooks_data.save_playbook(
                          current.kind === "detail" ? current.id : 0,
                          {
                              base_revision,
                              title: draft.title.trim(),
                              content: draft.content,
                              summary: draft.summary.trim(),
                          },
                      );
            saving = false;
            editing = false;
            detail = saved;
            detail_state = "ready";
            kept_drafts.delete(editor_key() ?? "");
            feedback_widget.show_toast({text: $t({defaultMessage: "Saved."})});
            if (save_visit === visit) {
                go_to(playbooks_data.detail_hash(saved.id));
            }
        } catch (error: unknown) {
            saving = false;
            if (save_visit !== visit) {
                return;
            }
            if (error_status(error) === 409) {
                conflict_revision = latest_revision_of(error) ?? base_revision + 1;
            } else {
                error_text = error_message(
                    error,
                    $t({defaultMessage: "The playbook was not saved. Try again."}),
                );
            }
            render_banners();
            update_save_state();
        }
    })();
}

const conflict_schema = z.object({
    responseJSON: z.object({latest_revision: z.number()}),
});

function latest_revision_of(error: unknown): number | undefined {
    const parsed = conflict_schema.safeParse(error);
    return parsed.success ? parsed.data.responseJSON.latest_revision : undefined;
}

function reload_after_conflict(): void {
    if (route.kind !== "detail") {
        return;
    }
    kept_drafts.delete(`edit:${route.id}`);
    editing = false;
    conflict_revision = undefined;
    visit += 1;
    load_detail(route.id, {quiet: false});
}

// ----- Preview -----

function show_preview(): void {
    previewing = true;
    preview_state = "loading";
    preview_html = "";
    render_detail();
    const preview_visit = visit;
    const content = draft.content;
    void (async () => {
        try {
            const html = await playbooks_data.render_preview(content);
            if (preview_visit !== visit || !previewing) {
                return;
            }
            preview_html = html;
            preview_state = "ready";
        } catch {
            if (preview_visit !== visit || !previewing) {
                return;
            }
            preview_state = "failed";
        }
        render_detail();
    })();
}

function show_write(): void {
    previewing = false;
    preview_state = "ready";
    render_detail();
}

// ----- Archiving -----

function archive_or_restore(): void {
    if (detail === undefined) {
        return;
    }
    const playbook = detail;
    if (playbook.archived) {
        void set_archived(playbook, false);
        return;
    }
    confirm_dialog.launch({
        modal_title_text: $t({defaultMessage: "Archive this playbook?"}),
        modal_content_html: $t_html({
            defaultMessage: "It leaves the list. You can restore it from the Archived list.",
        }),
        modal_submit_button_text: $t({defaultMessage: "Archive"}),
        on_click() {
            void set_archived(playbook, true);
        },
    });
}

async function set_archived(playbook: PlaybookDetail, archived: boolean): Promise<void> {
    const archive_visit = visit;
    try {
        const updated = await playbooks_data.set_archived(playbook.id, archived);
        if (archive_visit !== visit) {
            return;
        }
        detail = updated;
        feedback_widget.show_toast({
            text: archived
                ? $t({defaultMessage: "Playbook archived."})
                : $t({defaultMessage: "Playbook restored."}),
        });
        if (archived) {
            go_to(updated.scope === "personal" ? "#playbooks/mine" : "#playbooks");
        } else {
            render_detail();
        }
    } catch (error: unknown) {
        feedback_widget.show_toast({
            text: error_message(error, $t({defaultMessage: "That did not work. Try again."})),
            variant: "error",
        });
    }
}

// ----- Live updates -----

const playbook_event_schema = z.object({playbook: playbooks_data.playbook_schema});

function handle_event(event: Record<string, unknown>): void {
    if (!visible) {
        return;
    }
    const parsed = playbook_event_schema.safeParse(event);
    if (!parsed.success) {
        return;
    }
    const changed = parsed.data.playbook;
    if (route.kind === "list") {
        clearTimeout(refresh_timer);
        refresh_timer = setTimeout(load_list, EVENT_REFRESH_DELAY_MS);
        return;
    }
    if (route.kind !== "detail" || detail?.id !== changed.id) {
        return;
    }
    const mine = changed.latest_revision.author_id === current_user.user_id;
    if (editing) {
        // Another member saved while this person was typing. Say so now,
        // before the save is refused.
        if (changed.latest_revision.number > base_revision && !mine) {
            conflict_revision = changed.latest_revision.number;
            render_banners();
        }
        return;
    }
    clearTimeout(refresh_timer);
    refresh_timer = setTimeout(() => {
        if (route.kind === "detail" && detail?.id === changed.id) {
            load_detail(changed.id, {quiet: true});
        }
    }, EVENT_REFRESH_DELAY_MS);
}

// ----- Events from the page -----

// jQuery types `currentTarget` of a delegated handler as `any`.
function target_of(event: {currentTarget: unknown}): HTMLElement {
    assert(event.currentTarget instanceof HTMLElement);
    return event.currentTarget;
}

function field_value(event: {currentTarget: unknown}): string {
    const element = target_of(event);
    assert(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement);
    return element.value;
}

function on_view_events(): void {
    const view = "#playbooks-view";

    $("body").on("click", `${view} .playbooks-new`, () => {
        go_to(
            route.kind === "list" && route.tab === "mine"
                ? "#playbooks/new/mine"
                : "#playbooks/new",
        );
    });
    $("body").on("click", `${view} .playbooks-tab`, (event) => {
        const tab = target_of(event).dataset["tab"] === "mine" ? "mine" : "shared";
        go_to(playbooks_data.route_hash({kind: "list", tab}));
    });
    $("body").on("click", `${view} .playbooks-archived-toggle`, () => {
        show_archived = !show_archived;
        list_state = "loading";
        $view().find(".playbooks-archived-toggle").attr("aria-pressed", String(show_archived));
        visit += 1;
        render_list_region();
        load_list();
    });
    $("body").on("input", `${view} .playbooks-search-input`, (event) => {
        search = field_value(event);
        clearTimeout(search_timer);
        search_timer = setTimeout(() => {
            visit += 1;
            list_state = "loading";
            load_list();
        }, SEARCH_DELAY_MS);
    });
    $("body").on("click", `${view} .playbooks-retry`, () => {
        visit += 1;
        list_state = "loading";
        render_list_region();
        load_list();
    });

    $("body").on("click", `${view} .playbook-retry`, () => {
        if (route.kind !== "detail") {
            return;
        }
        visit += 1;
        if (route.panel === "history" && detail_state === "ready") {
            load_revision();
            render_detail();
        } else {
            load_detail(route.id, {quiet: false});
            render_detail();
        }
    });
    $("body").on("click", `${view} .playbook-action-edit`, () => {
        if (route.kind !== "detail") {
            return;
        }
        const id = route.id;
        if (route.panel === "edit") {
            leave_editor_then(() => {
                go_to(playbooks_data.detail_hash(id));
            });
        } else {
            go_to(playbooks_data.detail_hash(id, "edit"));
        }
    });
    $("body").on("click", `${view} .playbook-action-history`, () => {
        if (route.kind !== "detail") {
            return;
        }
        const id = route.id;
        leave_editor_then(() => {
            go_to(
                route.kind === "detail" && route.panel === "history"
                    ? playbooks_data.detail_hash(id)
                    : playbooks_data.detail_hash(id, "history"),
            );
        });
    });
    $("body").on("click", `${view} .playbook-action-archive`, () => {
        leave_editor_then(archive_or_restore);
    });

    $("body").on("input", `${view} .playbook-title-input`, (event) => {
        draft = {...draft, title: field_value(event)};
        update_save_state();
    });
    $("body").on("input", `${view} .playbook-content-input`, (event) => {
        draft = {...draft, content: field_value(event)};
        update_save_state();
    });
    $("body").on("input", `${view} .playbook-summary-input`, (event) => {
        draft = {...draft, summary: field_value(event)};
    });
    $("body").on("change", `${view} input[name="playbook-scope"]`, (event) => {
        new_scope = field_value(event) === "personal" ? "personal" : "shared";
    });
    $("body").on("click", `${view} .playbook-mode`, (event) => {
        if (target_of(event).dataset["mode"] === "preview") {
            show_preview();
        } else {
            show_write();
        }
    });
    $("body").on("submit", `${view} .playbook-editor`, (event) => {
        event.preventDefault();
        save();
    });
    $("body").on("click", `${view} .playbook-cancel`, () => {
        leave_editor_then(leave_editor_for_view);
    });
    $("body").on("click", `${view} .playbook-reload`, reload_after_conflict);
    $("body").on("keydown", `${view} .playbook-editor`, (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            leave_editor_then(leave_editor_for_view);
        } else if (event.key === "s" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            event.stopPropagation();
            save();
        }
    });

    // A link that leaves the editor asks first when text would be lost.
    document.addEventListener(
        "click",
        (event) => {
            if (!visible || !is_dirty() || saving || !(event.target instanceof Element)) {
                return;
            }
            const href = event.target.closest("a[href]")?.getAttribute("href");
            if (!href?.startsWith("#")) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            leave_editor_then(() => {
                go_to(href);
            });
        },
        true,
    );
    window.addEventListener("beforeunload", (event) => {
        if (visible && is_dirty() && !saving) {
            event.preventDefault();
        }
    });
}

// ----- The four exports every center view keeps -----

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    on_view_events();
    live_updates.on("playbook", handle_event);
}

export function show(args: string[]): void {
    views_util.show({
        highlight_view_in_left_sidebar() {
            views_util.handle_message_view_deactivated(() => {
                left_sidebar_navigation_area.select_top_left_corner_item("");
            });
        },
        $view: $view(),
        update_compose: compose_closed_ui.update_buttons,
        is_visible: () => visible,
        set_visible,
        complete_rerender() {
            // Every address draws its own screen below.
        },
    });
    // This view has no message feed to reply to, so it hides the
    // closed compose bar. An open compose box stays on screen.
    $("#compose").addClass("hide-closed-compose");
    apply_route(args);
}

export function hide(): void {
    if (!visible) {
        return;
    }
    keep_unsaved_text();
    editing = false;
    visit += 1;
    clearTimeout(search_timer);
    clearTimeout(refresh_timer);
    views_util.hide({$view: $view(), set_visible});
    $("#compose").removeClass("hide-closed-compose");
}

export function title(): string {
    return $t({defaultMessage: "Playbooks"});
}
