import $ from "jquery";
import assert from "minimalistic-assert";
import * as z from "zod/mini";

import render_task_board from "../templates/task_board.hbs";
import render_task_board_card_detail from "../templates/task_board_card_detail.hbs";
import render_task_board_new_card_form from "../templates/task_board_new_card_form.hbs";
import render_task_board_rename_form from "../templates/task_board_rename_form.hbs";

import * as channel from "./channel.ts";
import * as compose_closed_ui from "./compose_closed_ui.ts";
import * as confirm_dialog from "./confirm_dialog.ts";
import * as dialog_widget from "./dialog_widget.ts";
import * as hash_util from "./hash_util.ts";
import {$t, $t_html} from "./i18n.ts";
import * as left_sidebar_navigation_area from "./left_sidebar_navigation_area.ts";
import * as people from "./people.ts";
import * as sub_store from "./sub_store.ts";
import * as task_board_data from "./task_board_data.ts";
import type {Task, TaskHistoryEntry} from "./task_board_data.ts";
import * as timerender from "./timerender.ts";
import * as ui_report from "./ui_report.ts";
import * as views_util from "./views_util.ts";

let is_task_board_visible = false;
let hide_other_views_callback: (() => void) | undefined;
let open_task_id: number | undefined;
const history_by_task_id = new Map<number, TaskHistoryEntry[]>();

export function is_visible(): boolean {
    return is_task_board_visible;
}

function set_visible(value: boolean): void {
    is_task_board_visible = value;
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
        labels: task.labels,
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
    ].map((option) => ({...option, selected: option.value === current}));
}

export function complete_rerender(): void {
    if (!is_visible()) {
        return;
    }
    const board = task_board_data.get_board();
    if (board === undefined) {
        return;
    }

    const columns = task_board_data.get_columns().map((column) => {
        const folded_count = task_board_data.folded_count_in_column(column.id);
        return {
            id: column.id,
            name: column.name,
            work_limit: column.work_limit,
            done_window_days: column.done_window_days,
            done_window_label:
                column.done_window_days === null
                    ? ""
                    : $t({defaultMessage: "{days} days"}, {days: column.done_window_days}),
            card_count: task_board_data.tasks_in_column(column.id).length,
            at_work_limit: task_board_data.column_is_over_work_limit(column.id),
            folded_count,
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
            board_name: board.name,
            summary: board_summary(),
            columns,
            filters: filter_options(),
            members,
            extra_member_count: Math.max(0, member_ids.length - MAX_HEADER_MEMBERS),
        }),
    );

    if (open_task_id !== undefined) {
        render_card_detail(open_task_id);
    }
}

function history_string(entry: TaskHistoryEntry, key: string): string {
    const value = entry.extra_data[key];
    return typeof value === "string" ? value : "";
}

function history_text(entry: TaskHistoryEntry): string {
    const actor =
        entry.acting_user_id === null
            ? $t({defaultMessage: "Someone"})
            : (people.maybe_get_user_by_id(entry.acting_user_id, true)?.full_name ??
              $t({defaultMessage: "Someone"}));

    switch (entry.kind) {
        case "created":
            return $t({defaultMessage: "{actor} created the card"}, {actor});
        case "moved":
            return $t(
                {defaultMessage: "{actor} moved the card to {column}"},
                {actor, column: history_string(entry, "to_column_name")},
            );
        case "assigned":
            return $t({defaultMessage: "{actor} changed who is responsible"}, {actor});
        default:
            return $t({defaultMessage: "{actor} edited the card"}, {actor});
    }
}

function render_card_detail(task_id: number): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined) {
        close_card_detail();
        return;
    }

    const column = task_board_data.get_column(task.column_id);
    const assignee =
        task.assignee_id === null ? undefined : people.maybe_get_user_by_id(task.assignee_id, true);
    const history = history_by_task_id.get(task_id) ?? [];

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
            created_label: $t(
                {defaultMessage: "Created {date}"},
                {date: day_label(task.date_created)},
            ),
            checklist: task.checklist.map((item, index) => ({...item, index})),
            assignee_label:
                assignee === undefined
                    ? $t({defaultMessage: "Nobody is responsible for this card"})
                    : assignee.full_name,
            due_label: due_label(task),
            due_is_overdue: is_overdue(task),
            agent_job_url: task.agent_job_id === null ? "" : "#agent-jobs",
            history: history.map((entry) => ({
                time_label: time_label(entry.event_time),
                text: history_text(entry),
            })),
        }),
    );
    $("#task-board-detail").removeClass("hidden");
    $("#task-board-detail .task-board-detail-title").trigger("focus");
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
        },
    });
}

function open_card_detail(task_id: number): void {
    open_task_id = task_id;
    render_card_detail(task_id);
    fetch_history(task_id);
}

