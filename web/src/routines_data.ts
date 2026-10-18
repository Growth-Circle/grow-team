import * as z from "zod/mini";

import * as channel from "./channel.ts";

// The data side of the Scheduled page (routines_ui.ts): what the server
// sends, the address of each screen, the form's draft, and the small
// decisions that need no page to test. The words are in routines_labels.ts.

export const MAX_TITLE_LENGTH = 100;
export const MAX_INSTRUCTIONS_LENGTH = 4000;
export const MAX_TOPIC_LENGTH = 60;
export const MIN_INTERVAL_MINUTES = 15;
export const MAX_INTERVAL_MINUTES = 59;
export const MAX_INTERVAL_HOURS = 23;

export const PATTERN_KINDS = [
    "daily",
    "weekdays",
    "weekly",
    "monthly",
    "every_hours",
    "every_minutes",
] as const;
export type PatternKind = (typeof PATTERN_KINDS)[number];

export const MODEL_PRESETS = ["", "fast", "balanced", "best"] as const;

const run_schema = z.object({
    id: z.string(),
    routine_id: z.string(),
    trigger: z.enum(["schedule", "manual"]),
    scheduled_for: z.number(),
    started_at: z.nullable(z.number()),
    finished_at: z.nullable(z.number()),
    status: z.string(),
    error_code: z.string(),
    result_message_id: z.nullable(z.number()),
    cost_microunits: z.nullable(z.number()),
});
export type Run = z.infer<typeof run_schema>;

export const routine_schema = z.object({
    id: z.string(),
    title: z.string(),
    instructions: z.string(),
    agent_profile_id: z.nullable(z.string()),
    agent_name: z.string(),
    owner_id: z.number(),
    stream_id: z.nullable(z.number()),
    topic: z.string(),
    pattern_kind: z.enum(PATTERN_KINDS),
    interval: z.nullable(z.number()),
    time_of_day: z.nullable(z.string()),
    weekdays: z.array(z.number()),
    month_day: z.nullable(z.number()),
    timezone: z.string(),
    model_preset: z.string(),
    enabled: z.boolean(),
    paused_reason: z.string(),
    next_run_at: z.nullable(z.number()),
    last_run_at: z.nullable(z.number()),
    last_status: z.string(),
    executor: z.enum(["agent", "external"]),
});
export type RoutineFields = z.infer<typeof routine_schema>;

const routine_detail_schema = z.object({
    ...routine_schema.shape,
    can_edit: z.boolean(),
    last_run: z.nullable(run_schema),
});
export type Routine = z.infer<typeof routine_detail_schema>;

const template_schema = z.object({
    id: z.string(),
    title: z.string(),
    instructions: z.string(),
    pattern_kind: z.enum(PATTERN_KINDS),
    time_of_day: z.string(),
    weekdays: z.array(z.number()),
});
export type Template = z.infer<typeof template_schema>;

const agent_schema = z.object({id: z.string(), name: z.string()});
export type Agent = z.infer<typeof agent_schema>;

const list_response_schema = z.object({routines: z.array(routine_detail_schema)});
const detail_response_schema = z.object({routine: routine_detail_schema});
const runs_response_schema = z.object({runs: z.array(run_schema)});
const run_response_schema = z.object({run: run_schema});
const templates_response_schema = z.object({templates: z.array(template_schema)});
const agents_response_schema = z.object({
    agents: z.array(agent_schema),
    default_agent_id: z.nullable(z.string()),
    default_timezone: z.string(),
});

// ----- Addresses -----

export type Route =
    | {kind: "list"}
    | {kind: "new"; template_id: string | undefined}
    | {kind: "edit"; id: string}
    | {kind: "history"; id: string};

const ID_PATTERN = /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i;

