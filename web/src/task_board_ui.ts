import $ from "jquery";
import _ from "lodash";
import assert from "minimalistic-assert";
import type * as tippy from "tippy.js";
import * as z from "zod/mini";

import render_task_board from "../templates/task_board.hbs";
import render_task_board_card_detail from "../templates/task_board_card_detail.hbs";
import render_task_board_my_tasks from "../templates/task_board_my_tasks.hbs";
import render_task_board_new_card_form from "../templates/task_board_new_card_form.hbs";
import render_task_board_rename_form from "../templates/task_board_rename_form.hbs";

import type {Typeahead} from "./bootstrap_typeahead.ts";
import * as channel from "./channel.ts";
import * as chat_drawer from "./chat_drawer.ts";
import * as compose_closed_ui from "./compose_closed_ui.ts";
import * as composebox_typeahead from "./composebox_typeahead.ts";
import * as confirm_dialog from "./confirm_dialog.ts";
import * as dialog_widget from "./dialog_widget.ts";
import * as dropdown_widget from "./dropdown_widget.ts";
import * as hash_util from "./hash_util.ts";
import {$t, $t_html} from "./i18n.ts";
import * as left_sidebar_navigation_area from "./left_sidebar_navigation_area.ts";
import * as people from "./people.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import {current_user} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import * as sub_store from "./sub_store.ts";
import * as task_board_data from "./task_board_data.ts";
import type {MoveEdge, Task, TaskHistoryEntry} from "./task_board_data.ts";
import * as task_board_drag from "./task_board_drag.ts";
import {history_text} from "./task_board_history.ts";
import * as task_board_notes from "./task_board_notes.ts";
import type {TaskNote} from "./task_board_notes.ts";
import {can_edit_task, can_move_task} from "./task_board_permissions.ts";
import * as timerender from "./timerender.ts";
import * as ui_report from "./ui_report.ts";
import type {UserPillData} from "./user_pill.ts";
import * as util from "./util.ts";
import * as views_util from "./views_util.ts";

let hide_other_views_callback: (() => void) | undefined;
let open_task_id: number | undefined;
// The card a `#tasks/<id>` link names, opened once the board has loaded.
let linked_task_id: number | undefined;
let card_unavailable = false;
// The linked card's panel is scrolled to its notes once the notes and
// the history are in place. Each of them draws the panel again, and a
// panel drawn before that is too short to reach the notes.
let scroll_to_notes_for_task_id: number | undefined;
// The day the plan was last requested for. A new day asks again, and a
// failed request is not repeated on every redraw.
let plan_requested_for: string | undefined;
// Where the keyboard focus goes after the plan redraws for a change that
// the person made with the keyboard.
let pending_plan_focus: {task_id: number; direction: "up" | "down" | undefined} | undefined;
let carry_over_dismissed_for: string | undefined;
let plan_dragged_task_id: number | undefined;
const history_by_task_id = new Map<number, TaskHistoryEntry[]>();
// Notes are fetched when a card is opened, then kept current from
// `task_note` events.
const notes_by_task_id = new Map<number, TaskNote[]>();
let note_is_sending = false;

const NOTE_INPUT_ID = "task-board-note-input";
// The typeahead belongs to one note box. A panel drawn again keeps the
// same box, so the typeahead is made only when the box is a new one.
let note_typeahead: Typeahead<UserPillData> | undefined;
let note_typeahead_element: HTMLElement | undefined;

const ASSIGNEE_BUTTON_ID = "task-board-assignee_widget";
let assignee_picker: dropdown_widget.DropdownWidget | undefined;
let assignee_picker_instance: tippy.Instance | undefined;

const PRIORITY_SELECT_ID = "task-board-priority";
const DUE_INPUT_ID = "task-board-due-input";
const LABEL_INPUT_ID = "task-board-label-input";
let label_error: {task_id: number; message: string} | undefined;

const STATUS_COLUMN_ID = "task-board-status-column";
const STATUS_POSITION_ID = "task-board-status-position";
const STATUS_SAVE_ID = "task-board-status-save";

// What the reader has chosen in the Status row and not saved yet. It
// is tied to the column the card was in, so a card moved by someone
// else shows its new status instead of a stale choice.
type StatusDraft = {
    task_id: number;
    base_column_id: number;
    column_id: number;
    edge: MoveEdge;
};
let status_draft: StatusDraft | undefined;
let status_error: {task_id: number; message: string} | undefined;
let status_is_saving = false;

export const is_visible = task_board_data.is_visible;
const set_visible = task_board_data.set_visible;

// The title center_views.ts shows in the browser tab.
export function title(): string {
    return task_board_data.get_view() === "list"
        ? $t({defaultMessage: "My tasks"})
        : $t({defaultMessage: "Task board"});
}

function day_label(timestamp: number): string {
    return timerender.get_localized_date_or_time_for_format(
        new Date(timestamp * 1000),
        "dayofyear",
    );
}

function time_label(timestamp: number): string {
    return timerender.get_localized_date_or_time_for_format(new Date(timestamp * 1000), "time");
}

function origin_label(task: Task): string {
    if (task.stream_id === null) {
        return "";
    }
    const stream_name = sub_store.maybe_get_stream_name(task.stream_id);
    if (stream_name === undefined) {
        return "";
    }
    return task.topic === "" ? stream_name : `${stream_name} › ${task.topic}`;
}

function is_overdue(task: Task): boolean {
    return task.due_at !== null && task.due_at * 1000 < Date.now() && task.completed_at === null;
}

function due_label(task: Task): string {
    if (task.due_at === null) {
        return "";
    }
    const due_date = new Date(task.due_at * 1000);
    const today = new Date();
    if (due_date.toDateString() === today.toDateString()) {
        return $t({defaultMessage: "Today"});
    }
    return day_label(task.due_at);
}

function priority_name(priority: number): string {
    switch (priority) {
        case task_board_data.PRIORITIES.LOW:
            return "low";
        case task_board_data.PRIORITIES.MEDIUM:
            return "medium";
        case task_board_data.PRIORITIES.HIGH:
            return "high";
        default:
            return "";
    }
}

// The chip on a card says what the number means; a card without a
// priority shows no chip.
function priority_chip_label(priority: number): string {
    switch (priority) {
        case task_board_data.PRIORITIES.LOW:
            return $t({defaultMessage: "Low priority"});
        case task_board_data.PRIORITIES.MEDIUM:
            return $t({defaultMessage: "Medium priority"});
        case task_board_data.PRIORITIES.HIGH:
            return $t({defaultMessage: "High priority"});
        default:
            return "";
    }
}

function priority_options(selected: number): {value: number; label: string; selected: boolean}[] {
    return [
        {value: task_board_data.PRIORITIES.NONE, label: $t({defaultMessage: "No priority"})},
        {value: task_board_data.PRIORITIES.LOW, label: $t({defaultMessage: "Low"})},
        {value: task_board_data.PRIORITIES.MEDIUM, label: $t({defaultMessage: "Medium"})},
        {value: task_board_data.PRIORITIES.HIGH, label: $t({defaultMessage: "High"})},
    ].map((option) => ({...option, selected: option.value === selected}));
}

function agent_label(task: Task): string {
    // The board shows only that an agent runs this card. Whether that agent
    // is ready to run is checked elsewhere and is not repeated here.
    return task.agent_profile_id === null ? "" : $t({defaultMessage: "Agent"});
}

function card_context(task: Task): Record<string, unknown> {
    const checklist_total = task.checklist.length;
    const checklist_done = task.checklist.filter((item) => item.done).length;
    const assignee =
        task.assignee_id === null ? undefined : people.maybe_get_user_by_id(task.assignee_id, true);

    return {
        id: task.id,
        display_id: task.display_id,
        title: task.title,
        blocked: task.blocked,
        can_move: can_move_task(task),
        move_hint: move_hint(),
        labels: task.labels,
        priority_name: priority_name(task.priority),
        priority_label: priority_chip_label(task.priority),
        origin_label: origin_label(task),
        agent_label: agent_label(task),
        checklist_total,
        checklist_done,
        checklist_percent:
            checklist_total === 0 ? 0 : Math.round((checklist_done / checklist_total) * 100),
        checklist_label:
            checklist_total === 0
                ? ""
                : $t(
                      {defaultMessage: "{done} of {total} done"},
                      {done: checklist_done, total: checklist_total},
                  ),
        due_label: due_label(task),
        due_is_overdue: is_overdue(task),
        has_notes: task.note_count > 0,
        note_count: task.note_count,
        note_count_label: $t(
            {defaultMessage: "{count, plural, one {# note} other {# notes}}"},
            {count: task.note_count},
        ),
        completed_label: task.completed_at === null ? "" : day_label(task.completed_at),
        assignee:
            assignee === undefined
                ? undefined
                : {
                      full_name: assignee.full_name,
                      avatar_url: people.small_avatar_url_for_user_id(assignee.user_id),
                  },
    };
}

