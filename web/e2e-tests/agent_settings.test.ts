/* eslint-disable promise/prefer-await-to-then -- Request listeners capture response bodies without blocking the browser event loop. */
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

const fixture_schema = /"job_id": "([0-9a-f-]+)"/;
const provider_schema = /"provider_id": "([0-9a-f-]+)"/;
const evidence_job_schema = /"evidence_job_id": "([0-9a-f-]+)"/;
const owner_schema = /"owner_id": ([0-9]+)/;
const second_profile_schema = /"profile_ids": \["[0-9a-f-]+", "([0-9a-f-]+)"\]/;
const attachment_channel_schema = /"attachment_channel_id": ([0-9]+)/;

function assert_private_network(payload: unknown): void {
    assert.ok(payload && typeof payload === "object" && "network" in payload);
    const network = payload.network;
    assert.ok(network && typeof network === "object" && "targets" in network);
    assert.ok(Array.isArray(network.targets));
    const first: unknown = network.targets[0];
    assert.ok(first && typeof first === "object" && "allow_http_private" in first);
    assert.equal(first.allow_http_private, true);
}

async function check_area(
    page: Page,
    tab: string,
    panel: string,
    theme: "light" | "dark",
): Promise<void> {
    await page.click(`[data-agent-tab="${tab}"]`);
    await page.waitForSelector(panel, {visible: true});
    await page.waitForFunction(
        (selector) => {
            const node = document.querySelector(selector);
            return node && !node.hasAttribute("hidden");
        },
        {},
        panel,
    );
    await common.screenshot(page, `task9-${tab}-${theme}`);
}

type SharingFixture = {
    profile_id: string;
    runner_id: string;
    provider_id: string;
    repository_id: string;
    runner_owner: string;
    provider_owner: string;
    repository_owner: string;
    ordinary_member: string;
    ordinary_member_id: number;
};

async function log_in_as(page: Page, username: string): Promise<void> {
    await common.log_out(page);
    await common.log_in(page, {username, password: "task9-synthetic-browser-password"});
    await page.goto("http://zulip.zulipdev.com:9981/#settings/agents");
    await page.waitForSelector("#agent-settings", {visible: true});
}

async function create_visible_grant(
    page: Page,
    tab: "directory" | "devices" | "connections",
    kind: string,
    target_id: string,
    principal_id: number,
    actions: string[],
): Promise<void> {
    await page.click(`[data-agent-tab="${tab}"]`);
    if (kind === "profile") {
        await page.waitForSelector(
            `[data-agent-action="profile-detail"][data-agent-id="${target_id}"]`,
            {visible: true},
        );
        await page.$eval(
            `[data-agent-action="profile-detail"][data-agent-id="${target_id}"]`,
            (element) => {
                if (!(element instanceof HTMLElement)) {
                    throw new TypeError("Profile action is not an HTML element");
                }
                element.click();
            },
        );
    }
    await page.waitForSelector(
        `[data-agent-action="grant-open-${kind}"][data-agent-id="${target_id}"]`,
        {visible: true},
    );
    await page.$eval(
        `[data-agent-action="grant-open-${kind}"][data-agent-id="${target_id}"]`,
        (element) => {
            if (!(element instanceof HTMLElement)) {
                throw new TypeError("Grant action is not an HTML element");
            }
            element.click();
        },
    );
    await page.waitForSelector("#agent-resource-grant-form", {visible: true});
    await page.select("#agent-grant-principal", String(principal_id));
    await page.select("#agent-grant-actions", ...actions);
    await page.click('#agent-resource-grant-form button[type="submit"]');
    await page.waitForFunction(() =>
        document.querySelector("#agent-grant-result")?.textContent?.includes("Grant created"),
    );
}

