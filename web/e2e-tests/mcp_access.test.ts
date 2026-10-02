import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function test_mcp_access(page: Page): Promise<void> {
    await common.log_in(page);
    await common.go_to_hash(page, "#mcp");
    await page.waitForSelector("#mcp-token-name", {visible: true});
    await page.waitForFunction(() =>
        document.querySelector("#mcp-endpoint")?.textContent?.endsWith("/mcp"),
    );
    await page.type("#mcp-token-name", "Hermes browser test");
    await page.click("#mcp-token-form button[type='submit']");
    await page.waitForSelector("#mcp-created-token", {visible: true});
    const token = await page.$eval("#mcp-created-token", (element) => {
        if (!(element instanceof HTMLTextAreaElement)) {
            throw new TypeError("Expected a token field.");
        }
        return element.value;
    });
    assert.ok(token.startsWith("gtm_"));
    await page.click("#mcp-hide-token");
    assert.equal(
        await page.$eval("#mcp-created-token", (element) => {
            if (!(element instanceof HTMLTextAreaElement)) {
                throw new TypeError("Expected a token field.");
            }
            return element.value;
        }),
        "",
    );
    await common.screenshot(page, "mcp-connections");
    await page.waitForSelector("[data-mcp-revoke]", {visible: true});
    await page.click("[data-mcp-revoke]");
    await page.waitForFunction(() =>
        document.querySelector("#mcp-status")?.textContent?.includes("Access revoked"),
    );
    await page.waitForSelector("[data-mcp-revoke]", {hidden: true});
    const status = await page.evaluate(async (credential) => {
        const response = await fetch("/mcp", {
            method: "POST",
            headers: {Authorization: `Bearer ${credential}`, "Content-Type": "application/json"},
            body: JSON.stringify({jsonrpc: "2.0", id: 1, method: "tools/list"}),
        });
        return response.status;
    }, token);
    assert.equal(status, 401);
    await common.go_to_hash(page, "#inbox");
    await page.waitForSelector("#mcp-view", {hidden: true});
}

await common.run_test(test_mcp_access);
