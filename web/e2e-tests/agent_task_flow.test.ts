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
// The runner sends a heartbeat first. A runner that is not online makes the
// receipt say that the task starts when the device connects.
async function mention_and_send(page: Page, scenario: Scenario, request: string): Promise<string> {
    run_fixture(["heartbeat", "--runner", scenario.runner_id]);
    // The page keeps the banner of the last request. Remove it, so that
    // this request waits for its own banner and reads its own task link.
    await page.evaluate(() => {
        for (const old_banner of document.querySelectorAll(
            "#compose_banners .agent_task_receipt_banner",
        )) {
            old_banner.remove();
        }
    });
    const bot_name = await page.evaluate(
        (id) => zulip_test.get_person_by_user_id(id).full_name,
        scenario.bot_user_id,
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
    return job_url.replace("#agent-jobs/", "");
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
        // A button that is gone means that the click worked.
        if ((await page.$(selector)) === null) {
            return;
        }
        await page.click(selector);
    }
}

// AT-36: mention, claim, start, Pause job, stop, then Paused.
async function test_cancel_flow(page: Page, scenario: Scenario): Promise<void> {
    await open_channel_compose(page, scenario);
    const job_id = await mention_and_send(page, scenario, "Please give a short answer.");
    const claimed = claim_schema.parse(run_fixture(["claim", "--runner", scenario.runner_id]));
    assert.equal(claimed.job_id, job_id, "The fake runner must claim the mentioned task");
    run_fixture(["start", "--job", job_id]);
    await open_job(page, job_id);
    await wait_for_status(page, "The agent works on this task now.");
    await click_job_action(page, "[data-job-action='cancel']");
    await wait_for_status(page, "Your stop request went to the agent.");
    run_fixture(["stop", "--job", job_id]);
    await wait_for_state(page, "Paused");
    await wait_for_status(page, "You stopped this task.");
}

// AT-15: lose the lease. The job is interrupted. The drawer offers no
// resume before the device confirms that it stopped.
async function test_lease_lost_flow(page: Page, scenario: Scenario): Promise<void> {
    await open_channel_compose(page, scenario);
    const job_id = await mention_and_send(page, scenario, "Please give another answer.");
    const claimed = claim_schema.parse(run_fixture(["claim", "--runner", scenario.runner_id]));
    assert.equal(claimed.job_id, job_id, "The fake runner must claim the mentioned task");
    const lost = lose_lease_schema.parse(run_fixture(["lose-lease", "--job", job_id]));
    assert.equal(lost.status, "interrupted");
    assert.equal(lost.reason_code, "lease_lost");
    await open_job(page, job_id);
    await wait_for_status(page, "The device stopped responding. Create a new task.");
    assert.equal(await page.$("[data-job-action='resume']"), null);
    // The card in the room says the same in short, and offers no retry.
    await page.keyboard.press("Escape");
    await page.waitForSelector("#agent-job-overlay", {hidden: true});
    await wait_for_pill(page, job_id, "STOPPED");
    assert.equal(
        await common.get_text_from_selector(
            page,
            `.sj-job-card[data-job-id='${job_id}'] .sj-job-card__step`,
        ),
        "Stopped because the owner's device is offline.",
    );
    assert.equal(await page.$(`.sj-job-card[data-job-id='${job_id}'] .sj-job-card__retry`), null);
}

// AF-36: leave the page before finish, then open the job again and see
// Finished and the result. The tab goes to a blank page, so the app stops.
async function test_closed_tab_flow(page: Page, scenario: Scenario): Promise<void> {
    await open_channel_compose(page, scenario);
    const job_id = await mention_and_send(page, scenario, "Please answer while I step away.");
    run_fixture(["claim", "--runner", scenario.runner_id]);
    run_fixture(["start", "--job", job_id]);
    await open_job(page, job_id);
    await wait_for_status(page, "The agent works on this task now.");
    await page.goto("about:blank");
    run_fixture(["finish", "--job", job_id, "--summary", "Finished while the tab was closed."]);
    await page.goto(`${REALM_URL}#agent-jobs/${job_id}`);
    await page.waitForSelector("#agent-job-overlay", {visible: true});
    await wait_for_state(page, "Finished");
    await page.waitForSelector("#agent-job-artifacts a", {visible: true});
}

