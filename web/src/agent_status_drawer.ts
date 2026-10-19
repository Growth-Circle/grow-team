import $ from "jquery";

import render_agent_status_drawer from "../templates/agent_status_drawer.hbs";

import * as agent_status_data from "./agent_status_data.ts";
import type {AgentStatusItem, SectionKey} from "./agent_status_data.ts";
import * as agent_ui_state from "./agent_ui_state.ts";
import * as drawer from "./drawer.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as sanji_format from "./sanji_format.ts";

/*

The Status button of a 1:1 direct message with an agent opens this
drawer: what the agent is working on, what waits on the user, and what
the user decided in the last 7 days. Each item links to its job. The
sections follow `agent_job` events while the drawer is open.

*/

// True from a click on Try again until the answer, so the drawer shows
// that it is loading again.
let retrying = false;

function section_title(key: SectionKey): string {
    switch (key) {
        case "in_progress":
            return $t({defaultMessage: "In progress"});
        case "waiting_on_you":
            return $t({defaultMessage: "Waiting on you"});
        case "decided":
            return $t({defaultMessage: "Decided"});
    }
}

function empty_text(key: SectionKey): string {
    switch (key) {
        case "in_progress":
            return $t({defaultMessage: "Nothing in progress."});
        case "waiting_on_you":
            return $t({defaultMessage: "Nothing is waiting on you."});
        case "decided":
            return $t({defaultMessage: "You decided nothing in the last 7 days."});
    }
}

function outcome_label(outcome: string): string {
    switch (outcome) {
        case "approved":
            return $t({defaultMessage: "Approved"});
        case "rejected":
            return $t({defaultMessage: "Rejected"});
        default:
            return $t({defaultMessage: "Answered"});
    }
}

function message_href(item: AgentStatusItem): string {
    if (item.stream_id === null || item.message_id === null) {
        return "";
    }
    return hash_util.search_terms_to_hash([
        {operator: "channel", operand: String(item.stream_id)},
        ...(item.topic === null ? [] : [{operator: "topic" as const, operand: item.topic}]),
        {operator: "near", operand: String(item.message_id)},
    ]);
}

function build_row(item: AgentStatusItem): {
    title: string;
    meta: string;
    detail: string;
    job_href: string;
    message_href: string;
} {
    const state_label =
        item.outcome === null
            ? agent_ui_state.job_status_label(item.status ?? "")
            : outcome_label(item.outcome);
    const job_id = agent_status_data.job_link_id(item);
    return {
        title: item.title,
        meta: `${state_label} · ${sanji_format.format_relative(new Date(item.time))}`,
        detail: item.outcome === "answered" && item.detail !== null ? item.detail : "",
        job_href: job_id === undefined ? "" : `#agent-jobs/${job_id}`,
        message_href: message_href(item),
    };
}

function render($root: JQuery, profile_id: string): void {
    const status = agent_status_data.get_status(profile_id);
    const failed = agent_status_data.has_load_failed(profile_id) && !retrying;
    $root.html(
        render_agent_status_drawer({
            failed,
            loading: !failed && (status === undefined || retrying),
            sections: agent_status_data.SECTION_KEYS.map((key) => {
                const rows = (status?.[key] ?? []).map((item) => build_row(item));
                return {
                    key,
                    title: section_title(key),
                    empty_text: empty_text(key),
                    count: rows.length,
                    has_rows: rows.length > 0,
                    rows,
                };
            }),
        }),
    );
}

export function open(profile_id: string, agent_name: string): void {
    // The load that opens the drawer replaces a failure from before.
    retrying = agent_status_data.has_load_failed(profile_id);
    const $root = $("<div>").addClass("sj-agent-status");

    $root.on("click", ".sj-agent-status__link, .sj-agent-status__message-link", () => {
        drawer.close();
    });
    $root.on("click", ".sj-agent-status__retry", () => {
        retrying = true;
        render($root, profile_id);
        agent_status_data.load(profile_id);
    });

    const stop_listening = agent_status_data.on_change((changed_profile_id) => {
        if (changed_profile_id === profile_id) {
            retrying = false;
            render($root, profile_id);
        }
    });

    render($root, profile_id);
    drawer.open({
        kind_label: $t({defaultMessage: "STATUS · {agent_name}"}, {agent_name}),
        $body: $root,
        extra_class: "sj-agent-status-drawer",
        on_close: stop_listening,
    });
    agent_status_data.load(profile_id);
}
