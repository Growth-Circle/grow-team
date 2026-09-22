import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";

import type {Page} from "puppeteer";
import * as z from "zod/mini";

import * as common from "./lib/common.ts";

const fixture_schema = z.object({
    owner_id: z.number(),
    bot_user_id: z.number(),
    source_message_id: z.number(),
    profile_ids: z.array(z.string()),
});

async function test_task_composer(page: Page): Promise<void> {
    const fixture = fixture_schema.parse(
        JSON.parse(
            execFileSync(
                "python3",
                [
                    "web/e2e-tests/fixtures/agent_settings.py",
                    common.test_credentials.default_user.username,
                ],
                {encoding: "utf8"},
            ),
        ),
    );
    await common.log_in(page);
    const slug = [fixture.owner_id, fixture.bot_user_id].toSorted((a, b) => a - b).join(",");
    await page.goto(
        `http://zulip.zulipdev.com:9981/#narrow/dm/${slug}-dm/near/${fixture.source_message_id}`,
    );
    const row = `.message_row[data-message-id='${fixture.source_message_id}']`;
    await page.waitForSelector(row, {visible: true});
    await page.hover(row);
    await page.click(`${row} .message-actions-menu-button`);
    await page.waitForSelector(".agent_create_task", {visible: true});
    await page.click(".agent_create_task");
    await page.waitForSelector("#agent-task-dialog", {visible: true});
    assert.match(
        await page.$eval("#agent-task-source", (node) => node.textContent ?? ""),
        new RegExp(String(fixture.source_message_id)),
    );
    await page.waitForSelector(`#agent-task-profile option[value='${fixture.profile_ids[0]}']`);
    await page.select("#agent-task-profile", fixture.profile_ids[0]!);
    await page.waitForFunction(() =>
        document.querySelector("#agent-task-status")?.textContent?.includes("ready"),
    );
    await page.type("#agent-task-request", "Summarize this source message.");
    await common.screenshot(page, "task10-source-task-form");
    const response_promise = page.waitForResponse(
        (response) =>
            response.url().endsWith("/json/agent/jobs") && response.request().method() === "POST",
    );
    await page.click("#agent-task-form button[type='submit']");
    const response = await response_promise;
    assert.equal(response.status(), 200);
    const body = z
        .object({
            job: z.object({id: z.string(), source_message_id: z.number(), profile_id: z.string()}),
        })
        .parse(await response.json());
    assert.equal(body.job.source_message_id, fixture.source_message_id);
    assert.equal(body.job.profile_id, fixture.profile_ids[0]);
    await page.waitForSelector("#agent-job-overlay", {visible: true});
    await page.waitForFunction(
        (id) => document.querySelector("#agent-job-heading")?.textContent?.includes(id),
        {},
        body.job.id,
    );
    await common.screenshot(page, "task10-created-job");
}

await common.run_test(test_task_composer);