// `args` is the hash after "routines": #routines, #routines/new,
// #routines/new/<template>, #routines/<id>/edit and
// #routines/<id>/history. An address that names nothing real opens the
// list.
export function parse_route(args: string[]): Route {
    const [first, second] = args;
    if (first === "new") {
        return {
            kind: "new",
            template_id: second === undefined || second === "" ? undefined : second,
        };
    }
    if (first !== undefined && ID_PATTERN.test(first)) {
        if (second === "edit") {
            return {kind: "edit", id: first};
        }
        if (second === "history") {
            return {kind: "history", id: first};
        }
    }
    return {kind: "list"};
}

export function route_hash(route: Route): string {
    switch (route.kind) {
        case "list":
            return "#routines";
        case "new":
            return route.template_id === undefined
                ? "#routines/new"
                : `#routines/new/${route.template_id}`;
        case "edit":
            return `#routines/${route.id}/edit`;
        case "history":
            return `#routines/${route.id}/history`;
    }
}

// ----- The form's draft -----

// Everything the form holds is text, as it is in the controls.
export type Draft = {
    title: string;
    instructions: string;
    agent_profile_id: string;
    stream_id: string;
    topic: string;
    pattern_kind: PatternKind;
    interval: string;
    hour: string;
    minute: string;
    weekdays: number[];
    month_day: string;
    model_preset: string;
};

export type FormDefaults = {agent_profile_id: string; stream_id: string};

export function empty_draft(defaults: FormDefaults): Draft {
    return {
        title: "",
        instructions: "",
        agent_profile_id: defaults.agent_profile_id,
        stream_id: defaults.stream_id,
        topic: "",
        pattern_kind: "daily",
        interval: "30",
        hour: "08",
        minute: "00",
        weekdays: [4],
        month_day: "1",
        model_preset: "",
    };
}

function clock_parts(time_of_day: string | null): {hour: string; minute: string} | undefined {
    const match = time_of_day === null ? null : /^(\d{2}):(\d{2})$/.exec(time_of_day);
    return match?.[1] === undefined || match[2] === undefined
        ? undefined
        : {hour: match[1], minute: match[2]};
}

export function draft_from_template(template: Template, defaults: FormDefaults): Draft {
    return {
        ...empty_draft(defaults),
        title: template.title,
        instructions: template.instructions,
        pattern_kind: template.pattern_kind,
        ...clock_parts(template.time_of_day),
        weekdays: template.weekdays.length === 0 ? [4] : template.weekdays,
    };
}

export function draft_from_routine(routine: RoutineFields, defaults: FormDefaults): Draft {
    const base = empty_draft(defaults);
    return {
        title: routine.title,
        instructions: routine.instructions,
        agent_profile_id: routine.agent_profile_id ?? base.agent_profile_id,
        stream_id: routine.stream_id === null ? base.stream_id : String(routine.stream_id),
        topic: routine.topic,
        pattern_kind: routine.pattern_kind,
        interval: routine.interval === null ? base.interval : String(routine.interval),
        ...(clock_parts(routine.time_of_day) ?? {hour: base.hour, minute: base.minute}),
        weekdays: routine.weekdays.length === 0 ? base.weekdays : routine.weekdays,
        month_day: routine.month_day === null ? base.month_day : String(routine.month_day),
        model_preset: routine.model_preset,
    };
}

export function uses_interval(kind: PatternKind): boolean {
    return kind === "every_minutes" || kind === "every_hours";
}

export function uses_time_of_day(kind: PatternKind): boolean {
    return !uses_interval(kind);
}

function whole_number(text: string): number | undefined {
    return /^\d{1,4}$/.test(text.trim()) ? Number(text.trim()) : undefined;
}

export function interval_bounds(kind: PatternKind): {min: number; max: number} {
    return kind === "every_minutes"
        ? {min: MIN_INTERVAL_MINUTES, max: MAX_INTERVAL_MINUTES}
        : {min: 1, max: MAX_INTERVAL_HOURS};
}

export type FieldName =
    | "title"
    | "instructions"
    | "agent"
    | "stream"
    | "topic"
    | "interval"
    | "weekdays";

