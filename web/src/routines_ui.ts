import $ from "jquery";
import assert from "minimalistic-assert";
import * as z from "zod/mini";

import render_routine_form_page from "../templates/routine_form_page.hbs";
import render_routine_history_page from "../templates/routine_history_page.hbs";
import render_routines_list from "../templates/routines_list.hbs";
import render_routines_list_page from "../templates/routines_list_page.hbs";

import * as browser_history from "./browser_history.ts";
import * as compose_closed_ui from "./compose_closed_ui.ts";
import * as confirm_dialog from "./confirm_dialog.ts";
import * as feedback_widget from "./feedback_widget.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as left_sidebar_navigation_area from "./left_sidebar_navigation_area.ts";
import * as live_updates from "./live_updates.ts";
import type {Agent, Draft, Routine, Run, Template} from "./routines_data.ts";
import * as routines_data from "./routines_data.ts";
import * as routines_labels from "./routines_labels.ts";
import * as sanji_format from "./sanji_format.ts";
import {current_user} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import {user_settings} from "./user_settings.ts";
import * as views_util from "./views_util.ts";

// The Scheduled page (#routines, #routines/new, #routines/new/<template>,
// #routines/<id>/edit, #routines/<id>/history): the routines of the
// workspace, the form that makes or changes one, and the runs of one.

const EVENT_REFRESH_DELAY_MS = 400;

type LoadState = "loading" | "ready" | "failed";
type PageState = LoadState | "missing";

let visible = false;
let initialized = false;
let route: routines_data.Route = {kind: "list"};
// Raised by every change of address, so that a response for an address
// the person has left is dropped.
let visit = 0;
let refresh_timer: ReturnType<typeof setTimeout> | undefined;

// The list.
let items: Routine[] = [];
let list_state: LoadState = "loading";
// The routines that a request is changing, so a second click waits.
const busy_ids = new Set<string>();

// The form.
let form_state: PageState = "loading";
let editing_routine: Routine | undefined;
let templates: Template[] = [];
let agents: Agent[] = [];
let default_agent_id = "";
let default_timezone = "Asia/Jakarta";
let draft: Draft = routines_data.empty_draft({agent_profile_id: "", stream_id: ""});
let saved_draft: Draft = draft;
let saving = false;
let error_text = "";
let show_validation = false;

// The history.
let history_state: PageState = "loading";
let history_routine: Routine | undefined;
let runs: Run[] = [];

function set_visible(value: boolean): void {
    visible = value;
}

function $view(): JQuery {
    return $("#routines-view");
}

// ----- Small helpers -----

const error_schema = z.object({
    status: z.number(),
    responseJSON: z.optional(z.object({msg: z.optional(z.string())})),
});

function error_message(error: unknown, fallback: string): string {
    const parsed = error_schema.safeParse(error);
    return (parsed.success ? parsed.data.responseJSON?.msg : undefined) ?? fallback;
}

function is_missing(error: unknown): boolean {
    const parsed = error_schema.safeParse(error);
    return parsed.success && parsed.data.status === 400;
}

function go_to(hash: string): void {
    browser_history.go_to_location(hash);
}

function time_of(seconds: number): {time: string; full_time: string} {
    const date = new Date(seconds * 1000);
    return {
        time: sanji_format.format_relative(date),
        full_time: sanji_format.format_date_time(date),
    };
}

function where_label(stream_id: number | null, topic: string): string {
    if (stream_id === null) {
        return "–";
    }
    const room = stream_data.get_stream_name_from_id(stream_id);
    return topic === ""
        ? $t({defaultMessage: "# {room}"}, {room})
        : $t({defaultMessage: "# {room} › {topic}"}, {room, topic});
}

// The message that holds a result, in its channel and topic.
function result_url(
    stream_id: number | null,
    topic: string,
    message_id: number | null,
): string | undefined {
    if (stream_id === null || message_id === null) {
        return undefined;
    }
    return `${hash_util.by_stream_topic_url(stream_id, topic)}/near/${message_id}`;
}

// ----- The list -----

