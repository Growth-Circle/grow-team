/* eslint-disable no-jquery/no-parse-html-literal, @typescript-eslint/consistent-type-assertions -- Rows are static jQuery markup, and the filter comes from one of the tab buttons' own data attribute. */
import $ from "jquery";

import render_job_list from "../templates/agent/job_list.hbs";

import * as api from "./agent_api.ts";
import {job_hash} from "./agent_job_panel.ts";
import {job_status_label} from "./agent_ui_state.ts";
import * as browser_history from "./browser_history.ts";
import {$t} from "./i18n.ts";
import * as overlays from "./overlays.ts";

type Filter = "mine" | "waiting" | "running";

let current_filter: Filter = "mine";
let visit = 0;
let handlers_bound = false;

// The list falls back to a title derived the same way the server does,
// so a row reads well even against a response from before the
// team-backend release lane ships the title field.
function row_title(job: api.AgentJob): string {
    return job.title ?? job.request.slice(0, 80);
}

function render_rows(jobs: api.AgentJob[]): void {
    const $rows = $("#agent-job-list-rows").empty();
    $("#agent-job-list-empty").prop("hidden", jobs.length > 0);
    for (const job of jobs) {
        const $row = $("<a class='agent-job-row'>")
            .attr("href", job_hash(job.id))
            .attr("data-agent-job-id", job.id)
            .toggleClass("needs-action", job.needs_my_action === true)
            .appendTo($rows);
        $("<i class='agent-job-row-icon zulip-icon zulip-icon-bot' aria-hidden='true'>").appendTo(
            $row,
        );
        $("<span class='agent-job-row-text'>").text(row_title(job)).appendTo($row);
        $("<span class='agent-job-row-meta'>").text(job_status_label(job.status)).appendTo($row);
    }
}

function select_filter(filter: Filter): void {
    current_filter = filter;
    $("#agent-job-list-tabs [data-agent-job-view]")
        .attr("aria-current", "false")
        .removeClass("selected");
    $(`#agent-job-list-tabs [data-agent-job-view='${filter}']`)
        .attr("aria-current", "page")
        .addClass("selected");
}

async function load(token: number): Promise<void> {
    $("#agent-job-list-status").text($t({defaultMessage: "Loading tasks…"}));
    try {
        const result = await api.list_jobs(0, current_filter);
        if (token !== visit) {
            return;
        }
        $("#agent-job-list-status").text("");
        render_rows(result.jobs);
    } catch {
        if (token !== visit) {
            return;
        }
        $("#agent-job-list-status").text($t({defaultMessage: "Task list is unavailable. Retry."}));
        render_rows([]);
    }
}

function bind(): void {
    if (handlers_bound) {
        return;
    }
    handlers_bound = true;
    $("body").on("click", "#agent-job-list-tabs [data-agent-job-view]", function () {
        const filter = $(this).attr("data-agent-job-view") as Filter;
        if (filter === current_filter) {
            return;
        }
        select_filter(filter);
        visit += 1;
        void load(visit);
    });
}

export let open = (): void => {
    if ($("#agent-job-list-overlay").length === 0) {
        $("body").append($(render_job_list({})));
    }
    bind();
    select_filter("mine");
    overlays.open_overlay({
        name: "agent-jobs-list",
        $overlay: $("#agent-job-list-overlay"),
        on_close() {
            browser_history.exit_overlay();
        },
    });
    visit += 1;
    void load(visit);
    $("#agent-job-list-heading").trigger("focus");
};

export function rewire_open(value: typeof open): void {
    open = value;
}