// The fields that cannot be saved as they are. The server checks all
// of it again; this only keeps the person from sending what it will
// refuse.
export function invalid_fields(draft: Draft): FieldName[] {
    const invalid: FieldName[] = [];
    if (draft.title.trim() === "" || draft.title.trim().length > MAX_TITLE_LENGTH) {
        invalid.push("title");
    }
    if (
        draft.instructions.trim() === "" ||
        draft.instructions.trim().length > MAX_INSTRUCTIONS_LENGTH
    ) {
        invalid.push("instructions");
    }
    if (draft.agent_profile_id === "") {
        invalid.push("agent");
    }
    if (draft.stream_id === "") {
        invalid.push("stream");
    }
    if (draft.topic.trim() === "" || draft.topic.trim().length > MAX_TOPIC_LENGTH) {
        invalid.push("topic");
    }
    if (uses_interval(draft.pattern_kind)) {
        const bounds = interval_bounds(draft.pattern_kind);
        const interval = whole_number(draft.interval);
        if (interval === undefined || interval < bounds.min || interval > bounds.max) {
            invalid.push("interval");
        }
    }
    if (draft.pattern_kind === "weekly" && draft.weekdays.length === 0) {
        invalid.push("weekdays");
    }
    return invalid;
}

export function is_dirty(saved: Draft, draft: Draft): boolean {
    return JSON.stringify(saved) !== JSON.stringify(draft);
}

// The parameters of a save: only the fields that the pattern uses. The
// weekdays travel as a JSON list, as the API takes them.
export function draft_to_params(draft: Draft): Record<string, string> {
    const params: Record<string, string> = {
        title: draft.title.trim(),
        instructions: draft.instructions.trim(),
        agent_profile_id: draft.agent_profile_id,
        stream_id: draft.stream_id,
        topic: draft.topic.trim(),
        pattern_kind: draft.pattern_kind,
        model_preset: draft.model_preset,
    };
    if (uses_interval(draft.pattern_kind)) {
        params["interval"] = String(whole_number(draft.interval));
    } else {
        params["time_of_day"] = `${draft.hour}:${draft.minute}`;
    }
    if (draft.pattern_kind === "weekly") {
        params["weekdays"] = JSON.stringify(draft.weekdays.toSorted((a, b) => a - b));
    }
    if (draft.pattern_kind === "monthly") {
        params["month_day"] = String(whole_number(draft.month_day) ?? 1);
    }
    return params;
}

// ----- The list -----

export function sort_routines(routines: Routine[]): Routine[] {
    // A routine that runs soon comes first; a paused one, and one that
    // runs outside Sanji, after those that run.
    const rank = (routine: Routine): number =>
        routine.enabled && routine.next_run_at !== null ? 0 : 1;
    return routines.toSorted(
        (a, b) =>
            rank(a) - rank(b) ||
            (a.next_run_at ?? Number.POSITIVE_INFINITY) -
                (b.next_run_at ?? Number.POSITIVE_INFINITY) ||
            a.title.localeCompare(b.title),
    );
}

export function replace_routine(routines: Routine[], changed: Routine): Routine[] {
    return routines.some((routine) => routine.id === changed.id)
        ? routines.map((routine) => (routine.id === changed.id ? changed : routine))
        : [...routines, changed];
}

// A run that is still going, whose result is not in yet.
export function is_running(status: string): boolean {
    return status === "running";
}

export function was_skipped(status: string): boolean {
    return status.startsWith("skipped_");
}

// A run takes this long, in whole seconds, once it has ended.
export function run_seconds(run: Run): number | undefined {
    if (run.started_at === null || run.finished_at === null) {
        return undefined;
    }
    return Math.max(0, run.finished_at - run.started_at);
}

// ----- The week -----

export type WeekItem = {
    routine: Routine;
    // "HH:MM", or undefined for a routine that repeats within the day.
    time: string | undefined;
};