function board_summary(): string {
    const stream_names = task_board_data
        .origin_stream_ids()
        .map((stream_id) => sub_store.maybe_get_stream_name(stream_id))
        .filter((name) => name !== undefined);

    const count = task_board_data.total_task_count();
    const task_count_label = $t({defaultMessage: "{count} tasks"}, {count});
    if (stream_names.length === 0) {
        return task_count_label;
    }
    return `${task_count_label} · ${stream_names.map((name) => `#${name}`).join(", ")}`;
}

// Enough faces to show who is on the board; the rest are counted.
const MAX_HEADER_MEMBERS = 3;

function filter_options(): {value: string; label: string; selected: boolean}[] {
    const current = task_board_data.get_filter();
    return [
        {value: task_board_data.FILTERS.ALL, label: $t({defaultMessage: "All cards"})},
        {value: task_board_data.FILTERS.MINE, label: $t({defaultMessage: "My cards"})},
        {
            value: task_board_data.FILTERS.REVIEW,
            label: $t({defaultMessage: "Awaiting my review"}),
        },
        {value: task_board_data.FILTERS.BLOCKED, label: $t({defaultMessage: "Blocked cards"})},
        {
            value: task_board_data.FILTERS.HIGH_PRIORITY,
            label: $t({defaultMessage: "High priority"}),
        },
    ].map((option) => ({...option, selected: option.value === current}));
}

function due_section_title(section: task_board_data.DueSection): string {
    switch (section) {
        case "overdue":
            return $t({defaultMessage: "Overdue"});
        case "today":
            return $t({defaultMessage: "Today"});
        case "tomorrow":
            return $t({defaultMessage: "Tomorrow"});
        case "next_7_days":
            return $t({defaultMessage: "Next 7 days"});
        case "later":
            return $t({defaultMessage: "Later"});
        default:
            return $t({defaultMessage: "No due date"});
    }
}

function plan_date(now_ms: number): string {
    return task_board_data.local_date(now_ms, timerender.display_time_zone);
}

function fetch_plan(): void {
    const today = plan_date(Date.now());
    plan_requested_for = today;
    for (const date of [today, task_board_data.shift_date(today, -1)]) {
        void channel.get({
            url: "/json/tasks/plan",
            data: {date},
            success(raw_data) {
                task_board_data.set_plan(
                    date,
                    task_board_data.task_plan_response_schema.parse(raw_data).plan,
                );
                complete_rerender();
            },
            error(xhr) {
                ui_report.error(
                    $t_html({defaultMessage: "Could not load your plan"}),
                    xhr,
                    $("#home-error"),
                );
            },
        });
    }
}

// The page can stay open past midnight, and then today has no plan yet.
function ensure_plan_for(today: string): void {
    if (plan_requested_for !== today) {
        fetch_plan();
    }
}

// The list is redrawn from the card data on every card event, so a
// card whose deadline, owner or column changed lands in its new section.
function render_my_tasks(): void {
    const now_ms = Date.now();
    const today = plan_date(now_ms);
    ensure_plan_for(today);
    const open_tasks = task_board_data.open_tasks_assigned_to(people.my_current_user_id());
    const today_plan = task_board_data.todays_plan(
        people.my_current_user_id(),
        now_ms,
        timerender.display_time_zone,
    );
    const carry_over = carry_over_dismissed_for === today ? [] : today_plan.carry_over;
    const sections = task_board_data
        .group_by_due(open_tasks, now_ms, timerender.display_time_zone)
        .filter((group) => group.tasks.length > 0)
        .map((group) => ({
            section: group.section,
            title: due_section_title(group.section),
            is_overdue: group.section === "overdue",
            card_count: group.tasks.length,
            cards: group.tasks.map((task) => ({
                ...card_context(task),
                is_planned_today: task_board_data.is_planned(task.id, today),
            })),
        }));
    $("#task-board-pane").html(
        render_task_board_my_tasks({
            is_list: true,
            summary: $t({defaultMessage: "{count} open tasks"}, {count: open_tasks.length}),
            is_empty: open_tasks.length === 0,
            sections,
            card_unavailable,
            has_plan: task_board_data.has_plan_for(today),
            plan_count: today_plan.planned.length + today_plan.auto.length,
            planned: today_plan.planned.map((task, index) => ({
                ...card_context(task),
                is_first: index === 0,
                is_last: index === today_plan.planned.length - 1,
            })),
            auto: today_plan.auto.map((task) => ({...card_context(task), is_auto: true})),
            carry_over: carry_over.map((task) => card_context(task)),
            has_carry_over: carry_over.length > 0,
            has_planned: today_plan.planned.length > 0,
            has_auto: today_plan.auto.length > 0,
            is_empty_plan:
                today_plan.planned.length === 0 &&
                today_plan.auto.length === 0 &&
                carry_over.length === 0,
        }),
    );
    if (open_task_id !== undefined) {
        render_card_detail(open_task_id);
    }
}

export function complete_rerender(): void {
    if (!is_visible()) {
        return;
    }
    const board = task_board_data.get_board();
    if (board === undefined) {
        return;
    }
    if (task_board_data.get_view() === "list") {
        render_my_tasks();
        return;
    }

    const columns = task_board_data.get_columns().map((column) => {
        const folded_count = task_board_data.folded_count_in_column(column.id);
        return {
            id: column.id,
            name: column.name,
            work_limit: column.work_limit,
            has_work_limit: column.work_limit !== null && column.work_limit > 0,
            work_limit_tooltip: $t({
                defaultMessage: "Cards in this column / the most cards the team works on at once",
            }),
            has_done_window: column.done_window_days !== null,
            done_window_tooltip:
                column.done_window_days === null
                    ? ""
                    : $t(
                          {
                              defaultMessage:
                                  "Cards finished more than {days} days ago are folded away",
                          },
                          {days: column.done_window_days},
                      ),
            done_window_label:
                column.done_window_days === null
                    ? ""
                    : $t({defaultMessage: "{days} days"}, {days: column.done_window_days}),
            card_count: task_board_data.tasks_in_column(column.id).length,
            at_work_limit: task_board_data.column_is_over_work_limit(column.id),
            has_folded: folded_count > 0,
            folded_label: $t({defaultMessage: "Show {count} older cards"}, {count: folded_count}),
            cards: task_board_data
                .visible_tasks_in_column(column.id, people.my_current_user_id())
                .map((task) => card_context(task)),
        };
    });

    const member_ids = task_board_data.assignee_ids();
    const members = member_ids
        .slice(0, MAX_HEADER_MEMBERS)
        .map((user_id) => people.maybe_get_user_by_id(user_id, true))
        .filter((person) => person !== undefined)
        .map((person) => ({
            full_name: person.full_name,
            avatar_url: people.small_avatar_url_for_user_id(person.user_id),
        }));

    $("#task-board-pane").html(
        render_task_board({
            is_list: false,
            board_name: board.name,
            summary: board_summary(),
            columns,
            filters: filter_options(),
            has_members: members.length > 0,
            members,
            has_extra_members: member_ids.length > MAX_HEADER_MEMBERS,
            extra_member_count: member_ids.length - MAX_HEADER_MEMBERS,
            is_empty: task_board_data.total_task_count() === 0,
            move_hint: move_hint(),
            card_unavailable,
        }),
    );

    if (open_task_id !== undefined) {
        render_card_detail(open_task_id);
    }
}

function assignee_context(task: Task): {full_name: string; avatar_url: string} | undefined {
    if (task.assignee_id === null) {
        return undefined;
    }
    const person = people.maybe_get_user_by_id(task.assignee_id, true);
    if (person === undefined) {
        return {full_name: $t({defaultMessage: "A former member"}), avatar_url: ""};
    }
    return {
        full_name: person.full_name,
        avatar_url: people.small_avatar_url_for_user_id(person.user_id),
    };
}

