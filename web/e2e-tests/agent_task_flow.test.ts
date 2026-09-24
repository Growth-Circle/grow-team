import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";

import type {Page} from "puppeteer";
import * as z from "zod/mini";

import * as common from "./lib/common.ts";

const FIXTURE = "web/e2e-tests/fixtures/agent_fake_runner.py";
const REALM_URL = "http://zulip.zulipdev.com:9981/";

function run_fixture(args: string[]): unknown {
    return JSON.parse(execFileSync("python3", [FIXTURE, ...args], {encoding: "utf8"}));
}

const scenario_schema = z.object({
    runner_id: z.string(),
    profile_id: z.string(),
    bot_user_id: z.number(),
    stream_id: z.number(),
    topic: z.string(),
    source_message_id: z.number(),
    member_email: z.string(),
    member_password: z.string(),
});
type Scenario = z.infer<typeof scenario_schema>;

function start_scenario(args: string[]): Scenario {
    return scenario_schema.parse(run_fixture(["scenario", ...args]));
}

const claim_schema = z.object({job_id: z.nullable(z.string())});
const approval_schema = z.object({operation_id: z.string(), approval_id: z.string()});
const lose_lease_schema = z.object({status: z.string(), reason_code: z.string()});

async function open_channel_compose(page: Page, scenario: Scenario): Promise<void> {
    await page.goto(
        `${REALM_URL}#narrow/channel/${scenario.stream_id}-e/topic/${encodeURIComponent(scenario.topic)}`,
    );
    await page.waitForSelector(".message_row", {visible: true});
    await page.keyboard.press("KeyC");
    await page.waitForSelector("#compose-textarea", {visible: true});
}

// Mentions the scenario's bot in the open compose box, sends, and returns
// the job ID from the dispatch receipt banner (contract 12.1, existing text).
async function mention_and_send(page: Page, bot_user_id: number, request: string): Promise<string> {
    const bot_name = await page.evaluate(
        (id) => zulip_test.get_person_by_user_id(id).full_name,
        bot_user_id,
    );
    await common.select_item_via_typeahead(page, "#compose-textarea", `@**${bot_name}`, bot_name);
    await page.type("#compose-textarea", ` ${request}`);
    await page.click("#compose-send-button");
    const banner = "#compose_banners .agent_task_receipt_banner";
    await page.waitForSelector(banner, {visible: true});
    await page.waitForFunction(
        (selector) =>
            document.querySelector(`${selector} .agent-dispatch-receipt-outcome`)?.textContent ===
            "This agent started a task.",
        {},
        banner,
    );
    const job_url = await page.$eval(`${banner} a`, (node) => node.getAttribute("href"));
    assert.ok(job_url, "The dispatch receipt needs a task link");
    return job_url!.replace("#agent-jobs/", "");
}

async function open_job(page: Page, job_id: string): Promise<void> {
    await page.evaluate((id) => {
        window.location.hash = `#agent-jobs/${id}`;
    }, job_id);
    await page.waitForSelector("#agent-job-overlay", {visible: true});
    await page.waitForFunction(
        (id) => document.querySelector("#agent-job-heading")?.getAttribute("title") === id,
        {},
        job_id,
    );
}

async function wait_for_status(page: Page, text: string): Promise<void> {
    await page.waitForFunction(
        (expected) => document.querySelector("#agent-job-status")?.textContent?.includes(expected),
        {timeout: 20000},
        text,
    );
}

async function wait_for_state(page: Page, text: string): Promise<void> {
    await page.waitForFunction(
        (expected) => document.querySelector("#agent-job-state")?.textContent === expected,
        {timeout: 20000},
        text,
    );
}

// One server lock serializes every agent job action (agent_context.py's
// pg_try_advisory_xact_lock); a busy server answers 503 and the write does
// not auto-retry the way a read does (agent_api.ts). Retry the click itself,
// the way a person would click the button again.
async function click_job_action(page: Page, selector: string): Promise<void> {
    for (const delay_ms of [0, 500, 1000, 2000, 4000]) {
        if (delay_ms) {
            await new Promise((resolve) => setTimeout(resolve, delay_ms));
        }
        await page.click(selector);
    }
}

// AT-36: mention, claim, start, Request stop, stop, then Stopped.
async function test_cancel_flow(page: Page, scenario: Scenario): Promise<void> {
    await open_channel_compose(page, scenario);
    const job_id = await mention_and_send(page, scenario.bot_user_id, "Please give a short answer.");
    const claimed = claim_schema.parse(run_fixture(["claim", "--runner", scenario.runner_id]));
    assert.equal(claimed.job_id, job_id, "The fake runner must claim the mentioned task");
    run_fixture(["start", "--job", job_id]);
    await open_job(page, job_id);
    await wait_for_status(page, "The agent works on this task now.");
    await click_job_action(page, "[data-job-action='cancel']");
    await wait_for_status(page, "Your stop request went to the agent.");
    run_fixture(["stop", "--job", job_id]);
    await wait_for_state(page, "Stopped");
    await wait_for_status(page, "You stopped this task.");
}