function row_context(routine: Routine): Record<string, unknown> {
    const external = routine.executor === "external";
    const paused = !routine.enabled;
    const last_run = routine.last_run;
    const last_status = last_run?.status ?? routine.last_status;
    const last_moment = last_run?.finished_at ?? last_run?.started_at ?? routine.last_run_at;
    const next = routine.next_run_at === null || paused ? undefined : time_of(routine.next_run_at);
    const last =
        last_moment === null || last_moment === undefined ? undefined : time_of(last_moment);
    return {
        id: routine.id,
        title: routine.title,
        external,
        paused,
        paused_label: routines_labels.paused_text(routine.paused_reason),
        agent_name: routine.agent_name === "" ? "–" : routine.agent_name,
        where: where_label(routine.stream_id, routine.topic),
        pattern: routines_labels.pattern_text(routine),
        next_run: next?.time ?? "–",
        next_full_time: next?.full_time ?? "",
        status_text: routines_labels.status_label(last_status),
        status_tone: routines_labels.status_tone(last_status),
        last_time: last?.time,
        last_full_time: last?.full_time,
        reason: last_run === null ? "" : routines_labels.reason_text(last_run.error_code),
        result_url:
            last_run === null
                ? undefined
                : result_url(routine.stream_id, routine.topic, last_run.result_message_id),
        can_edit: routine.can_edit && !external,
        busy: busy_ids.has(routine.id),
        toggle_label: paused ? $t({defaultMessage: "Resume"}) : $t({defaultMessage: "Pause"}),
        history_hash: routines_data.route_hash({kind: "history", id: routine.id}),
        edit_hash: routines_data.route_hash({kind: "edit", id: routine.id}),
    };
}

function week_context(): Record<string, unknown>[] {
    const format = new Intl.DateTimeFormat(user_settings.default_language, {
        weekday: "short",
        day: "numeric",
        month: "short",
    });
    return routines_data.week_days(items, new Date()).map((day, offset) => ({
        label: format.format(day.date),
        today: offset === 0,
        has_items: day.items.length > 0,
        items: day.items.map((item) => ({
            title: item.routine.title,
            external: item.routine.executor === "external",
            time:
                item.time === undefined
                    ? routines_labels.interval_chip_text(item.routine)
                    : routines_labels.format_clock(item.time),
            full_label: `${item.routine.title} · ${routines_labels.pattern_text(item.routine)}`,
        })),
    }));
}

function list_context(): Record<string, unknown> {
    const sorted = routines_data.sort_routines(items);
    return {
        is_guest: current_user.is_guest,
        rows: sorted.map((routine) => row_context(routine)),
        week: week_context(),
        is_empty: items.length === 0,
        loading: list_state === "loading",
        load_failed: list_state === "failed",
    };
}

function render_list_page(): void {
    $view().html(render_routines_list_page(list_context()));
}

// Draws what changes while a load ends or a routine changes. The
// header stays where it is.
function render_list_region(): void {
    if (route.kind !== "list") {
        return;
    }
    $view().find(".routines-list-region").html(render_routines_list(list_context()));
}

function load_list({quiet}: {quiet: boolean}): void {
    const load_visit = visit;
    if (!quiet) {
        list_state = "loading";
    }
    void (async () => {
        try {
            const loaded = await routines_data.fetch_routines();
            if (load_visit !== visit) {
                return;
            }
            items = loaded;
            list_state = "ready";
        } catch {
            if (load_visit !== visit || quiet) {
                return;
            }
            list_state = "failed";
        }
        render_list_region();
    })();
}

function find_routine(id: string): Routine | undefined {
    return items.find((routine) => routine.id === id);
}

function set_busy(id: string, busy: boolean): void {
    if (busy) {
        busy_ids.add(id);
    } else {
        busy_ids.delete(id);
    }
    render_list_region();
}

function show_error(error: unknown): void {
    feedback_widget.show_toast({
        text: error_message(error, $t({defaultMessage: "That did not work. Try again."})),
        variant: "error",
    });
}

function toggle_routine(id: string): void {
    const routine = find_routine(id);
    if (routine === undefined || busy_ids.has(id)) {
        return;
    }
    const enable = !routine.enabled;
    set_busy(id, true);
    void (async () => {
        try {
            const changed = await routines_data.set_enabled(id, enable);
            items = routines_data.replace_routine(items, changed);
            feedback_widget.show_toast({
                text: enable
                    ? $t({defaultMessage: "Routine resumed."})
                    : $t({defaultMessage: "Routine paused."}),
            });
        } catch (error: unknown) {
            show_error(error);
        }
        set_busy(id, false);
    })();
}