async function test_agent_settings(page: Page): Promise<void> {
    const script = "web/e2e-tests/fixtures/agent_settings.py";
    const output = execFileSync(
        "python3",
        [script, common.test_credentials.default_user.username],
        {
            encoding: "utf8",
        },
    );
    const job_id = fixture_schema.exec(output)?.[1];
    assert.ok(job_id, "The synthetic fixture must create a job");
    const provider_id = provider_schema.exec(output)?.[1];
    assert.ok(provider_id);
    const evidence_job_id = evidence_job_schema.exec(output)?.[1];
    assert.ok(evidence_job_id);
    const owner_id = owner_schema.exec(output)?.[1];
    assert.ok(owner_id);
    const second_profile_id = second_profile_schema.exec(output)?.[1];
    assert.ok(second_profile_id);
    const attachment_channel_id = attachment_channel_schema.exec(output)?.[1];
    assert.ok(attachment_channel_id);
    await common.log_in(page);
    await page.click("#settings-dropdown");
    const agent_settings_item = '.link-item a[href="#settings/agents"]';
    await page.waitForSelector(agent_settings_item, {visible: true});
    await page.click(agent_settings_item);
    await page.waitForSelector("#agent-settings", {visible: true});
    await page.waitForFunction(
        () => document.querySelectorAll("#agent-profile-list .agent-card").length === 2,
    );
    const names = await page.$$eval("#agent-profile-list h4", (nodes) =>
        nodes.map((node) => node.textContent),
    );
    assert.deepEqual(names, ["Duplicate profile name", "Duplicate profile name"]);
    await page.click(`[data-agent-action="profile-detail"][data-agent-id="${second_profile_id}"]`);
    await page.waitForFunction(() =>
        document.querySelector("#agent-profile-detail")?.textContent?.includes("runner offline"),
    );
    assert.ok(await page.$('#agent-profile-detail [data-agent-action="repair-devices"]'));
    await page.waitForSelector(`#agent-attach-stream option[value="${attachment_channel_id}"]`);
    await page.select("#agent-attach-stream", attachment_channel_id);
    const attachment_name = await page.$eval("#agent-attach-stream", (node) => {
        if (!(node instanceof HTMLSelectElement)) {
            throw new TypeError("Named channel selector is missing");
        }
        return node.selectedOptions[0]?.textContent ?? "";
    });
    assert.ok(attachment_name && !/^[0-9]+$/.test(attachment_name));
    const attachment_response = page.waitForResponse(
        (response) =>
            response.request().method() === "POST" &&
            response.url().includes(`/json/agent/profiles/${second_profile_id}/attach-channel`),
    );
    await page.click('#agent-attach-form button[type="submit"]');
    const saved_attachment = await attachment_response;
    assert.equal(saved_attachment.status(), 200);
    const attachment_body = await saved_attachment.request().fetchPostData();
    const attachment_payload: unknown = JSON.parse(
        new URLSearchParams(attachment_body ?? "").get("payload") ?? "{}",
    );
    assert.ok(
        attachment_payload &&
            typeof attachment_payload === "object" &&
            "stream_id" in attachment_payload,
    );
    assert.equal(attachment_payload.stream_id, Number(attachment_channel_id));
    await page.waitForFunction(
        (name) =>
            document
                .querySelector("#agent-profile-detail")
                ?.textContent?.includes(`${name}: bot member`),
        {},
        attachment_name,
    );
    await page.$eval("#agent-profile-detail", (node) => {
        node.scrollIntoView({block: "start"});
    });
    await common.screenshot(page, "task9-attachment-detail-light");
    await page.click("#agent-detail-close");
    for (const theme of ["light", "dark"] as const) {
        await page.evaluate(
            (dark) => document.documentElement.classList.toggle("dark-theme", dark),
            theme === "dark",
        );
        await check_area(page, "directory", "#agent-directory-panel", theme);
        await check_area(page, "devices", "#agent-devices-panel", theme);
        await page.waitForFunction(
            () => document.querySelectorAll("#agent-runner-list .agent-card").length > 0,
        );
        await check_area(page, "connections", "#agent-connections-panel", theme);
        await page.waitForFunction(
            () => document.querySelectorAll("#agent-provider-list .agent-card").length > 0,
        );
        await check_area(page, "default", "#agent-default-panel", theme);
    }
    const provider_refresh = page.waitForResponse(
        (response) =>
            response.url().includes("/json/agent/providers?") && response.status() === 200,
    );
    await page.click('[data-agent-tab="connections"]');
    await provider_refresh;
    await page.waitForFunction(
        () => document.querySelectorAll("#agent-provider-list .agent-card").length > 0,
    );
    await page.evaluate(() => {
        const button = document.querySelector('[data-agent-action="provider-edit"]');
        if (!(button instanceof HTMLButtonElement)) {
            throw new TypeError("Provider edit control is missing");
        }
        button.click();
    });
    await page.waitForFunction(() => {
        const host = document.querySelector("#agent-provider-network-host");
        return host instanceof HTMLInputElement && host.value === "wulan.test";
    });
    const owner_connection = await page.evaluate(() => {
        const host = document.querySelector("#agent-provider-network-host");
        const private_http = document.querySelector("#agent-provider-network-http-private");
        if (!(host instanceof HTMLInputElement) || !(private_http instanceof HTMLInputElement)) {
            throw new TypeError("Owner network controls are missing");
        }
        return {
            scopes: [
                ...document.querySelectorAll<HTMLInputElement>(
                    "#agent-provider-scopes input:checked",
                ),
            ].map((input) => input.value),
            host: host.value,
            private_http: private_http.checked,
        };
    });
    assert.deepEqual(owner_connection, {
        scopes: ["synthetic", "selected_chat", "selected_repository"],
        host: "wulan.test",
        private_http: true,
    });
    await page.waitForFunction(() =>
        document
            .querySelector("#agent-provider-impact")
            ?.textContent?.includes("Duplicate profile name"),
    );
    assert.match(
        await page.$eval("#agent-provider-impact", (node) => node.textContent ?? ""),
        /Existing attempt snapshots/,
    );
    let saved_connection: Promise<string | undefined> | undefined;
    page.on("request", (request) => {
        if (
            request.url().includes(`/json/agent/providers/${provider_id}`) &&
            request.method() === "PATCH"
        ) {
            saved_connection = request
                .fetchPostData()
                .then((body) => new URLSearchParams(body ?? "").get("payload") ?? undefined);
        }
    });
    await page.click('#agent-provider-form button[type="submit"]');
    assert.ok(saved_connection);
    const saved_payload: unknown = JSON.parse((await saved_connection) ?? "{}");
    await page.waitForFunction(() =>
        document.querySelector("#agent-provider-result")?.textContent?.includes("saved"),
    );
    assert.ok(saved_payload && typeof saved_payload === "object" && "data_scope" in saved_payload);
    assert.deepEqual(saved_payload.data_scope, [
        "synthetic",
        "selected_chat",
        "selected_repository",
    ]);
    assert_private_network(saved_payload);
    await page.click('[data-agent-tab="directory"]');
    await page.click("#agent-new-profile");
    await page.waitForSelector("#agent-profile-form", {visible: true});
    await page.type("#agent-profile-name", "Private provider draft");
    await page.select("#agent-profile-provider", provider_id);
    for (let index = 0; index < 5; index += 1) {
        await page.click("#agent-profile-next");
    }
    const review = await page.$eval("#agent-profile-review", (node) => node.textContent ?? "");
    assert.match(review, /Browser test workstation/);
    assert.match(review, /Synthetic local model/);
    assert.match(review, /selected_chat/);
    assert.match(review, /Budget/);
    assert.match(review, /Capabilities not yet tested/);
    let created_profile: Promise<string | undefined> | undefined;
    page.on("request", (request) => {
        if (request.url().endsWith("/json/agent/profiles") && request.method() === "POST") {
            created_profile = request
                .fetchPostData()
                .then((body) => new URLSearchParams(body ?? "").get("payload") ?? undefined);
        }
    });
    await page.click("#agent-profile-save");
    await page.waitForFunction(() =>
        document.querySelector("#agent-profile-result")?.textContent?.includes("Draft saved"),
    );
    assert.ok(created_profile);
    const created_payload: unknown = JSON.parse((await created_profile) ?? "{}");
    assert_private_network(created_payload);
    assert.ok(
        created_payload &&
            typeof created_payload === "object" &&
            "idempotency_key" in created_payload &&
            typeof created_payload.idempotency_key === "string",
    );
    const recovery_key = created_payload.idempotency_key;
    await page.evaluate(
        (id, key) => {
            const pointer = `grow-agent-profile:${window.location.origin}:${id}`;
            sessionStorage.setItem(pointer, key);
            sessionStorage.setItem(`${pointer}:submitted`, JSON.stringify([key]));
        },
        owner_id,
        recovery_key,
    );
    await page.reload();
    await page.waitForFunction(() =>
        document
            .querySelector("#agent-settings-status")
            ?.textContent?.includes("Recovered saved profile Private provider draft"),
    );
    await page.setViewport({width: 390, height: 850});
    await page.click('[data-agent-tab="directory"]');
    await page.waitForFunction(() => {
        const input = document.querySelector("#agent-search");
        return (
            input instanceof HTMLInputElement &&
            input.getBoundingClientRect().right <= window.innerWidth
        );
    });
    await common.screenshot(page, "task9-directory-narrow");
    const narrow = await page.evaluate(() => {
        const panel = document.querySelector("#agent-settings");
        const input = document.querySelector("#agent-search");
        if (!(panel instanceof HTMLElement) || !(input instanceof HTMLInputElement)) {
            throw new TypeError("Narrow directory controls are missing");
        }
        return {
            scroll_width: panel.scrollWidth,
            client_width: panel.clientWidth,
            input_right: input.getBoundingClientRect().right,
            viewport_width: window.innerWidth,
        };
    });
    console.info("Narrow directory metrics:", JSON.stringify(narrow));
    assert.ok(
        narrow.scroll_width <= narrow.client_width + 1,
        "directory has no horizontal overflow",
    );
    assert.ok(narrow.input_right <= narrow.viewport_width, "search input stays in viewport");
    await page.focus("#agent-search");
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.id);
    assert.equal(focused, "agent-ownership");
    let profile_requests = 0;
    let runner_requests = 0;
    page.on("request", (request) => {
        if (request.url().includes("/json/agent/profiles?")) {
            profile_requests += 1;
        }
        if (request.url().includes("/json/agent/runners?")) {
            runner_requests += 1;
        }
    });
    await new Promise((resolve) => setTimeout(resolve, 16000));
    assert.ok(profile_requests >= 1 && profile_requests <= 2, "directory uses one parent refresh");
    await page.click('[data-agent-tab="devices"]');
    const profiles_before_devices = profile_requests;
    const runners_before_devices = runner_requests;
    await new Promise((resolve) => setTimeout(resolve, 16000));
    assert.equal(
        profile_requests,
        profiles_before_devices,
        "directory refresh stops on tab change",
    );
    assert.ok(runner_requests > runners_before_devices, "visible devices refresh once");
    await page.setViewport({width: 1400, height: 1024});
    await page.evaluate(() => {
        document.documentElement.classList.remove("dark-theme");
    });
    await page.goto(`http://zulip.zulipdev.com:9981/#agent-jobs/${job_id}`);
    const runners_after_close = runner_requests;
    await page.waitForSelector("#agent-job-overlay.show", {visible: true});
    await page.waitForFunction(
        () =>
            window.getComputedStyle(document.querySelector("#agent-job-overlay")!).opacity === "1",
    );
    await page.waitForFunction(() =>
        document.querySelector("#agent-job-status")?.textContent?.includes("stopped and cannot continue"),
    );
    await common.screenshot(page, "task9-job-light");
    await page.evaluate(() => {
        document.documentElement.classList.add("dark-theme");
    });
    await common.screenshot(page, "task9-job-dark");
    await new Promise((resolve) => setTimeout(resolve, 16000));
    assert.equal(runner_requests, runners_after_close, "settings refresh stops after close");
    await page.reload();
    await page.waitForSelector("#agent-job-overlay.show", {visible: true});
    await page.waitForFunction(() =>
        document.querySelector("#agent-job-status")?.textContent?.includes("stopped and cannot continue"),
    );
    assert.ok((await common.page_url_with_fragment(page)).endsWith(`#agent-jobs/${job_id}`));
    await page.goto(`http://zulip.zulipdev.com:9981/#agent-jobs/${evidence_job_id}`);
    await page.waitForFunction(() =>
        document.querySelector("#agent-job-status")?.textContent?.includes("waits for your decision"),
    );
    await page.waitForFunction(() =>
        document.querySelector("#agent-job-checks")?.textContent?.includes("passing"),
    );
    await page.waitForFunction(() =>
        document
            .querySelector("#agent-job-inputs")
            ?.textContent?.includes("The agent received it and has not used it yet."),
    );
    await page.waitForFunction(() =>
        document.querySelector("#agent-job-events")?.textContent?.includes("attempt.started"),
    );
    assert.ok(await page.$('#agent-job-operations [data-job-action="approve"]'));
    assert.ok(await page.$('#agent-job-operations [data-job-action="reject"]'));
    assert.ok(await page.$('#agent-job-controls [data-job-action="cancel"]'));
    assert.ok(await page.$("#agent-job-input-form:not([hidden])"));
    const evidence_text = await page.$eval("#agent-job-overlay", (node) => node.textContent ?? "");
    assert.match(evidence_text, /evidence\.patch/);
    assert.match(evidence_text, /The agent received it and has not used it yet\./);
    assert.match(evidence_text, /attempt\.started/);
    await page.evaluate(() => {
        document.documentElement.classList.remove("dark-theme");
    });
    await common.screenshot(page, "task9-job-evidence-light");
    await page.$eval("#agent-job-overlay .agent-job-body", (node) => {
        node.scrollTop = node.scrollHeight;
    });
    await common.screenshot(page, "task9-job-evidence-details-light");
    await page.evaluate(() => {
        document.documentElement.classList.add("dark-theme");
    });
    await common.screenshot(page, "task9-job-evidence-details-dark");
    const sharing_data: unknown = JSON.parse(
        execFileSync(
            "python3",
            [
                "web/e2e-tests/fixtures/agent_sharing.py",
                common.test_credentials.default_user.username,
            ],
            {encoding: "utf8"},
        ),
    );
    assert.ok(
        sharing_data &&
            typeof sharing_data === "object" &&
            "profile_id" in sharing_data &&
            "runner_id" in sharing_data &&
            "provider_id" in sharing_data &&
            "repository_id" in sharing_data &&
            "runner_owner" in sharing_data &&
            "provider_owner" in sharing_data &&
            "repository_owner" in sharing_data &&
            "ordinary_member" in sharing_data &&
            "ordinary_member_id" in sharing_data,
    );
    const sharing: SharingFixture = {
        profile_id: String(sharing_data.profile_id),
        runner_id: String(sharing_data.runner_id),
        provider_id: String(sharing_data.provider_id),
        repository_id: String(sharing_data.repository_id),
        runner_owner: String(sharing_data.runner_owner),
        provider_owner: String(sharing_data.provider_owner),
        repository_owner: String(sharing_data.repository_owner),
        ordinary_member: String(sharing_data.ordinary_member),
        ordinary_member_id: Number(sharing_data.ordinary_member_id),
    };
    await common.log_out(page);
    await common.log_in(page);
    await page.goto("http://zulip.zulipdev.com:9981/#settings/agents");
    await create_visible_grant(
        page,
        "directory",
        "profile",
        sharing.profile_id,
        sharing.ordinary_member_id,
        ["profile.use", "context.read"],
    );
    await log_in_as(page, sharing.ordinary_member);
    await page.waitForSelector(
        `[data-agent-action="profile-detail"][data-agent-id="${sharing.profile_id}"]`,
        {visible: true},
    );
    assert.equal(
        await page.$(
            `[data-agent-action="grant-open-profile"][data-agent-id="${sharing.profile_id}"]`,
        ),
        null,
    );
    assert.equal(
        await page.$(
            `[data-agent-action="grant-open-runner"][data-agent-id="${sharing.runner_id}"]`,
        ),
        null,
    );
    await page.click(`[data-agent-action="profile-detail"][data-agent-id="${sharing.profile_id}"]`);
    await page.waitForFunction(() =>
        document
            .querySelector("#agent-profile-detail")
            ?.textContent?.includes("Requires the resource owner's grant"),
    );
    await page.$eval("#agent-profile-detail", (node) => {
        node.scrollIntoView({block: "start"});
    });
    await common.screenshot(page, "task9-sharing-member-partial");
    await log_in_as(page, sharing.runner_owner);
    await create_visible_grant(
        page,
        "devices",
        "runner",
        sharing.runner_id,
        sharing.ordinary_member_id,
        ["runner.use"],
    );
    await log_in_as(page, sharing.provider_owner);
    await create_visible_grant(
        page,
        "connections",
        "provider",
        sharing.provider_id,
        sharing.ordinary_member_id,
        ["provider.use"],
    );
    await log_in_as(page, sharing.repository_owner);
    await create_visible_grant(
        page,
        "devices",
        "repository",
        sharing.repository_id,
        sharing.ordinary_member_id,
        ["repository.read"],
    );
    await log_in_as(page, sharing.ordinary_member);
    await page.waitForSelector(
        `[data-agent-action="profile-detail"][data-agent-id="${sharing.profile_id}"]`,
        {visible: true},
    );
    await page.click(`[data-agent-action="profile-detail"][data-agent-id="${sharing.profile_id}"]`);
    await page.waitForFunction(() =>
        document
            .querySelector("#agent-profile-detail")
            ?.textContent?.includes("Combined access for you: Available"),
    );
    assert.equal(
        await page.$("#agent-profile-detail [data-agent-action='grant-open-profile']"),
        null,
    );
    await page.$eval("#agent-profile-detail", (node) => {
        node.scrollIntoView({block: "start"});
    });
    await common.screenshot(page, "task9-sharing-member-complete");
}

await common.run_test(test_agent_settings);
