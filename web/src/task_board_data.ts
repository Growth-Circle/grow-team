import {TZDate} from "@date-fns/tz";
import * as z from "zod/mini";

// The board is not part of the register payload; the view fetches it when it
// opens and keeps it current from `task` and `task_board` events.

export const task_board_schema = z.object({
    id: z.number(),
    name: z.string(),
    date_created: z.number(),
});

export const task_board_column_schema = z.object({
    id: z.number(),
    board_id: z.number(),
    name: z.string(),
    order: z.number(),
    work_limit: z.nullable(z.number()),
    done_window_days: z.nullable(z.number()),
    is_review: z.boolean(),
});

export const task_checklist_item_schema = z.object({
    text: z.string(),
    done: z.boolean(),
});

export const task_schema = z.object({
    id: z.number(),
    display_id: z.string(),
    board_id: z.number(),
    column_id: z.number(),
    title: z.string(),
    body: z.string(),
    position: z.number(),
    stream_id: z.nullable(z.number()),
    topic: z.string(),
    origin_message_id: z.nullable(z.number()),
    creator_id: z.number(),
    assignee_id: z.nullable(z.number()),
    reviewer_id: z.nullable(z.number()),
    agent_profile_id: z.nullable(z.string()),
    agent_job_id: z.nullable(z.string()),
    priority: z.number(),
    labels: z.array(z.string()),
    checklist: z.array(task_checklist_item_schema),
    due_at: z.nullable(z.number()),
    note_count: z.number(),
    blocked: z.boolean(),
    completed_at: z.nullable(z.number()),
    date_created: z.number(),
    last_updated: z.number(),
});

export const task_history_entry_schema = z.object({
    id: z.number(),
    task_id: z.number(),
    acting_user_id: z.nullable(z.number()),
    kind: z.string(),
    event_time: z.number(),
    extra_data: z.record(z.string(), z.unknown()),
});

export const task_history_response_schema = z.object({
    history: z.array(task_history_entry_schema),
});

export const work_counts_schema = z.object({
    task_board: z.number(),
    my_tasks: z.number(),
    awaiting_my_review: z.number(),
    agent_running: z.number(),
});

export type WorkCounts = z.infer<typeof work_counts_schema>;

export const task_board_response_schema = z.object({
    board: task_board_schema,
    columns: z.array(task_board_column_schema),
    tasks: z.array(task_schema),
    folded_task_ids: z.array(z.number()),
    workspace_timezone: z.string(),
});

export type TaskBoard = z.infer<typeof task_board_schema>;
export type TaskBoardColumn = z.infer<typeof task_board_column_schema>;
export type Task = z.infer<typeof task_schema>;
export type TaskHistoryEntry = z.infer<typeof task_history_entry_schema>;
export type TaskBoardResponse = z.infer<typeof task_board_response_schema>;

export const task_plan_entry_schema = z.object({
    task_id: z.number(),
    date: z.string(),
    position: z.number(),
});

export const task_plan_response_schema = z.object({
    plan: z.array(task_plan_entry_schema),
});

export type TaskPlanEntry = z.infer<typeof task_plan_entry_schema>;

let board: TaskBoard | undefined;
let columns: TaskBoardColumn[] = [];
const tasks = new Map<number, Task>();
// The member's private plan, by `YYYY-MM-DD`. Only the days the view
// fetched are here; an event for any other day is ignored.
const plan_by_date = new Map<string, TaskPlanEntry[]>();
let folded_task_ids = new Set<number>();
let workspace_timezone: string | undefined;

export function set_board(response: TaskBoardResponse): void {
    board = response.board;
    columns = response.columns.toSorted((a, b) => a.order - b.order);
    tasks.clear();
    for (const task of response.tasks) {
        tasks.set(task.id, task);
    }
    folded_task_ids = new Set(response.folded_task_ids);
    workspace_timezone = response.workspace_timezone;
}

export function clear(): void {
    board = undefined;
    columns = [];
    tasks.clear();
    workspace_timezone = undefined;
    folded_task_ids = new Set();
    plan_by_date.clear();
}

export function get_board(): TaskBoard | undefined {
    return board;
}