// AT-36 and AD flows: a manage job's approval shows the summary, the
// requester approves, execute and finish run, and the reply lists the step.
async function test_manage_approval_flow(page: Page, scenario: Scenario): Promise<void> {
    // The scenario sets a new password for its member, which ends the
    // session of the page. Log in again with that password.
    await common.log_in(page, {
        username: scenario.member_email,
        password: scenario.member_password,
    });
    await open_channel_compose(page, scenario);
    const job_id = await mention_and_send(
        page,
        scenario,
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
    // The step unsubscribed the member, so the member cannot read the reply
    // in the room any more. The drawer still lists the step that ran.
    await page.waitForFunction(() =>
        document
            .querySelector("#agent-job-operations .agent-job-operation-title")
            ?.textContent?.includes("Unsubscribe"),
    );
}

type Box = {x: number; y: number; width: number; height: number};

async function box_of(page: Page, selector: string): Promise<Box> {
    return await page.$eval(selector, (node) => {
        const rect = node.getBoundingClientRect();
        return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
    });
}

function assert_close(actual: number, expected: number, tolerance: number, what: string): void {
    assert.ok(
        Math.abs(actual - expected) <= tolerance,
        `${what}: expected ${expected} (+/- ${tolerance}), got ${actual}`,
    );
}

async function wait_for_pill(page: Page, job_id: string, text: string): Promise<void> {
    await page.waitForFunction(
        (id, expected) =>
            document.querySelector(`.sj-job-card[data-job-id='${id}'] .sj-job-card__pill`)
                ?.textContent === expected,
        {timeout: 20000},
        job_id,
        text,
    );
}

// Spec 13: one card per request in the room, updated in place. The sizes
// are the ones of the Ruang mockup at 1600 by 1000 (7.9).
async function test_card_and_drawer(page: Page, scenario: Scenario): Promise<void> {
    await page.setViewport({width: 1600, height: 1000});
    await open_channel_compose(page, scenario);
    const job_id = await mention_and_send(page, scenario, "Please answer with a card.");
    const card = `.sj-job-card[data-job-id='${job_id}']`;
    await page.waitForSelector(card, {visible: true});
    await page.evaluate(async () => {
        await document.fonts.ready;
    });

    // RM-37: the request waits. The line "{name} · {state}" that older
    // clients show is hidden, so the message is only the card.
    await wait_for_pill(page, job_id, "QUEUED");
    const fallback_hidden = await page.$eval(
        `.message_row:has(${card}) .message_content`,
        (content) =>
            [...content.children]
                .filter((child) => !child.classList.contains("widget-content"))
                .every((child) => window.getComputedStyle(child).display === "none"),
    );
    assert.ok(fallback_hidden, "The fallback text is hidden while the card shows");

    const claimed = claim_schema.parse(run_fixture(["claim", "--runner", scenario.runner_id]));
    assert.equal(claimed.job_id, job_id);
    run_fixture(["start", "--job", job_id]);
    await wait_for_pill(page, job_id, "WORKING");
    const working = await box_of(page, card);
    const pill = await box_of(page, `${card} .sj-job-card__pill`);
    const foot = await box_of(page, `${card} .sj-job-card__foot`);
    console.log("card measures", JSON.stringify({working, pill, foot}));
    assert.ok(working.width >= 368 - 2 && working.width <= 522 + 2, `card width ${working.width}`);
    // A card without chips has a footer of 31px. The 40px and 45px of the
    // mockup are the footers with a chip and with the retry button.
    assert_close(working.height, 79, 3, "card height with the bar and no chip");
    assert_close(pill.height, 21, 2, "pill height");
    assert_close(foot.height, 31, 3, "card footer height without a chip");
    assert.ok(await page.$(`${card} .sj-job-card__bar`), "A running card has a bar");
    await common.screenshot(page, "job-card-working");

    // 13-P4 and RM-40: "Details" opens the drawer of this job.
    // The card is drawn again when a new state arrives, and a click that
    // spans a redraw is lost. This click is one event on the button.
    await page.$eval(`${card} .sj-job-card__detail`, (button) => {
        if (button instanceof HTMLElement) {
            button.click();
        }
    });
    await page.waitForSelector("#agent-job-overlay.show", {visible: true});
    await page.waitForFunction(
        (id) => document.querySelector("#agent-job-heading")?.getAttribute("title") === id,
        {},
        job_id,
    );
    assert.ok((await common.page_url_with_fragment(page)).endsWith(`#agent-jobs/${job_id}`));
    assert.equal(await common.get_text_from_selector(page, ".sj-job-drawer__kind"), "AGENT JOB");
    const panel = await box_of(page, ".sj-job-drawer__panel");
    console.log("drawer measures", JSON.stringify({panel}));
    assert_close(panel.width, 482, 2, "drawer width");
    assert_close(panel.x, 1600 - 482, 2, "drawer x");
    assert_close(panel.height, 1000, 2, "drawer height");
    assert.equal(await page.$$eval(".sj-job-drawer__step", (items) => items.length), 5);
    await page.waitForSelector(".sj-job-drawer__log-line", {visible: true});
    // The device name is for the owner and the admin. This drawer names none.
    assert.doesNotMatch(
        await page.$eval("#agent-job-overlay", (node) => node.textContent ?? ""),
        /LAPTOP|lease_lost|runner_offline/,
    );
    await common.screenshot(page, "job-drawer-1600");

    // The dark theme and a phone: the drawer fills the width of the phone.
    await page.evaluate(() => {
        document.documentElement.classList.add("dark-theme");
    });
    await common.screenshot(page, "job-drawer-1600-dark");
    await page.setViewport({width: 390, height: 844});
    assert_close(
        (await box_of(page, ".sj-job-drawer__panel")).width,
        390,
        2,
        "drawer width on a phone",
    );
    await common.screenshot(page, "job-drawer-390-dark");
    await page.setViewport({width: 1600, height: 1000});
    await page.evaluate(() => {
        document.documentElement.classList.remove("dark-theme");
    });

    // T-11: "Pause job" stops the job at the end of its step.
    await wait_for_status(page, "The agent works on this task now.");
    await click_job_action(page, "[data-job-action='cancel']");
    await wait_for_status(page, "Your stop request went to the agent.");
    run_fixture(["heartbeat", "--runner", scenario.runner_id]);
    run_fixture(["stop", "--job", job_id]);
    await wait_for_state(page, "Paused");
    await page.keyboard.press("Escape");
    await page.waitForSelector("#agent-job-overlay", {hidden: true});

    // RM-47: a stopped card says why, without a device name, and offers
    // "Try again".
    await wait_for_pill(page, job_id, "STOPPED");
    const step = await common.get_text_from_selector(page, `${card} .sj-job-card__step`);
    assert.equal(step, "Paused. Open the details to continue.");
    const retry = `${card} .sj-job-card__retry`;
    await page.waitForSelector(retry, {visible: true});
    assert.equal(await page.$(`${card} .sj-job-card__bar`), null, "A stopped card has no bar");
    const retry_box = await box_of(page, retry);
    const stopped = await box_of(page, card);
    const stopped_foot = await box_of(page, `${card} .sj-job-card__foot`);
    console.log("stopped card measures", JSON.stringify({retry_box, stopped, stopped_foot}));
    assert_close(retry_box.height, 28, 3, "retry button height");
    assert_close(stopped_foot.height, 45, 3, "card footer height with the button");
    assert_close(stopped.height, 88, 3, "card height with the retry button and no bar");
    await common.screenshot(page, "job-card-stopped");
    await page.evaluate(() => {
        document.documentElement.classList.add("dark-theme");
    });
    await common.screenshot(page, "job-card-stopped-dark");
    await page.setViewport({width: 390, height: 844});
    await common.screenshot(page, "job-card-stopped-390-dark");
    await page.setViewport({width: 1600, height: 1000});
    await page.evaluate(() => {
        document.documentElement.classList.remove("dark-theme");
    });

    // "Try again" starts the same job again, and the same card follows it.
    run_fixture(["heartbeat", "--runner", scenario.runner_id]);
    await page.$eval(retry, (button) => {
        if (button instanceof HTMLElement) {
            button.click();
        }
    });
    await wait_for_pill(page, job_id, "QUEUED");
    const reclaimed = claim_schema.parse(run_fixture(["claim", "--runner", scenario.runner_id]));
    assert.equal(reclaimed.job_id, job_id);
    run_fixture(["start", "--job", job_id]);
    run_fixture(["finish", "--job", job_id, "--summary", "Here is the answer."]);
    await wait_for_pill(page, job_id, "DONE");
    assert.equal(await page.$$eval(".sj-job-card", (cards) => cards.length), 1);
    const answer_shown = await page.$eval(
        `.message_row:has(${card}) .message_content`,
        (content) => content.textContent?.includes("Here is the answer.") ?? false,
    );
    assert.ok(answer_shown, "The answer stays above the card");
    await common.screenshot(page, "job-card-done");
}

async function test_agent_task_flow(page: Page): Promise<void> {
    await common.log_in(page);
    const answer_scenario = start_scenario([
        "--owner",
        common.test_credentials.default_user.username,
        "--kind",
        "answer",
    ]);
    await test_card_and_drawer(page, answer_scenario);
    await test_cancel_flow(page, answer_scenario);
    await test_closed_tab_flow(page, answer_scenario);
    // A lost lease keeps the runner busy until the device confirms that it
    // stopped, so this flow has a runner of its own.
    const lease_scenario = start_scenario([
        "--owner",
        common.test_credentials.default_user.username,
        "--kind",
        "answer",
    ]);
    await test_lease_lost_flow(page, lease_scenario);

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