function run_routine(id: string): void {
    if (busy_ids.has(id)) {
        return;
    }
    set_busy(id, true);
    void (async () => {
        try {
            await routines_data.run_now(id);
            feedback_widget.show_toast({text: $t({defaultMessage: "The routine is running."})});
            load_list({quiet: true});
        } catch (error: unknown) {
            show_error(error);
        }
        set_busy(id, false);
    })();
}

function delete_routine(id: string): void {
    const routine = find_routine(id);
    if (routine === undefined || busy_ids.has(id)) {
        return;
    }
    confirm_dialog.launch({
        modal_title_text: $t({defaultMessage: "Delete “{title}”?"}, {title: routine.title}),
        modal_content_html: $t({
            defaultMessage:
                "The routine and its history are deleted. Messages it already posted stay in the channel.",
        }),
        modal_submit_button_text: $t({defaultMessage: "Delete"}),
        on_click() {
            set_busy(id, true);
            void (async () => {
                try {
                    await routines_data.delete_routine(id);
                    items = items.filter((item) => item.id !== id);
                    feedback_widget.show_toast({text: $t({defaultMessage: "Routine deleted."})});
                } catch (error: unknown) {
                    show_error(error);
                }
                set_busy(id, false);
            })();
        },
    });
}

// ----- The form -----

type Option = {value: string; label: string; selected: boolean};

function options_for(values: [string, string][], selected: string): Option[] {
    return values.map(([value, label]) => ({value, label, selected: value === selected}));
}

function numbered_options(from: number, to: number, selected: string, pad: boolean): Option[] {
    const values: [string, string][] = [];
    for (let number = from; number <= to; number += 1) {
        const text = pad ? String(number).padStart(2, "0") : String(number);
        values.push([text, text]);
    }
    return options_for(values, selected);
}

function stream_options(): Option[] {
    const subs = stream_data
        .get_unsorted_subs_with_content_access()
        .filter((sub) => !sub.is_archived)
        .toSorted((a, b) => a.name.localeCompare(b.name));
    const values: [string, string][] = subs.map((sub) => [
        String(sub.stream_id),
        $t({defaultMessage: "# {room}"}, {room: sub.name}),
    ]);
    // A routine keeps its channel even when the list does not have it.
    const current = Number(draft.stream_id);
    if (draft.stream_id !== "" && !subs.some((sub) => sub.stream_id === current)) {
        values.push([draft.stream_id, where_label(current, "")]);
    }
    return options_for(values, draft.stream_id);
}

function agent_options(): Option[] {
    const values: [string, string][] = agents.map((agent) => [agent.id, agent.name]);
    if (
        draft.agent_profile_id !== "" &&
        !agents.some((agent) => agent.id === draft.agent_profile_id) &&
        editing_routine !== undefined
    ) {
        values.push([draft.agent_profile_id, editing_routine.agent_name]);
    }
    return options_for(values, draft.agent_profile_id);
}

function interval_label(kind: routines_data.PatternKind): string {
    return kind === "every_minutes"
        ? $t({defaultMessage: "Minutes between runs"})
        : $t({defaultMessage: "Hours between runs"});
}

function interval_hint(kind: routines_data.PatternKind): string {
    const bounds = routines_data.interval_bounds(kind);
    return kind === "every_minutes"
        ? $t(
              {defaultMessage: "From {min} to {max} minutes. Runs count from midnight."},
              {min: bounds.min, max: bounds.max},
          )
        : $t(
              {defaultMessage: "From {min} to {max} hours. Runs count from midnight."},
              {min: bounds.min, max: bounds.max},
          );
}

function form_zone(): string {
    return routines_labels.zone_label(editing_routine?.timezone ?? default_timezone);
}