// The time zone the workspace chose, which the server sends with the board.
export function get_workspace_timezone(): string | undefined {
    return workspace_timezone;
}

export function set_board_name(new_board: TaskBoard): void {
    if (board?.id === new_board.id) {
        board = new_board;
    }
}

export function get_columns(): TaskBoardColumn[] {
    return columns;
}

export function get_column(column_id: number): TaskBoardColumn | undefined {
    return columns.find((column) => column.id === column_id);
}

export function get_task(task_id: number): Task | undefined {
    return tasks.get(task_id);
}

export function add_or_update_task(task: Task): void {
    if (board?.id !== task.board_id) {
        return;
    }
    tasks.set(task.id, task);
    // A card that moves or changes has been touched, so it belongs in the
    // visible part of a done column again.
    folded_task_ids.delete(task.id);
}

export function remove_task(task_id: number): void {
    tasks.delete(task_id);
    folded_task_ids.delete(task_id);
}

export function is_folded(task_id: number): boolean {
    return folded_task_ids.has(task_id);
}

export function unfold_all(): void {
    folded_task_ids = new Set();
}

export function tasks_in_column(column_id: number): Task[] {
    return [...tasks.values()]
        .filter((task) => task.column_id === column_id)
        .toSorted((a, b) => a.position - b.position || a.id - b.id);
}

export const PRIORITIES = {
    NONE: 0,
    LOW: 1,
    MEDIUM: 2,
    HIGH: 3,
} as const;

// Mirrors Task.MAX_LABELS and Task.MAX_LABEL_LENGTH in zerver/models/tasks.py.
export const MAX_LABELS = 10;
export const MAX_LABEL_LENGTH = 40;

export const FILTERS = {
    ALL: "all",
    MINE: "mine",
    REVIEW: "review",
    BLOCKED: "blocked",
    HIGH_PRIORITY: "high_priority",
} as const;

export type TaskFilter = (typeof FILTERS)[keyof typeof FILTERS];

let current_filter: TaskFilter = FILTERS.ALL;

export function parse_filter(value: string | undefined): TaskFilter {
    for (const filter of Object.values(FILTERS)) {
        if (filter === value) {
            return filter;
        }
    }
    return FILTERS.ALL;
}

export type BoardView = "board" | "list";

// The board opens on `#tasks[/<filter>]`, or on one card with
// `#tasks/<task_id>`, the link the bot sends in a direct message.
// `#tasks/mine/list` is the person's own open cards, listed by deadline.
export function parse_hash_args(args: string[]): {
    filter: TaskFilter;
    task_id: number | undefined;
    view: BoardView;
} {
    const [first, second] = args;
    if (first !== undefined && /^\d+$/.test(first)) {
        return {filter: FILTERS.ALL, task_id: Number(first), view: "board"};
    }
    const view = first === FILTERS.MINE && second === "list" ? "list" : "board";
    return {filter: parse_filter(first), task_id: undefined, view};
}

let current_view: BoardView = "board";

export function get_view(): BoardView {
    return current_view;
}

export function set_view(view: BoardView): void {
    current_view = view;
}

export function get_filter(): TaskFilter {
    return current_filter;
}

export function set_filter(filter: TaskFilter): void {
    current_filter = filter;
}

// The visibility flag lives here rather than in task_board_ui so that
// narrow_title can read it without an import cycle through views_util.
let is_task_board_visible = false;

export function is_visible(): boolean {
    return is_task_board_visible;
}

export function set_visible(value: boolean): void {
    is_task_board_visible = value;
}

function passes_filter(task: Task, my_user_id: number | undefined): boolean {
    switch (current_filter) {
        case FILTERS.MINE:
            return my_user_id !== undefined && task.assignee_id === my_user_id;
        case FILTERS.REVIEW:
            return (
                my_user_id !== undefined &&
                task.reviewer_id === my_user_id &&
                get_column(task.column_id)?.is_review === true
            );
        case FILTERS.BLOCKED:
            return task.blocked;
        case FILTERS.HIGH_PRIORITY:
            return task.priority === PRIORITIES.HIGH;
        default:
            return true;
    }
}

export function visible_tasks_in_column(column_id: number, my_user_id?: number): Task[] {
    return tasks_in_column(column_id).filter(
        (task) => !folded_task_ids.has(task.id) && passes_filter(task, my_user_id),
    );
}