// AT-15 and AT-36: lose the lease, see Resume, resume, claim, finish, Finished.
async function test_lease_lost_resume_flow(page: Page, scenario: Scenario): Promise<void> {
    await open_channel_compose(page, scenario);
    const job_id = await mention_and_send(page, scenario.bot_user_id, "Please give another answer.");
    run_fixture(["claim", "--runner", scenario.runner_id]);
    const lost = lose_lease_schema.parse(run_fixture(["lose-lease", "--job", job_id]));
    assert.equal(lost.status, "interrupted");
    assert.equal(lost.reason_code, "lease_lost");
    await open_job(page, job_id);
    await wait_for_status(
        page,
        "The device stopped responding. Resume the task, or create a new task.",
    );
    await click_job_action(page, "[data-job-action='resume']");
    await wait_for_status(page, "This task waits for a free runner.");
    const reclaimed = claim_schema.parse(run_fixture(["claim", "--runner", scenario.runner_id]));
    assert.equal(reclaimed.job_id, job_id, "Resume must queue the same task for the runner");
    run_fixture(["start", "--job", job_id]);
    run_fixture(["finish", "--job", job_id, "--summary", "Here is the answer."]);
    await wait_for_state(page, "Finished");
    await wait_for_status(page, "This task finished. Read the artifacts and diff above.");
}

// AF-36: close the page before finish, then see Finished and the result from
// a second browser context. `page` stays open for the rest of this file and
// its `run_test` cleanup, so the throwaway tab is the one that closes.
async function test_closed_tab_flow(page: Page, scenario: Scenario): Promise<void> {
    const first_page = await common.get_page();
    try {
        await common.log_in(first_page);
        await open_channel_compose(first_page, scenario);
        const job_id = await mention_and_send(
            first_page,
            scenario.bot_user_id,
            "Please answer while I step away.",
        );
        run_fixture(["claim", "--runner", scenario.runner_id]);
        run_fixture(["start", "--job", job_id]);
        await open_job(first_page, job_id);
        await wait_for_status(first_page, "The agent works on this task now.");
        await first_page.close();
        run_fixture(["finish", "--job", job_id, "--summary", "Finished while the tab was closed."]);
        await open_job(page, job_id);
        await wait_for_state(page, "Finished");
        await page.waitForSelector("#agent-job-artifacts a", {visible: true});
    } finally {
        if (!first_page.isClosed()) {
            await first_page.close();
        }
    }
}

// AT-36 and AD flows: a manage job's approval shows the summary, the
// requester approves, execute and finish run, and the reply lists the step.
async function test_manage_approval_flow(page: Page, scenario: Scenario): Promise<void> {
    await open_channel_compose(page, scenario);
    const job_id = await mention_and_send(
        page,
        scenario.bot_user_id,
        "Please unsubscribe me, I am stepping back from this channel.",
    );
    run_fixture(["claim", "--runner", scenario.runner_id]);
    run_fixture(["start", "--job", job_id]);
    const approval = approval_schema.parse(run_fixture(["approval", "--job", job_id]));
    await open_job(page, job_id);
    await wait_for_status(page, "The agent waits for your decision on an operation.");
    await page.waitForFunction(() =>
        document
            .querySelector("#agent-job-operations .agent-job-operation-title")
            ?.textContent?.includes("Unsubscribe"),
    );
    await click_job_action(page, "[data-job-action='approve']");
    await wait_for_status(page, "The agent works on this task now.");
    run_fixture(["execute", "--job", job_id, "--operation", approval.operation_id]);
    run_fixture(["finish", "--job", job_id, "--summary", "Removed the member as requested."]);
    await wait_for_state(page, "Finished");
    await open_channel_compose(page, scenario);
    await page.waitForFunction(
        () => {
            const rows = [...document.querySelectorAll(".message_row .message_content")];
            return rows.some((row) => {
                const text = row.textContent ?? "";
                return text.includes("Steps taken:") && text.includes("Unsubscribed");
            });
        },
        {timeout: 20000},
    );
}

async function test_agent_task_flow(page: Page): Promise<void> {
    await common.log_in(page);
    const answer_scenario = start_scenario([
        "--owner",
        common.test_credentials.default_user.username,
        "--kind",
        "answer",
    ]);
    await test_cancel_flow(page, answer_scenario);
    await test_lease_lost_resume_flow(page, answer_scenario);
    await test_closed_tab_flow(page, answer_scenario);

    const manage_scenario = start_scenario([
        "--owner",
        "iago@zulip.com",
        "--member",
        common.test_credentials.default_user.username,
        "--kind",
        "manage",
    ]);
    await test_manage_approval_flow(page, manage_scenario);
}

await common.run_test(test_agent_task_flow);