function form_context(): Record<string, unknown> {
    const creating = route.kind === "new";
    const bounds = routines_data.interval_bounds(draft.pattern_kind);
    const active_template = route.kind === "new" ? route.template_id : undefined;
    const zone = form_zone();
    return {
        title: creating
            ? $t({defaultMessage: "New routine"})
            : $t({defaultMessage: "Change routine"}),
        description: creating
            ? $t({defaultMessage: "Say what to do, when, and where to post it."})
            : $t({defaultMessage: "A change to the time counts from now."}),
        loading: form_state === "loading",
        load_failed: form_state === "failed",
        missing: form_state === "missing",
        has_templates: creating && templates.length > 0,
        templates: creating
            ? templates.map((template) => ({
                  title: template.title,
                  pattern: routines_labels.pattern_text({
                      pattern_kind: template.pattern_kind,
                      interval: null,
                      time_of_day: template.time_of_day,
                      weekdays: template.weekdays,
                      month_day: null,
                      timezone: editing_routine?.timezone ?? default_timezone,
                  }),
                  hash: routines_data.route_hash({kind: "new", template_id: template.id}),
                  active: template.id === active_template,
              }))
            : [],
        draft,
        max_title: routines_data.MAX_TITLE_LENGTH,
        max_instructions: routines_data.MAX_INSTRUCTIONS_LENGTH,
        max_topic: routines_data.MAX_TOPIC_LENGTH,
        pattern_options: options_for(
            routines_data.PATTERN_KINDS.map((kind) => [
                kind,
                routines_labels.pattern_kind_label(kind),
            ]),
            draft.pattern_kind,
        ),
        interval_label: interval_label(draft.pattern_kind),
        interval_hint: interval_hint(draft.pattern_kind),
        interval_min: bounds.min,
        interval_max: bounds.max,
        weekday_options: [0, 1, 2, 3, 4, 5, 6].map((value) => ({
            value,
            label: routines_labels.weekday_name(value),
            checked: draft.weekdays.includes(value),
        })),
        month_day_options: numbered_options(1, 31, draft.month_day, false),
        time_legend: $t({defaultMessage: "Time of day, 24-hour ({zone})"}, {zone}),
        zone,
        hour_options: numbered_options(0, 23, draft.hour, true),
        minute_options: numbered_options(0, 59, draft.minute, true),
        stream_options: stream_options(),
        agent_options: agent_options(),
        model_options: options_for(
            routines_data.MODEL_PRESETS.map((preset) => [
                preset,
                routines_labels.preset_label(preset),
            ]),
            draft.model_preset,
        ),
        saving,
        save_label: saving
            ? $t({defaultMessage: "Saving…"})
            : creating
              ? $t({defaultMessage: "Save routine"})
              : $t({defaultMessage: "Save changes"}),
        error_text,
    };
}

function render_form(): void {
    $view().html(render_routine_form_page(form_context()));
    update_pattern_fields();
    if (form_state === "ready") {
        show_field_errors();
    }
}

// Shows the fields that the pattern uses, and the words that depend on
// it, without drawing the form again, so the focus stays where it is.
function update_pattern_fields(): void {
    const kind = draft.pattern_kind;
    const $form = $view().find(".routine-form");
    $form
        .find('[data-pattern-group="interval"]')
        .prop("hidden", !routines_data.uses_interval(kind));
    $form.find('[data-pattern-group="time"]').prop("hidden", !routines_data.uses_time_of_day(kind));
    $form.find('[data-pattern-group="weekly"]').prop("hidden", kind !== "weekly");
    $form.find('[data-pattern-group="monthly"]').prop("hidden", kind !== "monthly");
    if (routines_data.uses_interval(kind)) {
        const bounds = routines_data.interval_bounds(kind);
        $form.find(".routine-interval-label").text(interval_label(kind));
        $form.find(".routine-interval-hint").text(interval_hint(kind));
        $form.find(".routine-interval-input").attr({min: bounds.min, max: bounds.max});
    }
}

function field_messages(): Record<routines_data.FieldName, string> {
    const bounds = routines_data.interval_bounds(draft.pattern_kind);
    return {
        title: $t({defaultMessage: "Enter a title."}),
        instructions: $t({defaultMessage: "Tell the agent what to do."}),
        agent: $t({defaultMessage: "Choose an agent."}),
        stream: $t({defaultMessage: "Choose a channel."}),
        topic: $t({defaultMessage: "Enter a topic."}),
        interval: $t(
            {defaultMessage: "Enter a whole number from {min} to {max}."},
            {min: bounds.min, max: bounds.max},
        ),
        weekdays: $t({defaultMessage: "Choose at least one day."}),
    };
}

const FIELD_CONTROLS: Record<routines_data.FieldName, string> = {
    title: ".routine-title-input",
    instructions: ".routine-instructions-input",
    agent: ".routine-agent-input",
    stream: ".routine-stream-input",
    topic: ".routine-topic-input",
    interval: ".routine-interval-input",
    weekdays: ".routine-weekday-input",
};