export function assignee_ids(): number[] {
    const user_ids = new Set<number>();
    for (const task of tasks.values()) {
        if (task.assignee_id !== null) {
            user_ids.add(task.assignee_id);
        }
    }
    return [...user_ids];
}

export function folded_count_in_column(column_id: number): number {
    return tasks_in_column(column_id).filter((task) => folded_task_ids.has(task.id)).length;
}

export function total_task_count(): number {
    return tasks.size;
}

export function origin_stream_ids(): number[] {
    const stream_ids = new Set<number>();
    for (const task of tasks.values()) {
        if (task.stream_id !== null) {
            stream_ids.add(task.stream_id);
        }
    }
    return [...stream_ids];
}

// Cards are ordered by a float so a move writes one row instead of
// renumbering the column. The gap halves on each move between the same two
// cards, so it can only be halved about fifty times before floats stop
// separating them; a client that hits that re-fetches the board.
const POSITION_STEP = 1000;

function position_between(before: Task | undefined, after: Task | undefined): number {
    if (before === undefined && after === undefined) {
        return POSITION_STEP;
    }
    if (before === undefined) {
        return after!.position - POSITION_STEP;
    }
    if (after === undefined) {
        return before.position + POSITION_STEP;
    }
    return (before.position + after.position) / 2;
}

// `visible_task_ids` are the cards on screen in the target column, in
// order and without the card that is moving. Hidden cards, such as the
// ones a filter leaves out, keep their place: the card lands right next
// to the visible neighbor the person pointed at.
export function position_for_slot(
    column_id: number,
    visible_task_ids: number[],
    slot: number,
    moving_task_id: number,
): number {
    const others = tasks_in_column(column_id).filter((task) => task.id !== moving_task_id);
    const after_id = visible_task_ids[slot];
    if (after_id !== undefined) {
        const after_index = others.findIndex((task) => task.id === after_id);
        return position_between(others[after_index - 1], others[after_index]);
    }
    const before_id = visible_task_ids[slot - 1];
    if (before_id !== undefined) {
        const before_index = others.findIndex((task) => task.id === before_id);
        return position_between(others[before_index], others[before_index + 1]);
    }
    return position_between(others.at(-1), undefined);
}

export type MoveEdge = "top" | "bottom";

export function position_for_edge(
    column_id: number,
    edge: MoveEdge,
    moving_task_id: number,
): number {
    const others = tasks_in_column(column_id).filter((task) => task.id !== moving_task_id);
    return edge === "top"
        ? position_between(undefined, others[0])
        : position_between(others.at(-1), undefined);
}

// Saving a card into the column and the spot it already has changes
// nothing, so there is nothing to send.
export function move_to_edge_is_noop(task: Task, column_id: number, edge: MoveEdge): boolean {
    if (task.column_id !== column_id) {
        return false;
    }
    const column_tasks = tasks_in_column(column_id);
    const current_edge_task = edge === "top" ? column_tasks[0] : column_tasks.at(-1);
    return current_edge_task?.id === task.id;
}

export function column_is_over_work_limit(column_id: number): boolean {
    const work_limit = get_column(column_id)?.work_limit;
    if (work_limit === undefined || work_limit === null) {
        return false;
    }
    return tasks_in_column(column_id).length >= work_limit;
}

export type LabelEdit = {labels: string[]; error: "too_many" | "too_long" | undefined};

// A blank label, or one the card already has, changes nothing. The
// server enforces the same limits and stays the authority.
export function add_label(labels: string[], raw_label: string): LabelEdit {
    const label = raw_label.trim();
    if (label === "" || labels.includes(label)) {
        return {labels, error: undefined};
    }
    if (label.length > MAX_LABEL_LENGTH) {
        return {labels, error: "too_long"};
    }
    if (labels.length >= MAX_LABELS) {
        return {labels, error: "too_many"};
    }
    return {labels: [...labels, label], error: undefined};
}

export function remove_label(labels: string[], label: string): string[] {
    return labels.filter((existing) => existing !== label);
}

export const DUE_SECTIONS = [
    "overdue",
    "today",
    "tomorrow",
    "next_7_days",
    "later",
    "no_due_date",
] as const;