function assignee_options(
    current_assignee_id: string | number | undefined,
): dropdown_widget.Option[] {
    return people
        .get_realm_active_human_users()
        .toSorted(people.compare_by_name)
        .map((person) => ({
            unique_id: person.user_id,
            name: person.full_name,
            avatar_url: people.small_avatar_url_for_user_id(person.user_id),
            bold_current_selection: person.user_id === current_assignee_id,
        }));
}

function change_assignee(task_id: number, assignee_id: number | null): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined || task.assignee_id === assignee_id) {
        return;
    }
    const previous_assignee_id = task.assignee_id;

    // Show the choice at once; the card event that follows the save
    // replaces it with what the server stored.
    task_board_data.add_or_update_task({...task, assignee_id});
    complete_rerender();

    patch_task(task_id, assignee_id === null ? {clear_assignee: true} : {assignee_id}, {
        success() {
            if (open_task_id === task_id) {
                fetch_history(task_id);
            }
        },
        error() {
            const current = task_board_data.get_task(task_id);
            if (current !== undefined) {
                task_board_data.add_or_update_task({...current, assignee_id: previous_assignee_id});
                complete_rerender();
            }
        },
    });
}

// Shows the choice at once; the card event that follows the save
// replaces it with what the server stored, and a refused save puts the
// old values back.
function update_card_fields(
    task_id: number,
    changes: Partial<Task>,
    request: Record<string, string | number | boolean>,
): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined) {
        return;
    }
    const previous = _.pick(task, Object.keys(changes));
    task_board_data.add_or_update_task({...task, ...changes});
    complete_rerender();

    patch_task(task_id, request, {
        success() {
            if (open_task_id === task_id) {
                fetch_history(task_id);
            }
        },
        error() {
            const current = task_board_data.get_task(task_id);
            if (current !== undefined) {
                task_board_data.add_or_update_task({...current, ...previous});
                complete_rerender();
            }
        },
    });
}

function change_priority(task_id: number, priority: number): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined || task.priority === priority) {
        return;
    }
    update_card_fields(task_id, {priority}, {priority});
}

function change_labels(task_id: number, labels: string[]): void {
    update_card_fields(task_id, {labels}, {labels: JSON.stringify(labels)});
}

function change_due_date(task_id: number, date: string): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined) {
        return;
    }
    if (date === "") {
        if (task.due_at !== null) {
            update_card_fields(task_id, {due_at: null}, {clear_due_at: true});
        }
        return;
    }
    const due_at = task_board_data.end_of_day_timestamp(date, timerender.display_time_zone);
    if (due_at !== task.due_at) {
        update_card_fields(task_id, {due_at}, {due_at});
    }
}

function add_label_from_input(task_id: number): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined) {
        return;
    }
    const $input = $<HTMLInputElement>(`#${LABEL_INPUT_ID}`);
    const edit = task_board_data.add_label(task.labels, $input.val() ?? "");
    if (edit.error !== undefined) {
        label_error = {
            task_id,
            message:
                edit.error === "too_long"
                    ? $t(
                          {defaultMessage: "A label can have up to {count} characters."},
                          {count: task_board_data.MAX_LABEL_LENGTH},
                      )
                    : $t(
                          {defaultMessage: "A card can have up to {count} labels."},
                          {count: task_board_data.MAX_LABELS},
                      ),
        };
        $("#task-board-detail .task-board-label-error").text(label_error.message);
        return;
    }
    label_error = undefined;
    $("#task-board-detail .task-board-label-error").text("");
    $input.val("");
    if (edit.labels !== task.labels) {
        change_labels(task_id, edit.labels);
    }
}

function setup_assignee_picker(): void {
    assignee_picker = new dropdown_widget.DropdownWidget({
        widget_name: "task-board-assignee",
        get_options: assignee_options,
        $events_container: $("#task-board-detail"),
        unique_id_type: "number",
        hide_search_box_focus_first_item_on_keyboard_open: true,
        sticky_bottom_option: $t({defaultMessage: "Nobody"}),
        search_placeholder_text: $t({defaultMessage: "Find a member"}),
        on_show_callback(instance) {
            assignee_picker_instance = instance;
        },
        on_hidden_callback() {
            assignee_picker_instance = undefined;
        },
        item_click_callback(_event, instance, widget, is_nobody_clicked) {
            assignee_picker_instance = undefined;
            instance.destroy();
            $(`#${ASSIGNEE_BUTTON_ID}`).trigger("focus");
            if (open_task_id === undefined) {
                return;
            }
            const picked_id = widget.value();
            change_assignee(
                open_task_id,
                is_nobody_clicked || typeof picked_id !== "number" ? null : picked_id,
            );
        },
    });
    assignee_picker.setup();
}

function note_contexts(task_id: number): Record<string, unknown>[] {
    return (notes_by_task_id.get(task_id) ?? []).map((note) => {
        const author = people.maybe_get_user_by_id(note.author_id, true);
        return {
            id: note.id,
            author_name: author?.full_name ?? $t({defaultMessage: "A former member"}),
            avatar_url:
                author === undefined ? "" : people.small_avatar_url_for_user_id(author.user_id),
            time_label: `${day_label(note.date_created)} · ${time_label(note.date_created)}`,
            rendered_content: note.rendered_content,
            can_delete: task_board_notes.can_delete_note(note, current_user),
        };
    });
}

// The server renders a note with the Markdown processor of messages;
// this gives its mentions and links the same behavior. A message row
// is what marks a mention of the reader in a message, and a note has
// none, so the mark is added here.
function process_rendered_notes(): void {
    $("#task-board-detail .task-board-note-content").each(function () {
        const $content = $(this);
        rendered_markdown.update_elements($content);
        $content.find(".user-mention[data-user-id]").each(function () {
            if (people.is_my_user_id(Number($(this).attr("data-user-id")))) {
                $(this).addClass("user-mention-me");
            }
        });
    });
}

function setup_note_typeahead(): void {
    const $input = $<HTMLTextAreaElement>(`#${NOTE_INPUT_ID}`);
    const input = $input[0];
    if (input === undefined || input === note_typeahead_element) {
        return;
    }
    note_typeahead?.unlisten();
    note_typeahead = composebox_typeahead.initialize_task_note_typeahead($input);
    note_typeahead_element = input;
}

function update_note_submit_button(): void {
    const content = $<HTMLTextAreaElement>(`#${NOTE_INPUT_ID}`).val() ?? "";
    $("#task-board-detail .task-board-note-submit").prop(
        "disabled",
        note_is_sending || content.trim() === "",
    );
}

function current_status_draft(task: Task): StatusDraft {
    if (status_draft?.task_id === task.id && status_draft.base_column_id === task.column_id) {
        return status_draft;
    }
    status_draft = undefined;
    return {
        task_id: task.id,
        base_column_id: task.column_id,
        column_id: task.column_id,
        edge: "top",
    };
}

function status_save_is_disabled(task: Task, draft: StatusDraft): boolean {
    return (
        status_is_saving || task_board_data.move_to_edge_is_noop(task, draft.column_id, draft.edge)
    );
}

function status_error_message(task: Task): string {
    if (status_error?.task_id === task.id) {
        return status_error.message;
    }
    return "";
}

function move_hint(): string {
    return $t({
        defaultMessage: "Guests can only move cards they created, are responsible for, or review.",
    });
}