// Shows a message under each field that cannot be saved, once the
// person has tried to save, and keeps the messages in step as they type.
function show_field_errors(): void {
    const invalid = show_validation ? routines_data.invalid_fields(draft) : [];
    const messages = field_messages();
    for (const field of Object.keys(FIELD_CONTROLS) as routines_data.FieldName[]) {
        const failed = invalid.includes(field);
        const $message = $view().find(`.routine-field-error[data-field="${field}"]`);
        $message.text(failed ? messages[field] : "").prop("hidden", !failed);
        $view()
            .find(FIELD_CONTROLS[field])
            .attr("aria-invalid", failed ? "true" : "false");
    }
}

function is_dirty(): boolean {
    return visible && form_state === "ready" && routines_data.is_dirty(saved_draft, draft);
}

function form_defaults(): routines_data.FormDefaults {
    return {agent_profile_id: default_agent_id, stream_id: ""};
}

function start_form(): void {
    error_text = "";
    show_validation = false;
    saving = false;
    if (route.kind === "edit" && editing_routine !== undefined) {
        draft = routines_data.draft_from_routine(editing_routine, form_defaults());
    } else {
        const template_id = route.kind === "new" ? route.template_id : undefined;
        const template = templates.find((candidate) => candidate.id === template_id);
        draft =
            template === undefined
                ? routines_data.empty_draft(form_defaults())
                : routines_data.draft_from_template(template, form_defaults());
    }
    saved_draft = draft;
    form_state = "ready";
    render_form();
}

function load_form(): void {
    const load_visit = visit;
    const edit_id = route.kind === "edit" ? route.id : undefined;
    form_state = "loading";
    render_form();
    void (async () => {
        try {
            const [loaded_agents, loaded_templates, routine] = await Promise.all([
                routines_data.fetch_agents(),
                edit_id === undefined
                    ? routines_data.fetch_templates()
                    : Promise.resolve<Template[]>([]),
                edit_id === undefined
                    ? Promise.resolve(undefined)
                    : (find_routine(edit_id) ?? routines_data.fetch_routine(edit_id)),
            ]);
            if (load_visit !== visit) {
                return;
            }
            agents = loaded_agents.agents;
            default_agent_id = loaded_agents.default_agent_id ?? agents[0]?.id ?? "";
            default_timezone = loaded_agents.default_timezone;
            templates = loaded_templates;
            editing_routine = routine;
            if (routine !== undefined && (!routine.can_edit || routine.executor === "external")) {
                form_state = "missing";
                render_form();
                return;
            }
            start_form();
        } catch (error: unknown) {
            if (load_visit !== visit) {
                return;
            }
            form_state = is_missing(error) ? "missing" : "failed";
            render_form();
        }
    })();
}

function control_value(event: {currentTarget: unknown}): string {
    assert(event.currentTarget instanceof HTMLElement);
    assert(
        event.currentTarget instanceof HTMLInputElement ||
            event.currentTarget instanceof HTMLTextAreaElement ||
            event.currentTarget instanceof HTMLSelectElement,
    );
    return event.currentTarget.value;
}

function save(): void {
    if (saving || form_state !== "ready") {
        return;
    }
    show_validation = true;
    const invalid = routines_data.invalid_fields(draft);
    if (invalid.length > 0) {
        show_field_errors();
        const first = invalid[0];
        assert(first !== undefined);
        $view().find(FIELD_CONTROLS[first]).first().trigger("focus");
        return;
    }
    const params = routines_data.draft_to_params(draft);
    const save_visit = visit;
    const edit_id = route.kind === "edit" ? route.id : undefined;
    saving = true;
    error_text = "";
    $view()
        .find(".routine-save")
        .prop("disabled", true)
        .text($t({defaultMessage: "Saving…"}));
    $view().find(".routine-form-error").prop("hidden", true);
    void (async () => {
        try {
            const saved =
                edit_id === undefined
                    ? await routines_data.create_routine(params)
                    : await routines_data.save_routine(edit_id, params);
            if (save_visit !== visit) {
                return;
            }
            items = routines_data.replace_routine(items, saved);
            saved_draft = draft;
            saving = false;
            feedback_widget.show_toast({text: $t({defaultMessage: "Routine saved."})});
            go_to("#routines");
        } catch (error: unknown) {
            if (save_visit !== visit) {
                return;
            }
            saving = false;
            error_text = error_message(
                error,
                $t({defaultMessage: "That did not work. Try again."}),
            );
            $view()
                .find(".routine-save")
                .prop("disabled", false)
                .text(
                    edit_id === undefined
                        ? $t({defaultMessage: "Save routine"})
                        : $t({defaultMessage: "Save changes"}),
                );
            $view().find(".routine-form-error").text(error_text).prop("hidden", false);
        }
    })();
}