export type DueSection = (typeof DUE_SECTIONS)[number];

export type DueGroup = {section: DueSection; tasks: Task[]};

const MS_PER_DAY = 24 * 60 * 60 * 1000;

// The calendar day that a moment falls on in `time_zone`, as a count of
// days. Two moments are on the same day exactly when the counts match, and
// the difference is the number of calendar days between them, whatever
// the offset or a daylight saving change in between.
function day_number(timestamp_ms: number, time_zone: string): number {
    const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: time_zone,
        year: "numeric",
        month: "numeric",
        day: "numeric",
    }).formatToParts(timestamp_ms);
    const part = (type: string): number =>
        Number(parts.find((candidate) => candidate.type === type)?.value);
    return Date.UTC(part("year"), part("month") - 1, part("day")) / MS_PER_DAY;
}

export function due_section(due_at: number | null, now_ms: number, time_zone: string): DueSection {
    if (due_at === null) {
        return "no_due_date";
    }
    const days_away = day_number(due_at * 1000, time_zone) - day_number(now_ms, time_zone);
    if (days_away < 0) {
        return "overdue";
    }
    if (days_away === 0) {
        return "today";
    }
    if (days_away === 1) {
        return "tomorrow";
    }
    return days_away <= 7 ? "next_7_days" : "later";
}

// Buckets cards by how soon they are due, in the reader's time zone. Every
// section is returned, empty ones included, in the order they are shown.
// Inside a section the earliest deadline comes first, then the higher
// priority.
export function group_by_due(tasks: Task[], now_ms: number, time_zone: string): DueGroup[] {
    const groups = new Map<DueSection, Task[]>(DUE_SECTIONS.map((section) => [section, []]));
    for (const task of tasks) {
        groups.get(due_section(task.due_at, now_ms, time_zone))!.push(task);
    }
    return DUE_SECTIONS.map((section) => ({
        section,
        tasks: groups
            .get(section)!
            .toSorted(
                (a, b) =>
                    (a.due_at ?? 0) - (b.due_at ?? 0) ||
                    b.priority - a.priority ||
                    a.position - b.position ||
                    a.id - b.id,
            ),
    }));
}

// A card is open until it reaches a done column.
export function open_tasks_assigned_to(user_id: number): Task[] {
    return [...tasks.values()].filter(
        (task) => task.assignee_id === user_id && task.completed_at === null,
    );
}

// A card is due at the end of the chosen day, so it counts as due
// today until that day is over.
export function end_of_day_timestamp(date: string, time_zone: string): number {
    const [year, month, day] = date.split("-").map(Number);
    const end_of_day = new TZDate(year!, month! - 1, day!, 23, 59, 59, 0, time_zone);
    return Math.floor(end_of_day.getTime() / 1000);
}

// The value for a date input: the day the deadline falls on, or "".
export function due_date_input_value(due_at: number | null, time_zone: string): string {
    if (due_at === null) {
        return "";
    }
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: time_zone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).format(due_at * 1000);
    return parts;
}