function render_card_detail(task_id: number): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined) {
        close_card_detail();
        return;
    }

    // The panel is drawn again on every card event. Close an open
    // picker, whose button is about to be replaced, and keep the
    // keyboard focus where the reader left it.
    const focused_id = document.activeElement?.id;
    const keep_focus_on_assignee_button = focused_id === ASSIGNEE_BUTTON_ID;
    const planning_focus_id = [PRIORITY_SELECT_ID, LABEL_INPUT_ID, DUE_INPUT_ID].find(
        (id) => id === focused_id,
    );
    const label_draft = $<HTMLInputElement>(`#${LABEL_INPUT_ID}`).val() ?? "";
    const status_focus_id = [STATUS_COLUMN_ID, STATUS_POSITION_ID, STATUS_SAVE_ID].find(
        (id) => id === focused_id,
    );
    const composer_focus =
        document.activeElement instanceof HTMLElement &&
        $("#task-board-detail .task-board-note-composer").has(document.activeElement).length > 0
            ? document.activeElement
            : undefined;
    const panel_scroll_top = $("#task-board-detail").scrollTop() ?? 0;
    // The note box carries a draft, a message, and a caret, so the same
    // element goes back into the new panel.
    const $kept_composer = $(
        `#task-board-detail .task-board-note-composer[data-task-id="${task_id}"]`,
    ).detach();
    assignee_picker_instance?.destroy();
    assignee_picker_instance = undefined;

    const column = task_board_data.get_column(task.column_id);
    const history = history_by_task_id.get(task_id) ?? [];
    const status = current_status_draft(task);

    $("#task-board-detail").html(
        render_task_board_card_detail({
            display_id: task.display_id,
            column_name: column?.name ?? "",
            agent_label: agent_label(task),
            title: task.title,
            body: task.body,
            origin_label: origin_label(task),
            origin_url:
                task.stream_id === null
                    ? ""
                    : hash_util.by_stream_topic_url(task.stream_id, task.topic),
            has_channel: task.stream_id !== null,
            created_label: $t(
                {defaultMessage: "Created {date}"},
                {date: day_label(task.date_created)},
            ),
            has_checklist: task.checklist.length > 0,
            checklist: task.checklist.map((item, index) => ({...item, index})),
            can_edit: can_edit_task(task),
            can_move: can_move_task(task),
            status_columns: task_board_data.get_columns().map((status_column) => ({
                id: status_column.id,
                name: status_column.name,
                selected: status_column.id === status.column_id,
            })),
            status_edge_is_top: status.edge === "top",
            status_save_disabled: status_save_is_disabled(task, status),
            status_error: status_error_message(task),
            move_hint: move_hint(),
            task_id,
            note_count: notes_by_task_id.get(task_id)?.length ?? task.note_count,
            notes_loaded: notes_by_task_id.has(task_id),
            has_notes: (notes_by_task_id.get(task_id)?.length ?? 0) > 0,
            notes: note_contexts(task_id),
            assignee: assignee_context(task),
            priorities: priority_options(task.priority),
            priority_current_label:
                priority_chip_label(task.priority) || $t({defaultMessage: "No priority"}),
            labels: task.labels,
            label_error: label_error?.task_id === task_id ? label_error.message : "",
            due_label: due_label(task),
            due_input_value: task_board_data.due_date_input_value(
                task.due_at,
                timerender.display_time_zone,
            ),
            due_is_overdue: is_overdue(task),
            plan_is_ready: task_board_data.has_plan_for(plan_date(Date.now())),
            is_planned_today: task_board_data.is_planned(task.id, plan_date(Date.now())),
            agent_job_url: task.agent_job_id === null ? "" : "#agent-jobs",
            history: history.map((entry) => ({
                time_label: time_label(entry.event_time),
                text: history_text(entry),
            })),
        }),
    );
    $("#task-board-detail").removeClass("hidden");
    if ($kept_composer.length > 0) {
        $("#task-board-detail .task-board-note-composer").replaceWith($kept_composer);
    }
    $(`#${LABEL_INPUT_ID}`).val(label_draft);
    process_rendered_notes();
    setup_note_typeahead();
    update_note_submit_button();
    $("#task-board-detail").scrollTop(panel_scroll_top);

    if (assignee_picker !== undefined) {
        const picker = assignee_picker;
        picker.current_value = task.assignee_id ?? undefined;
        // setup() binds these on the button, which each render replaces.
        $(`#${ASSIGNEE_BUTTON_ID}`)
            .on("mousedown", () => {
                picker.dropdown_triggered_via_keyboard = false;
            })
            .on("keydown", () => {
                picker.dropdown_triggered_via_keyboard = true;
            });
    }

    if (keep_focus_on_assignee_button && $(`#${ASSIGNEE_BUTTON_ID}`).length > 0) {
        $(`#${ASSIGNEE_BUTTON_ID}`).trigger("focus");
    } else if (planning_focus_id !== undefined && $(`#${planning_focus_id}`).length > 0) {
        $(`#${planning_focus_id}`).trigger("focus");
    } else if (status_focus_id !== undefined && $(`#${status_focus_id}:enabled`).length > 0) {
        $(`#${status_focus_id}`).trigger("focus");
    } else if (composer_focus !== undefined) {
        // A button that just turned off, such as Add note after a post,
        // hands the focus back to the box.
        const $target = $(composer_focus);
        ($target.is(":disabled") ? $(`#${NOTE_INPUT_ID}`) : $target).trigger("focus");
    } else {
        $("#task-board-detail .task-board-detail-title").trigger("focus");
    }
}

function scroll_linked_card_to_notes(task_id: number): void {
    if (
        scroll_to_notes_for_task_id !== task_id ||
        !notes_by_task_id.has(task_id) ||
        !history_by_task_id.has(task_id)
    ) {
        return;
    }
    scroll_to_notes_for_task_id = undefined;
    if (open_task_id === task_id) {
        document.querySelector("#task-board-notes")?.scrollIntoView({block: "start"});
    }
}

function fetch_history(task_id: number): void {
    void channel.get({
        url: `/json/tasks/${task_id}/history`,
        success(raw_data) {
            const data = task_board_data.task_history_response_schema.parse(raw_data);
            history_by_task_id.set(task_id, data.history);
            if (open_task_id === task_id) {
                render_card_detail(task_id);
            }
            scroll_linked_card_to_notes(task_id);
        },
    });
}

function fetch_notes(task_id: number): void {
    void channel.get({
        url: `/json/tasks/${task_id}/notes`,
        success(raw_data) {
            const data = task_board_notes.task_notes_response_schema.parse(raw_data);
            notes_by_task_id.set(task_id, data.notes);
            if (open_task_id === task_id) {
                render_card_detail(task_id);
            }
            scroll_linked_card_to_notes(task_id);
        },
        error() {
            scroll_to_notes_for_task_id = undefined;
        },
    });
}

// Opening a card is how a person sees the notes and the assignment that
// notified them, so the server clears those notifications. A failure
// leaves them in the Needs you list, which is harmless.
function resolve_card_notifications(task_id: number): void {
    void channel.post({url: `/json/tasks/${task_id}/notifications/resolve`});
}

function open_card_detail(task_id: number): void {
    if (open_task_id !== task_id) {
        status_draft = undefined;
        status_error = undefined;
    }
    open_task_id = task_id;
    card_unavailable = false;
    render_card_detail(task_id);
    fetch_history(task_id);
    fetch_notes(task_id);
    resolve_card_notifications(task_id);
}

// A link can name a card that was deleted, or one this person cannot
// see. The board is then shown with a short message.
function open_linked_card(): void {
    const task_id = linked_task_id;
    linked_task_id = undefined;
    if (task_id === undefined) {
        return;
    }
    if (task_board_data.get_task(task_id) === undefined) {
        card_unavailable = true;
        close_card_detail();
        complete_rerender();
        return;
    }
    scroll_to_notes_for_task_id = task_id;
    open_card_detail(task_id);
}

// A card from a channel opens that conversation in the chat drawer. A
// card without a channel has none, so its notes are the place to talk.
function open_card_conversation(task_id: number): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined) {
        return;
    }
    if (task.stream_id === null) {
        document.querySelector("#task-board-notes")?.scrollIntoView({block: "start"});
        $(`#${NOTE_INPUT_ID}`).trigger("focus");
        return;
    }
    chat_drawer.open({stream_id: task.stream_id, topic: task.topic});
}

function close_card_detail(): void {
    open_task_id = undefined;
    note_is_sending = false;
    note_typeahead?.unlisten();
    note_typeahead = undefined;
    note_typeahead_element = undefined;
    $("#task-board-detail").addClass("hidden").empty();
}

function report_error(xhr: JQuery.jqXHR): void {
    ui_report.error($t_html({defaultMessage: "Could not save the change"}), xhr, $("#home-error"));
}

function patch_task(
    task_id: number,
    data: Record<string, string | number | boolean>,
    callbacks: {
        success?: () => void;
        error?: (xhr: JQuery.jqXHR) => void;
        // The caller shows the failure itself, next to what it changed.
        quiet?: boolean;
    } = {},
): void {
    void channel.patch({
        url: `/json/tasks/${task_id}`,
        data,
        success: callbacks.success,
        error(xhr) {
            if (callbacks.quiet !== true) {
                report_error(xhr);
            }
            callbacks.error?.(xhr);
        },
    });
}