// Runs `action` at once when nothing would be lost, and otherwise
// after the person agrees to leave the unsaved text.
function leave_form_then(action: () => void): void {
    if (!is_dirty() || saving) {
        action();
        return;
    }
    confirm_dialog.launch({
        modal_title_text: $t({defaultMessage: "Discard your changes?"}),
        modal_content_html: $t({defaultMessage: "The routine you changed has not been saved."}),
        modal_submit_button_text: $t({defaultMessage: "Discard"}),
        modal_exit_button_text: $t({defaultMessage: "Keep editing"}),
        on_click() {
            saved_draft = draft;
            action();
        },
    });
}

// ----- The history -----

function history_context(): Record<string, unknown> {
    const routine = history_routine;
    return {
        title:
            routine === undefined
                ? $t({defaultMessage: "History"})
                : $t({defaultMessage: "History of {title}"}, {title: routine.title}),
        description:
            routine === undefined ? "" : $t({defaultMessage: "The newest 50 runs, newest first."}),
        loading: history_state === "loading",
        load_failed: history_state === "failed",
        missing: history_state === "missing",
        is_empty: runs.length === 0,
        runs: runs.map((run) => {
            const moment = time_of(run.scheduled_for);
            return {
                status_text: routines_labels.status_label(run.status),
                tone: routines_labels.status_tone(run.status),
                time: moment.time,
                full_time: moment.full_time,
                trigger: routines_labels.trigger_label(run.trigger),
                duration: routines_labels.duration_text(routines_data.run_seconds(run)),
                cost: routines_labels.cost_text(run.cost_microunits),
                reason: routines_labels.reason_text(run.error_code),
                result_url:
                    routine === undefined
                        ? undefined
                        : result_url(routine.stream_id, routine.topic, run.result_message_id),
            };
        }),
    };
}

function render_history(): void {
    $view().html(render_routine_history_page(history_context()));
}

function load_history({quiet}: {quiet: boolean}): void {
    if (route.kind !== "history") {
        return;
    }
    const id = route.id;
    const load_visit = visit;
    if (!quiet) {
        history_state = "loading";
        render_history();
    }
    void (async () => {
        try {
            const [routine, loaded_runs] = await Promise.all([
                find_routine(id) ?? routines_data.fetch_routine(id),
                routines_data.fetch_runs(id),
            ]);
            if (load_visit !== visit) {
                return;
            }
            history_routine = routine;
            runs = loaded_runs;
            history_state = "ready";
        } catch (error: unknown) {
            if (load_visit !== visit || quiet) {
                return;
            }
            history_state = is_missing(error) ? "missing" : "failed";
        }
        render_history();
    })();
}

// ----- Addresses -----

function apply_route(args: string[]): void {
    // A guest has no routines. The page says so, and asks the server for
    // nothing.
    route = current_user.is_guest ? {kind: "list"} : routines_data.parse_route(args);
    visit += 1;
    saving = false;
    switch (route.kind) {
        case "list":
            render_list_page();
            if (!current_user.is_guest) {
                load_list({quiet: list_state === "ready"});
            }
            break;
        case "new":
        case "edit":
            editing_routine = undefined;
            // A template of the same form needs no new load.
            if (route.kind === "new" && form_state === "ready" && templates.length > 0) {
                start_form();
            } else {
                load_form();
            }
            break;
        case "history":
            history_routine = undefined;
            runs = [];
            load_history({quiet: false});
            break;
    }
}

// ----- Live updates -----

function handle_event(): void {
    if (!visible) {
        return;
    }
    clearTimeout(refresh_timer);
    refresh_timer = setTimeout(() => {
        if (route.kind === "list") {
            load_list({quiet: true});
        } else if (route.kind === "history") {
            load_history({quiet: true});
        }
    }, EVENT_REFRESH_DELAY_MS);
}

// ----- Events from the page -----

function target_of(event: {currentTarget: unknown}): HTMLElement {
    assert(event.currentTarget instanceof HTMLElement);
    return event.currentTarget;
}

