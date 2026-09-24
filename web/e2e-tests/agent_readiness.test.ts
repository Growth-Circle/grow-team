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

function channel_topic_url(scenario: Scenario): string {
    return `${REALM_URL}#narrow/channel/${scenario.stream_id}-e/topic/${encodeURIComponent(scenario.topic)}`;
}

async function open_agent_settings(page: Page): Promise<void> {
    await page.goto(`${REALM_URL}#settings/agents`);
    await page.waitForSelector("#agent-settings", {visible: true});
}

async function open_profile_detail(page: Page, profile_id: string): Promise<void> {
    await page.click('[data-agent-tab="directory"]');
    const detail_button = `[data-agent-action="profile-detail"][data-agent-id="${profile_id}"]`;
    await page.waitForSelector(detail_button, {visible: true});
    await page.click(detail_button);
    await page.waitForSelector("#agent-profile-detail", {visible: true});
}

// AS-14: a draft profile with a passing probe is ready but takes no task
// until an explicit Enable; the next mention then creates one.
async function test_readiness_gate(page: Page, scenario: Scenario): Promise<void> {
    await open_agent_settings(page);
    await open_profile_detail(page, scenario.profile_id);
    await page.waitForFunction(
        () =>
            document
                .querySelector("#agent-profile-detail")
                ?.textContent?.includes("Ready draft. Explicit enable is required."),
    );
    assert.equal(
        await page.$(`[data-agent-action="create-task"][data-agent-id="${scenario.profile_id}"]`),
        null,
        "A ready draft profile must not offer Create task",
    );
    await page.click(`[data-agent-action="profile-enable"][data-agent-id="${scenario.profile_id}"]`);
    await page.waitForFunction(
        () =>
            !document
                .querySelector("#agent-profile-detail")
                ?.textContent?.includes("Ready draft. Explicit enable is required."),
    );
    await page.waitForSelector(
        `[data-agent-action="create-task"][data-agent-id="${scenario.profile_id}"]`,
        {visible: true},
    );

    await page.goto(channel_topic_url(scenario));
    await page.waitForSelector(".message_row", {visible: true});
    await page.keyboard.press("KeyC");
    await page.waitForSelector("#compose-textarea", {visible: true});
    const bot_name = await page.evaluate(
        (id) => zulip_test.get_person_by_user_id(id).full_name,
        scenario.bot_user_id,
    );
    await common.select_item_via_typeahead(page, "#compose-textarea", `@**${bot_name}`, bot_name);
    await page.type("#compose-textarea", " Are you on now?");
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
}

// AS-01: a member with a share grant, but no pairing of their own, creates a
// task from the message actions menu on someone else's message.
async function test_member_message_menu(page: Page, scenario: Scenario): Promise<void> {
    // The selection reason needs a recent heartbeat, or it stays "the agent
    // runner did not report recently" instead of "ready to start the task".
    run_fixture(["heartbeat", "--runner", scenario.runner_id]);
    await common.log_out(page);
    await common.log_in(page, {username: scenario.member_email, password: scenario.member_password});
    await page.goto(channel_topic_url(scenario));
    const row = `.message_row[data-message-id='${scenario.source_message_id}']`;
    await page.waitForSelector(row, {visible: true});
    await page.hover(row);
    await page.click(`${row} .message-actions-menu-button`);
    await page.waitForSelector(".agent_create_task", {visible: true});
    await page.click(".agent_create_task");
    await page.waitForSelector("#agent-task-dialog", {visible: true});
    await page.waitForSelector(`#agent-task-profile option[value='${scenario.profile_id}']`);
    await page.select("#agent-task-profile", scenario.profile_id);
    await page.waitForFunction(() =>
        document.querySelector("#agent-task-status")?.textContent?.includes("ready"),
    );
    await page.type("#agent-task-request", "Please help from a shared agent.");
    const response_promise = page.waitForResponse(
        (response) =>
            response.url().endsWith("/json/agent/jobs") && response.request().method() === "POST",
    );
    await page.click("#agent-task-form button[type='submit']");
    const response = await response_promise;
    assert.equal(response.status(), 200);
    const body = z
        .object({job: z.object({id: z.string(), profile_id: z.string()})})
        .parse(await response.json());
    assert.equal(body.job.profile_id, scenario.profile_id);
    await page.waitForSelector("#agent-job-overlay", {visible: true});
}

async function test_agent_readiness(page: Page): Promise<void> {
    const scenario = start_scenario([
        "--owner",
        common.test_credentials.default_user.username,
        "--kind",
        "answer",
        "--state",
        "draft",
    ]);
    await common.log_in(page);
    await test_readiness_gate(page, scenario);
    await test_member_message_menu(page, scenario);
}

await common.run_test(test_agent_readiness);