function move_task(
    task_id: number,
    column_id: number,
    position: number,
    callbacks: {
        start?: () => void;
        success?: () => void;
        error?: (xhr: JQuery.jqXHR) => void;
        quiet?: boolean;
    } = {},
): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined) {
        return;
    }
    const send = (): void => {
        callbacks.start?.();
        patch_task(task_id, {column_id, position}, callbacks);
    };

    const column = task_board_data.get_column(column_id);
    const crossing_into_a_full_column =
        task.column_id !== column_id && task_board_data.column_is_over_work_limit(column_id);

    if (crossing_into_a_full_column && column !== undefined) {
        confirm_dialog.launch({
            modal_title_text: $t({defaultMessage: "This column is full"}),
            modal_content_html: $t_html(
                {
                    defaultMessage:
                        "{column} already holds as much parallel work as the team agreed to. Move the card anyway?",
                },
                {column: column.name},
            ),
            modal_submit_button_text: $t({defaultMessage: "Move it"}),
            on_click: send,
        });
        return;
    }

    send();
}

function update_status_controls(task: Task): void {
    const draft = current_status_draft(task);
    $(`#${STATUS_SAVE_ID}`).prop("disabled", status_save_is_disabled(task, draft));
    $("#task-board-detail .task-board-status-error").text(status_error_message(task));
}

function read_status_draft(task: Task): StatusDraft {
    return {
        task_id: task.id,
        base_column_id: task.column_id,
        column_id: Number($<HTMLSelectElement>(`#${STATUS_COLUMN_ID}`).val()),
        edge: $<HTMLSelectElement>(`#${STATUS_POSITION_ID}`).val() === "bottom" ? "bottom" : "top",
    };
}

function save_status(task_id: number): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined || status_is_saving) {
        return;
    }
    const draft = current_status_draft(task);
    if (task_board_data.move_to_edge_is_noop(task, draft.column_id, draft.edge)) {
        return;
    }
    const position = task_board_data.position_for_edge(draft.column_id, draft.edge, task_id);
    status_error = undefined;
    const finish = (): void => {
        status_is_saving = false;
        status_draft = undefined;
    };

    move_task(task_id, draft.column_id, position, {
        quiet: true,
        start() {
            status_is_saving = true;
            update_status_controls(task);
        },
        success() {
            finish();
            if (open_task_id === task_id) {
                render_card_detail(task_id);
                fetch_history(task_id);
            }
        },
        error(xhr) {
            finish();
            const parsed = z.object({msg: z.string()}).safeParse(xhr.responseJSON);
            status_error = {
                task_id,
                message:
                    xhr.status >= 400 && xhr.status < 500 && parsed.success
                        ? parsed.data.msg
                        : $t({defaultMessage: "Could not move the card. Try again."}),
            };
            if (open_task_id === task_id) {
                render_card_detail(task_id);
            }
        },
    });
}

// The card being dragged and the spot the last dragover chose for it.
// The drop reads that spot back, so the card lands where the line was.
type DropTarget = {column_id: number; slot: number; visible_task_ids: number[]};
let dragged_task_id: number | undefined;
let drop_target: DropTarget | undefined;

// Edge zone and top speed of the scroll while a card is held near the
// border of something that scrolls.
const AUTO_SCROLL_ZONE_EM = 3;
const AUTO_SCROLL_MAX_STEP = 24;

function root_font_size(): number {
    return Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
}

function cards_other_than_dragged($column_cards: JQuery): HTMLElement[] {
    return [...$column_cards.children(".task-board-card:not(.task-board-card-dragging)")].filter(
        (card): card is HTMLElement => card instanceof HTMLElement,
    );
}

function clear_drop_feedback(): void {
    $("#task-board-view .task-board-column-drop-target").removeClass(
        "task-board-column-drop-target",
    );
    $("#task-board-view .task-board-drop-indicator").remove();
    drop_target = undefined;
}

function show_drop_target($column: JQuery, pointer_y: number): void {
    const column_id = Number($column.attr("data-column-id"));
    const $column_cards = $column.find(".task-board-column-cards");
    const cards = cards_other_than_dragged($column_cards);
    const boxes = cards.map((card) => card.getBoundingClientRect());
    const slot = task_board_drag.drop_slot(boxes, pointer_y);
    drop_target = {
        column_id,
        slot,
        visible_task_ids: cards.map((card) => Number(card.dataset["taskId"])),
    };

    $("#task-board-view .task-board-column-drop-target")
        .not($column)
        .removeClass("task-board-column-drop-target");
    $column.addClass("task-board-column-drop-target");

    const container = $column_cards[0];
    assert(container !== undefined);
    let $indicator = $column_cards.children(".task-board-drop-indicator");
    if ($indicator.length === 0) {
        $("#task-board-view .task-board-drop-indicator").remove();
        $indicator = $("<div>").addClass("task-board-drop-indicator").attr("aria-hidden", "true");
        $column_cards.append($indicator);
    }
    const gap = Number.parseFloat(getComputedStyle(container).rowGap) || 0;
    const container_top = container.getBoundingClientRect().top + container.clientTop;
    $indicator.css("top", task_board_drag.indicator_offset(boxes, slot, container_top, gap));
}

// The nearest ancestor that scrolls vertically. The page itself is not
// one: it scrolls through the window.
function vertical_scroll_parent(element: HTMLElement): HTMLElement | undefined {
    for (
        let node = element.parentElement;
        node !== null && node !== document.body && node !== document.documentElement;
        node = node.parentElement
    ) {
        const overflow_y = getComputedStyle(node).overflowY;
        if (
            (overflow_y === "auto" || overflow_y === "scroll") &&
            node.scrollHeight > node.clientHeight
        ) {
            return node;
        }
    }
    return undefined;
}

function auto_scroll_while_dragging(column: HTMLElement, x: number, y: number): void {
    const zone = AUTO_SCROLL_ZONE_EM * root_font_size();
    // The page smooths its own scrolling, which would lag behind a
    // pointer that keeps moving.
    const instant = "instant";

    const board_columns = column.closest(".task-board-columns");
    if (board_columns instanceof HTMLElement) {
        const rect = board_columns.getBoundingClientRect();
        board_columns.scrollBy({
            left: task_board_drag.scroll_step(x, rect.left, rect.right, zone, AUTO_SCROLL_MAX_STEP),
            behavior: instant,
        });
    }

    const scroll_parent = vertical_scroll_parent(column);
    if (scroll_parent === undefined) {
        // The fixed navbar covers the top of the page, and the board
        // container is padded down by exactly its height.
        const container = column.closest(".task-board-container");
        const navbar_height =
            container === null ? 0 : Number.parseFloat(getComputedStyle(container).paddingTop);
        window.scrollBy({
            top: task_board_drag.scroll_step(
                y,
                navbar_height,
                window.innerHeight,
                zone,
                AUTO_SCROLL_MAX_STEP,
            ),
            behavior: instant,
        });
        return;
    }
    const rect = scroll_parent.getBoundingClientRect();
    scroll_parent.scrollBy({
        top: task_board_drag.scroll_step(y, rect.top, rect.bottom, zone, AUTO_SCROLL_MAX_STEP),
        behavior: instant,
    });
}

// The slot among the planned cards other than the one being dragged.
function plan_drop_slot($list: JQuery, pointer_y: number): number {
    const boxes = $list
        .children(".task-board-plan-item")
        .not(".task-board-plan-item-dragging")
        .toArray()
        .map((item) => {
            const rect = item.getBoundingClientRect();
            return {top: rect.top, height: rect.height};
        });
    return task_board_drag.drop_slot(boxes, pointer_y);
}

function clear_plan_drop_feedback(): void {
    $(".task-board-plan-item").removeClass(
        "task-board-plan-item-dragging task-board-plan-drop-before task-board-plan-drop-after",
    );
}

function show_plan_drop_target($list: JQuery, pointer_y: number): void {
    const slot = plan_drop_slot($list, pointer_y);
    const $others = $list.children(".task-board-plan-item").not(".task-board-plan-item-dragging");
    $others.removeClass("task-board-plan-drop-before task-board-plan-drop-after");
    if (slot < $others.length) {
        $others.eq(slot).addClass("task-board-plan-drop-before");
    } else {
        $others.last().addClass("task-board-plan-drop-after");
    }
}