export type WeekDay = {date: Date; key: string; items: WeekItem[]};

function days_in_month(year: number, month_index: number): number {
    return new Date(Date.UTC(year, month_index + 1, 0)).getUTCDate();
}

// Whether the routine has a run on this calendar day, read as a day of
// the routine's own clock.
export function runs_on(routine: Routine, year: number, month_index: number, day: number): boolean {
    // Monday is 0, as the server counts.
    const weekday = (new Date(Date.UTC(year, month_index, day)).getUTCDay() + 6) % 7;
    switch (routine.pattern_kind) {
        case "weekdays":
            return weekday < 5;
        case "weekly":
            return routine.weekdays.includes(weekday);
        case "monthly":
            return day === Math.min(routine.month_day ?? 1, days_in_month(year, month_index));
        case "daily":
        case "every_hours":
        case "every_minutes":
            return true;
    }
}

// The next `count` days from `today`, each with the routines that run on
// it, in the order of their time. Only a routine that runs counts: a
// paused one has nothing to show.
export function week_days(routines: Routine[], today: Date, count = 7): WeekDay[] {
    const running = routines.filter((routine) => routine.enabled);
    const days: WeekDay[] = [];
    for (let offset = 0; offset < count; offset += 1) {
        const date = new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset);
        const items = running
            .filter((routine) =>
                runs_on(routine, date.getFullYear(), date.getMonth(), date.getDate()),
            )
            .map(
                (routine): WeekItem => ({
                    routine,
                    time: uses_time_of_day(routine.pattern_kind)
                        ? (routine.time_of_day ?? undefined)
                        : undefined,
                }),
            )
            .toSorted(
                (a, b) =>
                    (a.time ?? "").localeCompare(b.time ?? "") ||
                    a.routine.title.localeCompare(b.routine.title),
            );
        days.push({
            date,
            key: `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`,
            items,
        });
    }
    return days;
}

// ----- Requests -----

export async function fetch_routines(): Promise<Routine[]> {
    const result: unknown = await channel.get({url: "/json/routines"});
    return list_response_schema.parse(result).routines;
}

export async function fetch_routine(id: string): Promise<Routine> {
    const result: unknown = await channel.get({url: `/json/routines/${id}`});
    return detail_response_schema.parse(result).routine;
}

export async function fetch_runs(id: string): Promise<Run[]> {
    const result: unknown = await channel.get({url: `/json/routines/${id}/runs`});
    return runs_response_schema.parse(result).runs;
}

export async function fetch_templates(): Promise<Template[]> {
    const result: unknown = await channel.get({url: "/json/routines/templates"});
    return templates_response_schema.parse(result).templates;
}

export async function fetch_agents(): Promise<{
    agents: Agent[];
    default_agent_id: string | null;
    default_timezone: string;
}> {
    const result: unknown = await channel.get({url: "/json/routines/agents"});
    return agents_response_schema.parse(result);
}

export async function create_routine(params: Record<string, string>): Promise<Routine> {
    const result: unknown = await channel.post({url: "/json/routines", data: params});
    return detail_response_schema.parse(result).routine;
}

export async function save_routine(id: string, params: Record<string, string>): Promise<Routine> {
    const result: unknown = await channel.patch({url: `/json/routines/${id}`, data: params});
    return detail_response_schema.parse(result).routine;
}

export async function set_enabled(id: string, enabled: boolean): Promise<Routine> {
    const action = enabled ? "resume" : "pause";
    const result: unknown = await channel.post({url: `/json/routines/${id}/${action}`});
    return detail_response_schema.parse(result).routine;
}

export async function run_now(id: string): Promise<Run> {
    const result: unknown = await channel.post({url: `/json/routines/${id}/run`});
    return run_response_schema.parse(result).run;
}

export async function delete_routine(id: string): Promise<void> {
    await channel.del({url: `/json/routines/${id}`});
}
