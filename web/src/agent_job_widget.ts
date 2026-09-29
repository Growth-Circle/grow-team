import $ from "jquery";
import assert from "minimalistic-assert";

import render_agent_job_card from "../templates/widgets/agent_job_card.hbs";

import type {AgentJobCardArtifact, AgentJobCardData} from "./agent_job_data.ts";
import {agent_job_card_data_schema} from "./agent_job_data.ts";
import {bucket_for_status, card_step_text, pill_text} from "./agent_job_labels.ts";
import * as browser_history from "./browser_history.ts";
import * as channel from "./channel.ts";
import * as feedback_widget from "./feedback_widget.ts";
import {$t} from "./i18n.ts";
import type {Message} from "./message_store.ts";
import type {Event} from "./widget_data.ts";
import type {AnyWidgetData, WidgetData, WidgetOutboundData} from "./widget_schema.ts";

/*
    The job card of an agent message (spec 13, RM-35..47). The server
    posts one card for each request, and it sends the full state of the
    card as a submessage each time the job changes. This widget draws
    the last state.

    "Details" opens the job drawer at #agent-jobs/<job id>. "Try again"
    resumes a stopped job. Both use `channel` directly, so this module
    does not depend on agent_api.ts.
*/

const CHIP_TINTS = new Map([
    ["pr", "ayame"],
    ["file", "ayame"],
    ["check", "matcha"],
    ["task", "kuning"],
]);

// A chip opens an agent file, a page on the web, or a task. The server
// checks the link when it makes the card, and this check repeats it.
function chip_href(artifact: AgentJobCardArtifact): string | undefined {
    if (artifact.task_id !== null) {
        return `#tasks/${artifact.task_id}`;
    }
    if (
        artifact.url !== null &&
        (artifact.url.startsWith("/json/agent/artifacts/") || artifact.url.startsWith("https://"))
    ) {
        return artifact.url;
    }
    return undefined;
}

export function build_template_data(card: AgentJobCardData): Record<string, unknown> {
    const bucket = bucket_for_status(card.status);
    const show_chips = bucket === "review" || bucket === "done";
    const pill = pill_text(card.status);
    return {
        job_id: card.job_id,
        bucket,
        pill_text: pill,
        title: card.title,
        aria_label: `${card.title} · ${pill}`,
        show_bar: bucket === "queued" || bucket === "working",
        bar_percent: Math.max(0, Math.min(100, Math.round(card.progress * 100))),
        step_text: card_step_text(card),
        chips: show_chips
            ? card.artifacts.map((artifact) => ({
                  label: artifact.label,
                  href: chip_href(artifact),
                  external: artifact.url?.startsWith("https://") === true,
                  tint: CHIP_TINTS.get(artifact.kind) ?? "kuning",
              }))
            : [],
        show_retry: bucket === "stopped" && card.can_retry,
    };
}

// Reads the version of the job, then asks the server to resume it. A
// stale version is refused, so the read comes first.
async function resume_job(job_id: string): Promise<void> {
    const detail_xhr = channel.get({url: `/json/agent/jobs/${job_id}`});
    const detail: unknown = await detail_xhr;
    const job =
        typeof detail === "object" && detail !== null && "job" in detail ? detail.job : undefined;
    const expected_version =
        typeof job === "object" && job !== null && "version" in job ? job.version : undefined;
    if (typeof expected_version !== "number") {
        throw new TypeError("The job has no version.");
    }
    const resume_xhr = channel.post({
        url: `/json/agent/jobs/${job_id}/resume`,
        data: {payload: JSON.stringify({schema_version: 1, expected_version})},
    });
    await resume_xhr;
    // The server sends a new card state when the job is queued again.
    // The widget draws it then, so nothing changes here.
}

async function retry(job_id: string, $button: JQuery): Promise<void> {
    const $foot = $button.closest(".sj-job-card__foot");
    $button.prop("disabled", true).attr("aria-busy", "true");
    $foot.addClass("sj-skeleton");
    try {
        await resume_job(job_id);
    } catch {
        // Put the card back as it was, and offer the retry again.
        $button.prop("disabled", false).removeAttr("aria-busy");
        $foot.removeClass("sj-skeleton");
        feedback_widget.show_toast({
            text: $t({defaultMessage: "Could not start the job again."}),
            variant: "error",
            on_retry() {
                void retry(job_id, $button);
            },
        });
    }
}

// Before the first draft, the server writes one line "{name} · {state}"
// as the message text, for clients that cannot draw a card. This client
// draws the card, so it hides that line. A real answer stays.
export function is_fallback_line(text: string, sender_full_name: string): boolean {
    const line = text.trim();
    return line.startsWith(`${sender_full_name} · `) && !line.includes("\n");
}

export function activate({any_data}: {message: Message; any_data: AnyWidgetData}): {
    inbound_events_handler: (events: Event[]) => void;
    widget_data: WidgetData;
} {
    assert(any_data.widget_type === "agent_job");
    const widget_data: WidgetData = {widget_type: "agent_job", data: any_data.extra_data};
    const inbound_events_handler = (events: Event[]): void => {
        for (const event of events) {
            const parsed = agent_job_card_data_schema.safeParse(event.data);
            if (
                parsed.success &&
                widget_data.widget_type === "agent_job" &&
                parsed.data.job_id === widget_data.data.job_id
            ) {
                widget_data.data = parsed.data;
            }
        }
    };
    return {inbound_events_handler, widget_data};
}

export function render({
    $elem,
    message,
    widget_data,
}: {
    $elem: JQuery;
    callback: (data: WidgetOutboundData) => void;
    message: Message;
    widget_data: WidgetData;
    rerender: boolean;
}): void {
    assert(widget_data.widget_type === "agent_job");
    const card = widget_data.data;
    // The server can send the same state again. A new card would replace
    // the button under the pointer, and a click that spans it would be lost.
    const state = JSON.stringify(card);
    if ($elem.attr("data-card-state") === state && $elem.find(".sj-job-card").length > 0) {
        return;
    }
    $elem.attr("data-card-state", state);
    $elem.empty().append($(render_agent_job_card(build_template_data(card))));
    const $text = $elem.siblings();
    $text.toggleClass(
        "sj-job-card-fallback-text",
        is_fallback_line($text.text(), message.sender_full_name),
    );
    // A click on a message starts a reply to it. A click on a control of
    // the card must not, and it must not undo the new address.
    $elem.find(".sj-job-card__detail").on("click", (event) => {
        event.stopPropagation();
        browser_history.go_to_location(`#agent-jobs/${card.job_id}`);
    });
    $elem.find(".sj-job-card__retry").on("click", (event) => {
        event.stopPropagation();
        void retry(card.job_id, $(event.currentTarget));
    });
    $elem.find(".sj-job-card__chip").on("click", (event) => {
        event.stopPropagation();
    });
}