function selected_id(selector: string): number | undefined {
    const value = $<HTMLSelectElement>(selector).val();
    return value ? Number(value) : undefined;
}

function launch_new_card_dialog(column_id: number): void {
    const column_name = task_board_data.get_column(column_id)?.name ?? "";
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "New task in {column}"}, {column: column_name}),
        modal_content_html: render_task_board_new_card_form({
            columns: task_board_data.get_columns().map((column) => ({
                id: column.id,
                name: column.name,
                selected: column.id === column_id,
            })),
            people: people
                .get_realm_active_human_users()
                .toSorted(people.compare_by_name)
                .map((person) => ({user_id: person.user_id, full_name: person.full_name})),
            priorities: priority_options(task_board_data.PRIORITIES.NONE),
            channels: stream_data
                .subscribed_subs()
                .toSorted((a, b) => util.strcmp(a.name, b.name))
                .map((sub) => ({stream_id: sub.stream_id, name: sub.name})),
        }),
        modal_submit_button_text: $t({defaultMessage: "Create"}),
        id: "task-board-new-card-modal",
        form_id: "task-board-new-card-form",
        focus_submit_on_open: false,
        on_shown() {
            $("#task-board-new-title").trigger("focus");
        },
        on_click() {
            const title = $<HTMLInputElement>("#task-board-new-title").val()?.trim() ?? "";
            if (title === "") {
                return;
            }
            const data: Record<string, string | number> = {
                title,
                body: $<HTMLTextAreaElement>("#task-board-new-body").val() ?? "",
                column_id: selected_id("#task-board-new-column") ?? column_id,
            };
            const assignee_id = selected_id("#task-board-new-assignee");
            if (assignee_id !== undefined) {
                data["assignee_id"] = assignee_id;
            }
            const reviewer_id = selected_id("#task-board-new-reviewer");
            if (reviewer_id !== undefined) {
                data["reviewer_id"] = reviewer_id;
            }
            const stream_id = selected_id("#task-board-new-channel");
            if (stream_id !== undefined) {
                data["stream_id"] = stream_id;
            }
            const priority = Number($<HTMLSelectElement>("#task-board-new-priority").val());
            if (priority !== task_board_data.PRIORITIES.NONE) {
                data["priority"] = priority;
            }
            const due = $<HTMLInputElement>("#task-board-new-due").val() ?? "";
            if (due !== "") {
                // A card is due at the end of the chosen day in the
                // reader's own time zone.
                data["due_at"] = task_board_data.end_of_day_timestamp(
                    due,
                    timerender.display_time_zone,
                );
            }
            void channel.post({
                url: "/json/tasks",
                data,
                error: report_error,
            });
        },
    });
}

function launch_rename_dialog(): void {
    const board = task_board_data.get_board();
    if (board === undefined) {
        return;
    }
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "Rename board"}),
        modal_content_html: render_task_board_rename_form({board_name: board.name}),
        modal_submit_button_text: $t({defaultMessage: "Save"}),
        id: "task-board-rename-modal",
        form_id: "task-board-rename-form",
        focus_submit_on_open: false,
        on_shown() {
            $("#task-board-rename-input").trigger("focus");
        },
        on_click() {
            const name = $<HTMLInputElement>("#task-board-rename-input").val()?.trim() ?? "";
            if (name === "") {
                return;
            }
            void channel.patch({
                url: `/json/task_boards/${board.id}`,
                data: {name},
                error: report_error,
            });
        },
    });
}

function ignored_mentions_notice(user_ids: number[]): string {
    if (user_ids.length === 0) {
        return "";
    }
    const names = user_ids.map(
        (user_id) =>
            people.maybe_get_user_by_id(user_id, true)?.full_name ??
            $t({defaultMessage: "A former member"}),
    );
    return $t(
        {defaultMessage: "{names} can't see this card, so they weren't notified."},
        {names: util.format_array_as_list_with_conjunction(names, "long")},
    );
}

function post_note(task_id: number): void {
    const $input = $<HTMLTextAreaElement>(`#${NOTE_INPUT_ID}`);
    const content = $input.val()?.trim() ?? "";
    if (content === "" || note_is_sending) {
        return;
    }
    const $composer = $input.closest(".task-board-note-composer");
    const $notice = $composer.find(".task-board-note-notice");
    const $error = $composer.find(".task-board-note-error");
    note_is_sending = true;
    $notice.text("");
    $error.text("");
    update_note_submit_button();

    void channel.post({
        url: `/json/tasks/${task_id}/notes`,
        data: {content},
        success(raw_data) {
            note_is_sending = false;
            const data = task_board_notes.add_note_response_schema.parse(raw_data);
            notes_by_task_id.set(
                task_id,
                task_board_notes.add_note(notes_by_task_id.get(task_id) ?? [], data.note),
            );
            $input.val("");
            $notice.text(ignored_mentions_notice(data.ignored_mentions));
            if (open_task_id === task_id) {
                render_card_detail(task_id);
            }
        },
        error(xhr) {
            note_is_sending = false;
            // The draft stays so the person can fix it or try again.
            $error.html(
                channel.xhr_error_message("", xhr) ||
                    _.escape($t({defaultMessage: "Could not add the note. Try again."})),
            );
            update_note_submit_button();
        },
    });
}

function fetch_board(): void {
    void channel.get({
        url: "/json/tasks",
        success(raw_data) {
            task_board_data.set_board(task_board_data.task_board_response_schema.parse(raw_data));
            complete_rerender();
            open_linked_card();
        },
        error(xhr) {
            linked_task_id = undefined;
            ui_report.error(
                $t_html({defaultMessage: "Could not load the task board"}),
                xhr,
                $("#home-error"),
            );
        },
    });
}

export function show(
    filter: task_board_data.TaskFilter = task_board_data.FILTERS.ALL,
    task_id?: number,
    view: task_board_data.BoardView = "board",
): void {
    assert(hide_other_views_callback !== undefined);
    hide_other_views_callback();
    task_board_data.set_filter(filter);
    task_board_data.set_view(view);
    linked_task_id = task_id;
    card_unavailable = false;

    // The personal list has its own sidebar entry, which marks itself
    // from the hash. The filter's row would mark the board as well.
    const highlight_filter = view === "list" ? "" : filter;

    const was_already_visible = is_visible();
    if (was_already_visible) {
        // views_util.show does nothing for a view that is already
        // open, so a switch between the Work rows moves the highlight
        // here.
        left_sidebar_navigation_area.highlight_task_board_view(highlight_filter);
    }
    views_util.show({
        highlight_view_in_left_sidebar() {
            views_util.handle_message_view_deactivated(() => {
                left_sidebar_navigation_area.highlight_task_board_view(highlight_filter);
            });
        },
        $view: $("#task-board-view"),
        update_compose: compose_closed_ui.update_buttons,
        is_visible: () => was_already_visible,
        set_visible,
        complete_rerender,
    });

    fetch_board();
    fetch_plan();
}

export function hide(): void {
    if (!is_visible()) {
        return;
    }
    close_card_detail();
    views_util.hide({$view: $("#task-board-view"), set_visible});
}

export function handle_task_event(event: {op: string; task?: unknown; task_id?: number}): void {
    if (event.op === "remove") {
        assert(event.task_id !== undefined);
        task_board_data.remove_task(event.task_id);
        notes_by_task_id.delete(event.task_id);
        if (open_task_id === event.task_id) {
            close_card_detail();
        }
    } else {
        const task = task_board_data.task_schema.parse(event.task);
        task_board_data.add_or_update_task(task);
    }
    complete_rerender();
    schedule_work_counts_fetch();
}

export function handle_task_note_event(event: {
    op: string;
    task_id: number;
    note?: unknown;
    note_id?: number;
}): void {
    const notes = notes_by_task_id.get(event.task_id);
    if (notes === undefined) {
        // Nobody has opened this card yet. If the panel is loading its
        // notes now, the list may have been read before this change.
        if (open_task_id === event.task_id) {
            fetch_notes(event.task_id);
        }
        return;
    }
    if (event.op === "add") {
        const note = task_board_notes.task_note_schema.parse(event.note);
        notes_by_task_id.set(event.task_id, task_board_notes.add_note(notes, note));
    } else {
        assert(event.note_id !== undefined);
        notes_by_task_id.set(event.task_id, task_board_notes.remove_note(notes, event.note_id));
    }
    if (open_task_id === event.task_id) {
        render_card_detail(event.task_id);
    }
}