function routine_id_of(event: {currentTarget: unknown}): string {
    const id = target_of(event).closest<HTMLElement>(".routine-row")?.dataset["routineId"];
    assert(id !== undefined);
    return id;
}

function on_view_events(): void {
    const view = "#routines-view";

    $("body").on("click", `${view} .routines-retry`, () => {
        visit += 1;
        load_list({quiet: false});
        render_list_region();
    });
    $("body").on("click", `${view} .routine-toggle`, (event) => {
        toggle_routine(routine_id_of(event));
    });
    $("body").on("click", `${view} .routine-run`, (event) => {
        run_routine(routine_id_of(event));
    });
    $("body").on("click", `${view} .routine-delete`, (event) => {
        delete_routine(routine_id_of(event));
    });
    $("body").on("click", `${view} .routine-history-retry`, () => {
        visit += 1;
        load_history({quiet: false});
    });
    $("body").on("click", `${view} .routine-form-retry`, () => {
        visit += 1;
        load_form();
    });

    $("body").on("input", `${view} .routine-title-input`, (event) => {
        draft = {...draft, title: control_value(event)};
        show_field_errors();
    });
    $("body").on("input", `${view} .routine-instructions-input`, (event) => {
        draft = {...draft, instructions: control_value(event)};
        show_field_errors();
    });
    $("body").on("input", `${view} .routine-topic-input`, (event) => {
        draft = {...draft, topic: control_value(event)};
        show_field_errors();
    });
    $("body").on("input", `${view} .routine-interval-input`, (event) => {
        draft = {...draft, interval: control_value(event)};
        show_field_errors();
    });
    $("body").on("change", `${view} .routine-pattern-input`, (event) => {
        const kind = routines_data.PATTERN_KINDS.find((value) => value === control_value(event));
        if (kind !== undefined) {
            draft = {...draft, pattern_kind: kind};
            update_pattern_fields();
            show_field_errors();
        }
    });
    $("body").on("change", `${view} .routine-hour-input`, (event) => {
        draft = {...draft, hour: control_value(event)};
    });
    $("body").on("change", `${view} .routine-minute-input`, (event) => {
        draft = {...draft, minute: control_value(event)};
    });
    $("body").on("change", `${view} .routine-month-day-input`, (event) => {
        draft = {...draft, month_day: control_value(event)};
    });
    $("body").on("change", `${view} .routine-stream-input`, (event) => {
        draft = {...draft, stream_id: control_value(event)};
        show_field_errors();
    });
    $("body").on("change", `${view} .routine-agent-input`, (event) => {
        draft = {...draft, agent_profile_id: control_value(event)};
        show_field_errors();
    });
    $("body").on("change", `${view} .routine-model-input`, (event) => {
        draft = {...draft, model_preset: control_value(event)};
    });
    $("body").on("change", `${view} .routine-weekday-input`, () => {
        const days: number[] = [];
        for (const element of $view().find(".routine-weekday-input:checked").toArray()) {
            assert(element instanceof HTMLInputElement);
            days.push(Number(element.value));
        }
        draft = {...draft, weekdays: days};
        show_field_errors();
    });
    $("body").on("submit", `${view} .routine-form`, (event) => {
        event.preventDefault();
        save();
    });
    $("body").on("click", `${view} .routine-cancel`, () => {
        leave_form_then(() => {
            go_to("#routines");
        });
    });
    $("body").on("keydown", `${view} .routine-form`, (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            leave_form_then(() => {
                go_to("#routines");
            });
        }
    });

    // A link that leaves the form asks first when text would be lost.
    document.addEventListener(
        "click",
        (event) => {
            if (!is_dirty() || saving || !(event.target instanceof Element)) {
                return;
            }
            const href = event.target.closest("a[href]")?.getAttribute("href");
            if (!href?.startsWith("#")) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            leave_form_then(() => {
                go_to(href);
            });
        },
        true,
    );
    window.addEventListener("beforeunload", (event) => {
        if (is_dirty() && !saving) {
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
    live_updates.on("routine", handle_event);
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
    visit += 1;
    clearTimeout(refresh_timer);
    views_util.hide({$view: $view(), set_visible});
    $("#compose").removeClass("hide-closed-compose");
}

export function title(): string {
    return $t({defaultMessage: "Scheduled"});
}
