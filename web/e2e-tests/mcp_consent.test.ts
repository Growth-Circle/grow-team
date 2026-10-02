import assert from "node:assert/strict";
import {createHash} from "node:crypto";

import type {Page} from "puppeteer";
import * as z from "zod/mini";

import * as common from "./lib/common.ts";

async function test_mcp_consent(page: Page): Promise<void> {
    await common.log_in(page);
    const origin = new URL(page.url()).origin;
    const registration = await page.evaluate(async () => {
        const response = await fetch("/mcp/register", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                client_name: "Claude",
                redirect_uris: ["https://claude.ai/callback"],
            }),
        });
        const data: unknown = await response.json();
        return data;
    });
    const {client_id} = z.object({client_id: z.string()}).parse(registration);
    const verifier = "a".repeat(64);
    const challenge = createHash("sha256").update(verifier).digest("base64url");
    const params = new URLSearchParams({
        client_id,
        redirect_uri: "https://claude.ai/callback",
        response_type: "code",
        code_challenge: challenge,
        code_challenge_method: "S256",
        scope: "team:read",
        resource: `${origin}/mcp`,
        state: "browser-consent-test",
    });
    await page.setViewport({width: 1440, height: 1000});
    await page.goto(`${origin}/mcp/authorize?${params}`);
    await page.waitForSelector(".mcp-consent__approve", {visible: true});
    await page.evaluate(async () => {
        await document.fonts.ready;
    });
    const layout = await page.evaluate(() => {
        const card = document.querySelector(".mcp-consent");
        const approve = document.querySelector(".mcp-consent__approve");
        const checkbox = document.querySelector("#mcp-allow-write");
        if (!card || !approve || !(checkbox instanceof HTMLInputElement)) {
            throw new Error("Consent controls are missing.");
        }
        return {
            width: card.getBoundingClientRect().width,
            buttonHeight: approve.getBoundingClientRect().height,
            buttonColor: window.getComputedStyle(approve).backgroundColor,
            font: window.getComputedStyle(card).fontFamily,
            checked: checkbox.checked,
        };
    });
    assert.ok(layout.width > 700 && layout.width <= 882);
    assert.ok(layout.buttonHeight >= 44);
    assert.equal(layout.buttonColor, "rgb(255, 106, 61)");
    assert.match(layout.font, /Schibsted/);
    assert.equal(layout.checked, false);
    await common.screenshot(page, "mcp-consent-desktop");
    await page.click("#mcp-allow-write");
    await page.setViewport({width: 390, height: 844});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await common.screenshot(page, "mcp-consent-mobile");
    await page.emulateMediaFeatures([{name: "prefers-color-scheme", value: "dark"}]);
    await common.screenshot(page, "mcp-consent-dark");
    await page.emulateMediaFeatures([]);
    await page.setRequestInterception(true);
    page.on("request", (request) => {
        if (request.url().startsWith("https://claude.ai/callback")) {
            void request.respond({
                status: 200,
                contentType: "text/html",
                body: "<p>Connection approved</p>",
            });
        } else {
            void request.continue();
        }
    });
    await Promise.all([page.waitForNavigation(), page.click("button[value='approve']")]);
    const callback = new URL(page.url());
    assert.equal(callback.searchParams.get("state"), "browser-consent-test");
    const code = callback.searchParams.get("code");
    assert.ok(code);
    await page.goto(`${origin}/#mcp`);
    await page.waitForSelector("#mcp-token-name", {visible: true});
    const exchange = await page.evaluate(
        async (data) => {
            const response = await fetch("/mcp/token", {
                method: "POST",
                body: new URLSearchParams({
                    grant_type: "authorization_code",
                    client_id: data.client_id,
                    code: data.code,
                    code_verifier: data.verifier,
                    redirect_uri: "https://claude.ai/callback",
                }),
            });
            const raw: unknown = await response.json();
            return raw;
        },
        {client_id, code, verifier},
    );
    const pair = z.object({access_token: z.string(), scope: z.string()}).parse(exchange);
    const response = await page.evaluate(async (credential) => {
        const tools = await fetch("/mcp", {
            method: "POST",
            headers: {"Content-Type": "application/json", Authorization: `Bearer ${credential}`},
            body: JSON.stringify({jsonrpc: "2.0", id: 1, method: "tools/list"}),
        });
        const raw: unknown = await tools.json();
        return raw;
    }, pair.access_token);
    const rpc = z
        .object({result: z.object({tools: z.array(z.object({name: z.string()}))})})
        .parse(response);
    const names = new Set(rpc.result.tools.map((tool) => tool.name));
    assert.equal(pair.scope, "team:read team:write");
    assert.ok(names.has("send_message"));
    assert.ok(names.has("send_direct_message"));
}

await common.run_test(test_mcp_consent);