export function handle_task_board_event(event: {board: unknown}): void {
    task_board_data.set_board_name(task_board_data.task_board_schema.parse(event.board));
    complete_rerender();
}

function add_to_plan(task_id: number): void {
    void channel.post({
        url: `/json/tasks/${task_id}/plan`,
        data: {date: plan_date(Date.now())},
        error: report_error,
    });
}

// The cards go in one after another, so they keep the order of the prompt.
function add_all_to_plan(task_ids: number[]): void {
    const [first, ...rest] = task_ids;
    if (first === undefined) {
        return;
    }
    void channel.post({
        url: `/json/tasks/${first}/plan`,
        data: {date: plan_date(Date.now())},
        success() {
            add_all_to_plan(rest);
        },
        error: report_error,
    });
}

function remove_from_plan(task_id: number): void {
    void channel.del({
        url: `/json/tasks/${task_id}/plan`,
        data: {date: plan_date(Date.now())},
        error: report_error,
    });
}

function planned_task_ids(now_ms: number): number[] {
    return task_board_data
        .todays_plan(people.my_current_user_id(), now_ms, timerender.display_time_zone)
        .planned.map((task) => task.id);
}

// `slot_of` turns the card's index in today's plan into the slot it moves
// to, counted among the other planned cards.
function move_planned_card(
    task_id: number,
    slot_of: (index: number) => number,
    direction: "up" | "down" | undefined,
): void {
    const now_ms = Date.now();
    const today = plan_date(now_ms);
    const ids = planned_task_ids(now_ms);
    const index = ids.indexOf(task_id);
    const slot = slot_of(index);
    if (index === -1 || slot < 0 || slot > ids.length - 1) {
        return;
    }
    pending_plan_focus = {task_id, direction};
    void channel.patch({
        url: `/json/tasks/${task_id}/plan`,
        data: {
            date: today,
            position: task_board_data.plan_position_for_slot(
                today,
                ids.filter((id) => id !== task_id),
                slot,
                task_id,
            ),
        },
        error: report_error,
    });
}

function restore_plan_focus(): void {
    if (pending_plan_focus === undefined) {
        return;
    }
    const {task_id, direction} = pending_plan_focus;
    pending_plan_focus = undefined;
    const $item = $(`.task-board-plan-item[data-task-id="${task_id}"]`);
    const $button =
        direction === undefined
            ? $()
            : $item.find(`.task-board-plan-move[data-direction="${direction}"]:enabled`);
    ($button.length > 0 ? $button : $item.find(".task-board-list-row")).trigger("focus");
}

export function handle_task_plan_event(event: {
    op: string;
    task_id: number;
    date: string;
    position?: number;
}): void {
    switch (event.op) {
        case "add":
            task_board_data.add_plan_entry(task_board_data.task_plan_entry_schema.parse(event));
            break;
        case "reorder":
            task_board_data.reorder_plan_entry(event.task_id, event.date, event.position ?? 0);
            break;
        default:
            task_board_data.remove_plan_entry(event.task_id, event.date);
            break;
    }
    complete_rerender();
    restore_plan_focus();
}

function render_count($count: JQuery, count: number, text: string): void {
    $count.toggleClass("hide", count === 0).text(count === 0 ? "" : text);
}

export function render_work_counts(counts: task_board_data.WorkCounts): void {
    render_count(
        $(".top_left_task_board .unread_count"),
        counts.task_board,
        String(counts.task_board),
    );
    render_count($(".top_left_my_tasks .unread_count"), counts.my_tasks, String(counts.my_tasks));
    render_count(
        $(".top_left_awaiting_review .unread_count"),
        counts.awaiting_my_review,
        String(counts.awaiting_my_review),
    );
    render_count(
        $(".top_left_agent_tasks .unread_count"),
        counts.agent_running,
        $t({defaultMessage: "{count} running"}, {count: counts.agent_running}),
    );
}

function fetch_work_counts(): void {
    void channel.get({
        url: "/json/tasks/counts",
        success(raw_data) {
            const data = z.object({counts: task_board_data.work_counts_schema}).parse(raw_data);
            render_work_counts(data.counts);
        },
    });
}

// Several card events can arrive in one burst, for example when someone
// drags a card; one fetch after the burst is enough.
let work_counts_timer: ReturnType<typeof setTimeout> | undefined;

function schedule_work_counts_fetch(): void {
    clearTimeout(work_counts_timer);
    work_counts_timer = setTimeout(fetch_work_counts, 500);
}