function close_card_detail(): void {
    open_task_id = undefined;
    $("#task-board-detail").addClass("hidden").empty();
}

function report_error(xhr: JQuery.jqXHR): void {
    ui_report.error($t_html({defaultMessage: "Could not save the change"}), xhr, $("#home-error"));
}

function patch_task(task_id: number, data: Record<string, string | number | boolean>): void {
    void channel.patch({
        url: `/json/tasks/${task_id}`,
        data,
        error: report_error,
    });
}

function move_task(task_id: number, column_id: number, index: number): void {
    const task = task_board_data.get_task(task_id);
    if (task === undefined) {
        return;
    }
    const position = task_board_data.position_for_drop(column_id, index);

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
            on_click() {
                patch_task(task_id, {column_id, position});
            },
        });
        return;
    }

    patch_task(task_id, {column_id, position});
}

function drop_index($column_cards: JQuery, drop_y: number): number {
    const $cards = $column_cards.children(".task-board-card");
    let index = 0;
    for (const card of $cards) {
        const box = card.getBoundingClientRect();
        if (drop_y < box.top + box.height / 2) {
            return index;
        }
        index += 1;
    }
    return index;
}

function launch_new_card_dialog(column_id: number): void {
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "New task"}),
        modal_content_html: render_task_board_new_card_form(),
        modal_submit_button_text: $t({defaultMessage: "Create"}),
        id: "task-board-new-card-modal",
        form_id: "task-board-new-card-form",
        focus_submit_on_open: false,
        on_click() {
            const title = $<HTMLInputElement>("#task-board-new-title").val()?.trim() ?? "";
            if (title === "") {
                return;
            }
            void channel.post({
                url: "/json/tasks",
                data: {
                    title,
                    body: $<HTMLTextAreaElement>("#task-board-new-body").val() ?? "",
                    column_id,
                },
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

function fetch_board(): void {
    void channel.get({
        url: "/json/tasks",
        success(raw_data) {
            task_board_data.set_board(task_board_data.task_board_response_schema.parse(raw_data));
            complete_rerender();
        },
        error(xhr) {
            ui_report.error(
                $t_html({defaultMessage: "Could not load the task board"}),
                xhr,
                $("#home-error"),
            );
        },
    });
}

export function show(filter: task_board_data.TaskFilter = task_board_data.FILTERS.ALL): void {
    assert(hide_other_views_callback !== undefined);
    hide_other_views_callback();
    task_board_data.set_filter(filter);

    const was_already_visible = is_visible();
    views_util.show({
        highlight_view_in_left_sidebar() {
            views_util.handle_message_view_deactivated(() => {
                left_sidebar_navigation_area.highlight_task_board_view(filter);
            });
        },
        $view: $("#task-board-view"),
        update_compose: compose_closed_ui.update_buttons,
        is_visible: () => was_already_visible,
        set_visible,
        complete_rerender,
    });

    fetch_board();
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

export function handle_task_board_event(event: {board: unknown}): void {
    task_board_data.set_board_name(task_board_data.task_board_schema.parse(event.board));
    complete_rerender();
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

    $view.on("click", ".task-board-card", function (this: HTMLElement, event) {
        event.preventDefault();
        open_card_detail(Number($(this).attr("data-task-id")));
    });

    $view.on("keydown", ".task-board-card", function (this: HTMLElement, event) {
        if (event.key !== "Enter" && event.key !== " ") {
            return;
        }
        event.preventDefault();
        open_card_detail(Number($(this).attr("data-task-id")));
    });

    $view.on("click", ".task-board-detail-close", (event) => {
        event.preventDefault();
        close_card_detail();
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
        data_transfer.setData("text/plain", $(this).attr("data-task-id") ?? "");
        data_transfer.effectAllowed = "move";
        $(this).addClass("task-board-card-dragging");
    });

    $view.on("dragend", ".task-board-card", function (this: HTMLElement) {
        $(this).removeClass("task-board-card-dragging");
        $view.find(".task-board-column-cards").removeClass("task-board-column-cards-drop-target");
    });

    $view.on("dragover", ".task-board-column-cards", function (this: HTMLElement, event) {
        event.preventDefault();
        $(this).addClass("task-board-column-cards-drop-target");
    });

    $view.on("dragleave", ".task-board-column-cards", function (this: HTMLElement) {
        $(this).removeClass("task-board-column-cards-drop-target");
    });

    $view.on("drop", ".task-board-column-cards", function (this: HTMLElement, event) {
        event.preventDefault();
        $(this).removeClass("task-board-column-cards-drop-target");

        const original = event.originalEvent;
        const task_id = Number(original?.dataTransfer?.getData("text/plain"));
        if (original === undefined || !task_id) {
            return;
        }
        const column_id = Number($(this).attr("data-column-id"));
        move_task(task_id, column_id, drop_index($(this), original.clientY));
    });
}
