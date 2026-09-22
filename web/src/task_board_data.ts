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
    agent_profile_id: z.nullable(z.string()),
    agent_job_id: z.nullable(z.string()),
    labels: z.array(z.string()),
    checklist: z.array(task_checklist_item_schema),
    due_at: z.nullable(z.number()),
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

export const task_board_response_schema = z.object({
    board: task_board_schema,
    columns: z.array(task_board_column_schema),
    tasks: z.array(task_schema),
    folded_task_ids: z.array(z.number()),
});

export type TaskBoard = z.infer<typeof task_board_schema>;
export type TaskBoardColumn = z.infer<typeof task_board_column_schema>;
export type Task = z.infer<typeof task_schema>;
export type TaskHistoryEntry = z.infer<typeof task_history_entry_schema>;
export type TaskBoardResponse = z.infer<typeof task_board_response_schema>;

let board: TaskBoard | undefined;
let columns: TaskBoardColumn[] = [];
const tasks = new Map<number, Task>();
let folded_task_ids = new Set<number>();

export function set_board(response: TaskBoardResponse): void {
    board = response.board;
    columns = response.columns.toSorted((a, b) => a.order - b.order);
    tasks.clear();
    for (const task of response.tasks) {
        tasks.set(task.id, task);
    }
    folded_task_ids = new Set(response.folded_task_ids);
}

export function clear(): void {
    board = undefined;
    columns = [];
    tasks.clear();
    folded_task_ids = new Set();
}

export function get_board(): TaskBoard | undefined {
    return board;
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

export const FILTERS = {
    ALL: "all",
    MINE: "mine",
    BLOCKED: "blocked",
} as const;

export type TaskFilter = (typeof FILTERS)[keyof typeof FILTERS];

let current_filter: TaskFilter = FILTERS.ALL;

export function get_filter(): TaskFilter {
    return current_filter;
}

export function set_filter(filter: TaskFilter): void {
    current_filter = filter;
}

function passes_filter(task: Task, my_user_id: number | undefined): boolean {
    switch (current_filter) {
        case FILTERS.MINE:
            return my_user_id !== undefined && task.assignee_id === my_user_id;
        case FILTERS.BLOCKED:
            return task.blocked;
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

export function position_for_drop(column_id: number, index: number): number {
    const column_tasks = tasks_in_column(column_id);
    const before = column_tasks[index - 1];
    const after = column_tasks[index];

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

export function column_is_over_work_limit(column_id: number): boolean {
    const work_limit = get_column(column_id)?.work_limit;
    if (work_limit === undefined || work_limit === null) {
        return false;
    }
    return tasks_in_column(column_id).length >= work_limit;
}