export function initialize(opts: {hide_other_views: () => void}): void {
    hide_other_views_callback = opts.hide_other_views;
    fetch_work_counts();

    const $view = $("#task-board-view");

    setup_assignee_picker();

    $view.on(
        "click",
        ".task-board-card, .task-board-list-row",
        function (this: HTMLElement, event) {
            event.preventDefault();
            open_card_detail(Number($(this).attr("data-task-id")));
        },
    );

    $view.on(
        "keydown",
        ".task-board-card, .task-board-list-row",
        function (this: HTMLElement, event) {
            if (event.key !== "Enter" && event.key !== " ") {
                return;
            }
            event.preventDefault();
            open_card_detail(Number($(this).attr("data-task-id")));
        },
    );

    $view.on("click", ".task-board-plan-add", function (this: HTMLElement, event) {
        event.preventDefault();
        add_to_plan(Number($(this).attr("data-task-id")));
    });

    $view.on("click", ".task-board-plan-remove", function (this: HTMLElement, event) {
        event.preventDefault();
        remove_from_plan(Number($(this).attr("data-task-id")));
    });

    $view.on("click", ".task-board-plan-move", function (this: HTMLElement, event) {
        event.preventDefault();
        const direction = $(this).attr("data-direction") === "up" ? "up" : "down";
        move_planned_card(
            Number($(this).attr("data-task-id")),
            (index) => (direction === "up" ? index - 1 : index + 1),
            direction,
        );
    });

    // Alt+Up and Alt+Down move the focused planned card, so the list can
    // be put in order without the buttons or the mouse.
    $view.on(
        "keydown",
        ".task-board-plan-item .task-board-list-row",
        function (this: HTMLElement, event) {
            if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) {
                return;
            }
            event.preventDefault();
            const up = event.key === "ArrowUp";
            move_planned_card(
                Number($(this).attr("data-task-id")),
                (index) => (up ? index - 1 : index + 1),
                undefined,
            );
        },
    );

    $view.on("click", ".task-board-plan-carry-all", (event) => {
        event.preventDefault();
        add_all_to_plan(
            task_board_data
                .todays_plan(people.my_current_user_id(), Date.now(), timerender.display_time_zone)
                .carry_over.map((task) => task.id),
        );
    });

    $view.on("click", ".task-board-plan-carry-dismiss", (event) => {
        event.preventDefault();
        carry_over_dismissed_for = plan_date(Date.now());
        complete_rerender();
    });

    $view.on("click", ".task-board-detail-plan", (event) => {
        event.preventDefault();
        if (open_task_id === undefined) {
            return;
        }
        if (task_board_data.is_planned(open_task_id, plan_date(Date.now()))) {
            remove_from_plan(open_task_id);
        } else {
            add_to_plan(open_task_id);
        }
    });

    $view.on("dragstart", ".task-board-plan-item", function (this: HTMLElement, event) {
        const data_transfer = event.originalEvent?.dataTransfer;
        if (data_transfer === null || data_transfer === undefined) {
            return;
        }
        plan_dragged_task_id = Number($(this).attr("data-task-id"));
        data_transfer.setData("text/plain", String(plan_dragged_task_id));
        data_transfer.effectAllowed = "move";
        $(this).addClass("task-board-plan-item-dragging");
    });

    $view.on("dragend", ".task-board-plan-item", () => {
        plan_dragged_task_id = undefined;
        clear_plan_drop_feedback();
    });

    $view.on("dragover", ".task-board-plan-sortable", function (this: HTMLElement, event) {
        const original = event.originalEvent;
        if (plan_dragged_task_id === undefined || original === undefined) {
            return;
        }
        event.preventDefault();
        if (original.dataTransfer !== null) {
            original.dataTransfer.dropEffect = "move";
        }
        show_plan_drop_target($(this), original.clientY);
    });

    $view.on("drop", ".task-board-plan-sortable", function (this: HTMLElement, event) {
        event.preventDefault();
        const task_id = plan_dragged_task_id;
        const original = event.originalEvent;
        if (task_id === undefined || original === undefined) {
            return;
        }
        const slot = plan_drop_slot($(this), original.clientY);
        clear_plan_drop_feedback();
        plan_dragged_task_id = undefined;
        move_planned_card(task_id, () => slot, undefined);
    });

    $view.on("click", ".task-board-detail-close", (event) => {
        event.preventDefault();
        close_card_detail();
    });

    $view.on("click", ".task-board-detail-chat", (event) => {
        event.preventDefault();
        if (open_task_id === undefined) {
            return;
        }
        open_card_conversation(open_task_id);
    });

    $view.on("click", ".task-board-rename", (event) => {
        event.preventDefault();
        launch_rename_dialog();
    });

    $view.on("click", ".task-board-new-card", (event) => {
        event.preventDefault();
        const first_column = task_board_data.get_columns()[0];
        if (first_column !== undefined) {
            launch_new_card_dialog(first_column.id);
        }
    });

    $view.on("click", ".task-board-add-card", function (this: HTMLElement, event) {
        event.preventDefault();
        launch_new_card_dialog(Number($(this).attr("data-column-id")));
    });

    $view.on("change", ".task-board-filter", () => {
        const value = String($("#task-board-filter").val() ?? "");
        task_board_data.set_filter(task_board_data.parse_filter(value));
        complete_rerender();
    });

    $view.on("click", ".task-board-show-folded", (event) => {
        event.preventDefault();
        task_board_data.unfold_all();
        complete_rerender();
    });

    $view.on("change", ".task-board-detail-checklist-item", (event) => {
        if (open_task_id === undefined) {
            return;
        }
        const task = task_board_data.get_task(open_task_id);
        if (task === undefined) {
            return;
        }
        const $item = $(event.currentTarget);
        const index = Number($item.attr("data-index"));
        const done = $item.is(":checked");
        const checklist = task.checklist.map((item, item_index) =>
            item_index === index ? {...item, done} : item,
        );
        patch_task(task.id, {checklist: JSON.stringify(checklist)});
    });

    $view.on("input", `#${NOTE_INPUT_ID}`, update_note_submit_button);

    $view.on("keydown", `#${NOTE_INPUT_ID}`, (event) => {
        if (
            event.key === "Enter" &&
            (event.ctrlKey || event.metaKey) &&
            open_task_id !== undefined
        ) {
            event.preventDefault();
            post_note(open_task_id);
        }
    });

    $view.on("click", ".task-board-note-submit", (event) => {
        event.preventDefault();
        if (open_task_id !== undefined) {
            post_note(open_task_id);
        }
    });

    $view.on("click", ".task-board-note-delete", function (this: HTMLElement, event) {
        event.preventDefault();
        if (open_task_id === undefined) {
            return;
        }
        const task_id = open_task_id;
        const note_id = Number($(this).attr("data-note-id"));
        void channel.del({
            url: `/json/tasks/${task_id}/notes/${note_id}`,
            success() {
                const notes = notes_by_task_id.get(task_id);
                if (notes !== undefined) {
                    notes_by_task_id.set(task_id, task_board_notes.remove_note(notes, note_id));
                }
                if (open_task_id === task_id) {
                    render_card_detail(task_id);
                }
            },
            error: report_error,
        });
    });

    $view.on("change", `#${STATUS_COLUMN_ID}, #${STATUS_POSITION_ID}`, () => {
        const task =
            open_task_id === undefined ? undefined : task_board_data.get_task(open_task_id);
        if (task === undefined) {
            return;
        }
        status_draft = read_status_draft(task);
        status_error = undefined;
        update_status_controls(task);
    });

    $view.on("change", `#${PRIORITY_SELECT_ID}`, () => {
        if (open_task_id !== undefined) {
            change_priority(open_task_id, Number($(`#${PRIORITY_SELECT_ID}`).val()));
        }
    });

    $view.on("change", `#${DUE_INPUT_ID}`, () => {
        if (open_task_id !== undefined) {
            change_due_date(open_task_id, $<HTMLInputElement>(`#${DUE_INPUT_ID}`).val() ?? "");
        }
    });

    $view.on("click", ".task-board-due-clear", (event) => {
        event.preventDefault();
        if (open_task_id !== undefined) {
            change_due_date(open_task_id, "");
        }
    });

    $view.on("keydown", `#${LABEL_INPUT_ID}`, (event) => {
        if (event.key === "Enter" && open_task_id !== undefined) {
            event.preventDefault();
            add_label_from_input(open_task_id);
        }
    });

    $view.on("click", ".task-board-label-remove", function (this: HTMLElement, event) {
        event.preventDefault();
        const task =
            open_task_id === undefined ? undefined : task_board_data.get_task(open_task_id);
        const label = $(this).attr("data-label");
        if (task !== undefined && label !== undefined) {
            label_error = undefined;
            change_labels(task.id, task_board_data.remove_label(task.labels, label));
        }
    });

    $view.on("click", `#${STATUS_SAVE_ID}`, (event) => {
        event.preventDefault();
        if (open_task_id !== undefined) {
            save_status(open_task_id);
        }
    });

    $view.on("click", ".task-board-detail-delete", (event) => {
        event.preventDefault();
        if (open_task_id === undefined) {
            return;
        }
        const task_id = open_task_id;
        confirm_dialog.launch({
            modal_title_text: $t({defaultMessage: "Delete card"}),
            modal_content_html: $t_html({
                defaultMessage:
                    "The card is removed from the board. The conversation it came from is unchanged.",
            }),
            modal_submit_button_text: $t({defaultMessage: "Delete"}),
            on_click() {
                void channel.del({url: `/json/tasks/${task_id}`, error: report_error});
            },
        });
    });

    $view.on("dragstart", ".task-board-card", function (this: HTMLElement, event) {
        const data_transfer = event.originalEvent?.dataTransfer;
        if (data_transfer === null || data_transfer === undefined) {
            return;
        }
        const task = task_board_data.get_task(Number($(this).attr("data-task-id")));
        if (task === undefined || !can_move_task(task)) {
            event.preventDefault();
            return;
        }
        dragged_task_id = task.id;
        data_transfer.setData("text/plain", String(dragged_task_id));
        data_transfer.effectAllowed = "move";
        $(this).addClass("task-board-card-dragging");
    });

    $view.on("dragend", ".task-board-card", function (this: HTMLElement) {
        $(this).removeClass("task-board-card-dragging");
        dragged_task_id = undefined;
        clear_drop_feedback();
    });

    $view.on("dragover", ".task-board-column", function (this: HTMLElement, event) {
        const original = event.originalEvent;
        if (dragged_task_id === undefined || original === undefined) {
            return;
        }
        event.preventDefault();
        if (original.dataTransfer !== null) {
            original.dataTransfer.dropEffect = "move";
        }
        show_drop_target($(this), original.clientY);
        auto_scroll_while_dragging(this, original.clientX, original.clientY);
    });

    // Leaving for a card inside the same column also fires dragleave.
    // The pointer is still in the column then, and the line stays.
    $view.on("dragleave", ".task-board-column", function (this: HTMLElement, event) {
        const original = event.originalEvent;
        if (
            original !== undefined &&
            task_board_drag.is_inside(
                this.getBoundingClientRect(),
                original.clientX,
                original.clientY,
            )
        ) {
            return;
        }
        $(this).removeClass("task-board-column-drop-target");
        $(this).find(".task-board-drop-indicator").remove();
        drop_target = undefined;
    });

    $view.on("drop", ".task-board-column", function (this: HTMLElement, event) {
        event.preventDefault();
        const original = event.originalEvent;
        const task_id = Number(original?.dataTransfer?.getData("text/plain")) || dragged_task_id;
        const column_id = Number($(this).attr("data-column-id"));
        if (original === undefined || !task_id) {
            clear_drop_feedback();
            return;
        }
        if (drop_target?.column_id !== column_id) {
            show_drop_target($(this), original.clientY);
        }
        const target = drop_target;
        clear_drop_feedback();
        dragged_task_id = undefined;
        if (target === undefined) {
            return;
        }
        move_task(
            task_id,
            column_id,
            task_board_data.position_for_slot(
                column_id,
                target.visible_task_ids,
                target.slot,
                task_id,
            ),
        );
    });
}