// The day as `YYYY-MM-DD` in `time_zone`. The plan keys its days the way
// a date input does, so a day always means the reader's own day.
export function local_date(now_ms: number, time_zone: string): string {
    return new Intl.DateTimeFormat("en-CA", {
        timeZone: time_zone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).format(now_ms);
}

export function shift_date(date: string, days: number): string {
    const [year, month, day] = date.split("-").map(Number);
    return new Date(Date.UTC(year!, month! - 1, day! + days)).toISOString().slice(0, 10);
}

// How many working days after today a card is due, when the new-task form
// fills in the date.
const DEFAULT_DUE_WORKING_DAYS = 3;

// The date a new card is due unless the person changes it: three working
// days (Monday to Friday) after today in `time_zone`, as `YYYY-MM-DD`.
export function default_due_date(now_ms: number, time_zone: string): string {
    let date = local_date(now_ms, time_zone);
    let working_days_left = DEFAULT_DUE_WORKING_DAYS;
    while (working_days_left > 0) {
        date = shift_date(date, 1);
        const [year, month, day] = date.split("-").map(Number);
        const weekday = new Date(Date.UTC(year!, month! - 1, day)).getUTCDay();
        if (weekday !== 0 && weekday !== 6) {
            working_days_left -= 1;
        }
    }
    return date;
}

export function set_plan(date: string, entries: TaskPlanEntry[]): void {
    plan_by_date.set(date, entries);
}

export function has_plan_for(date: string): boolean {
    return plan_by_date.has(date);
}

export function plan_entries(date: string): TaskPlanEntry[] {
    return (plan_by_date.get(date) ?? []).toSorted(
        (a, b) => a.position - b.position || a.task_id - b.task_id,
    );
}

export function is_planned(task_id: number, date: string): boolean {
    return (plan_by_date.get(date) ?? []).some((entry) => entry.task_id === task_id);
}

export function add_plan_entry(entry: TaskPlanEntry): void {
    const entries = plan_by_date.get(entry.date);
    if (entries === undefined) {
        return;
    }
    plan_by_date.set(entry.date, [
        ...entries.filter((existing) => existing.task_id !== entry.task_id),
        entry,
    ]);
}

export function remove_plan_entry(task_id: number, date: string): void {
    const entries = plan_by_date.get(date);
    if (entries !== undefined) {
        plan_by_date.set(
            date,
            entries.filter((entry) => entry.task_id !== task_id),
        );
    }
}

export function reorder_plan_entry(task_id: number, date: string, position: number): void {
    const entries = plan_by_date.get(date);
    if (entries !== undefined) {
        plan_by_date.set(
            date,
            entries.map((entry) => (entry.task_id === task_id ? {...entry, position} : entry)),
        );
    }
}

export type TodayPlan = {
    // The cards the person chose, in their order. A card that is done, or
    // that the board no longer holds, is not shown.
    planned: Task[];
    // The person's own overdue and due-today cards that they did not plan.
    auto: Task[];
    // Yesterday's unfinished plan that today's plan does not have yet.
    carry_over: Task[];
};

export function todays_plan(user_id: number, now_ms: number, time_zone: string): TodayPlan {
    const today = local_date(now_ms, time_zone);
    const open_planned = (date: string): Task[] =>
        plan_entries(date).flatMap((entry) => {
            const task = tasks.get(entry.task_id);
            return task?.completed_at === null ? [task] : [];
        });

    const planned = open_planned(today);
    const planned_ids = new Set(planned.map((task) => task.id));
    const auto = open_tasks_assigned_to(user_id)
        .filter((task) => {
            const section = due_section(task.due_at, now_ms, time_zone);
            return (section === "overdue" || section === "today") && !planned_ids.has(task.id);
        })
        .toSorted((a, b) => a.due_at! - b.due_at! || b.priority - a.priority || a.id - b.id);
    const carry_over = open_planned(shift_date(today, -1)).filter(
        (task) => !is_planned(task.id, today),
    );
    return {planned, auto, carry_over};
}

function plan_position_between(before: number | undefined, after: number | undefined): number {
    if (before === undefined && after === undefined) {
        return POSITION_STEP;
    }
    if (before === undefined) {
        return after! - POSITION_STEP;
    }
    if (after === undefined) {
        return before + POSITION_STEP;
    }
    return (before + after) / 2;
}

// `visible_task_ids` are the planned cards on screen in order, without the
// one that moves. Planned cards the screen does not show keep their place:
// the card lands right next to the visible neighbor it was moved against.
export function plan_position_for_slot(
    date: string,
    visible_task_ids: number[],
    slot: number,
    moving_task_id: number,
): number {
    const others = plan_entries(date).filter((entry) => entry.task_id !== moving_task_id);
    const after_id = visible_task_ids[slot];
    if (after_id !== undefined) {
        const after_index = others.findIndex((entry) => entry.task_id === after_id);
        return plan_position_between(
            others[after_index - 1]?.position,
            others[after_index]?.position,
        );
    }
    const before_id = visible_task_ids[slot - 1];
    if (before_id !== undefined) {
        const before_index = others.findIndex((entry) => entry.task_id === before_id);
        return plan_position_between(
            others[before_index]?.position,
            others[before_index + 1]?.position,
        );
    }
    return plan_position_between(others.at(-1)?.position, undefined);
}
