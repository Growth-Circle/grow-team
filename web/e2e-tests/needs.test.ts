import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";

import type {HTTPRequest, Page} from "puppeteer";
import * as z from "zod/mini";

import * as common from "./lib/common.ts";

const FIXTURE = "web/e2e-tests/fixtures/agent_fake_runner.py";
const REALM_URL = "http://zulip.zulipdev.com:9981/";
// The page sends an action 5 seconds after the click, unless the
// person undoes it.
const UNDO_WINDOW_MS = 5000;

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

const approval_schema = z.object({operation_id: z.string(), approval_id: z.string()});

function assert_near(actual: number, expected: number, tolerance: number, label: string): void {
    assert.ok(
        Math.abs(actual - expected) <= tolerance,
        `${label}: expected ${expected} (±${tolerance}), got ${actual}`,
    );
}

async function sleep(ms: number): Promise<void> {
    await new Promise((resolve) => {
        setTimeout(resolve, ms);
    });
}

// The shared log_in waits for the Today page to have a size. The app
// starts on Today or on Inbox, by the build, and an empty Inbox has no
// size. This waits until either page is on, which happens when the app
// has started.
async function log_in(page: Page, username: string, password: string): Promise<void> {
    await page.goto(`${REALM_URL}login/`);
    await common.fill_form(page, "form#login_form", {username, password});
    await page.$eval("form#login_form", (form) => {
        form.submit();
    });
    await page.waitForFunction(() =>
        [...document.querySelectorAll("#today-view, #inbox-view")].some(
            (view) => window.getComputedStyle(view).display !== "none",
        ),
    );
}

async function open_needs(page: Page): Promise<void> {
    await common.go_to_hash(page, "#needs");
    await page.waitForSelector("#needs-view", {visible: true});
}

// ----- A real approval, from the fake runner -----

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
    // Type the whole mention. Picking it from the list moves the caret
    // after the text is typed, which mixes up a slow run.
    await page.type("#compose-textarea", `@**${bot_name}** ${request}`);
    await page.click("#compose-send-button");
    const banner = "#compose_banners .agent_task_receipt_banner";
    // Right after the test database is made, the server can answer "busy"
    // to a send for a moment. The page then keeps the message with a retry
    // control, so this retries like a person would, up to 5 times.
    for (let attempt = 1; ; attempt += 1) {
        const state = await page.waitForFunction(
            (selector) => {
                if (document.querySelector(selector) !== null) {
                    return "sent";
                }
                return document.querySelector(".message_row .refresh-failed-message") === null
                    ? false
                    : "failed";
            },
            {timeout: 30000},
            banner,
        );
        if ((await state.jsonValue()) === "sent") {
            break;
        }
        assert.ok(attempt < 5, "The send kept failing after 5 tries");
        await page.click(".message_row .refresh-failed-message");
    }
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

async function start_waiting_approval(
    page: Page,
    scenario: Scenario,
): Promise<{job_id: string; approval: z.infer<typeof approval_schema>; count_before: number}> {
    // The receipt of a task says that the agent started it only when the
    // runner has reported lately.
    run_fixture(["heartbeat", "--runner", scenario.runner_id]);
    // A scenario sets a new password for the member, which ends the
    // session of anyone who is logged in as that member.
    await log_in(page, scenario.member_email, scenario.member_password);
    await open_channel_compose(page, scenario);
    const count_before = Number(await sidebar_badge(page));
    const job_id = await mention_and_send(
        page,
        scenario.bot_user_id,
        "Please unsubscribe me, I am stepping back from this channel.",
    );
    run_fixture(["claim", "--runner", scenario.runner_id]);
    run_fixture(["start", "--job", job_id]);
    const approval = approval_schema.parse(run_fixture(["approval", "--job", job_id]));
    return {job_id, approval, count_before};
}

function finish_job(job_id: string, operation_id: string): void {
    run_fixture(["execute", "--job", job_id, "--operation", operation_id]);
    run_fixture(["finish", "--job", job_id, "--summary", "Removed the member as requested."]);
}

// Approve a manage job's risky operation from the page instead of the
// job overlay. The click waits 5 seconds before it sends the decision.
// The fixture runner accepts the operation only after the server holds
// an approved decision, so this proves that the click reached the server
// and did not only change the screen.
async function test_approve_from_needs(page: Page, scenario: Scenario): Promise<void> {
    const {job_id, approval, count_before} = await start_waiting_approval(page, scenario);
    const need_id = `approval:${approval.approval_id}`;
    const card = `.needs-card[data-need-id="${need_id}"]`;

    // The job event moves the count in the sidebar while the page is on a room.
    await page.waitForFunction(
        (selector, expected) => document.querySelector(selector)?.textContent === expected,
        {timeout: 15000},
        '.sanji-nav-item[data-nav-id="needs"] .unread_count',
        String(count_before + 1),
    );

    await open_needs(page);
    await page.waitForSelector(card, {visible: true});
    assert.equal(
        await page.$eval(`${card} .needs-tag`, (node) => node.textContent),
        "APPROVAL",
        "the operation must show up under the APPROVAL tag",
    );

    await page.click(`.needs-card-primary[data-need-id="${need_id}"]`);
    await page.waitForSelector(card, {hidden: true});
    await page.waitForSelector(".sj-toast__action", {visible: true});

    await page.waitForSelector(`.needs-done-row[data-need-id="${need_id}"]`, {
        visible: true,
        timeout: 15000,
    });

    finish_job(job_id, approval.operation_id);
}

// A list that loads again while an approved item is inside its undo
// window must not bring the item back. This leaves the page and opens it
// again inside the window, then checks that the card stays away, and
// that it shows once in the done list when the delayed decision lands.
async function test_reload_during_undo_window(page: Page, scenario: Scenario): Promise<void> {
    const {job_id, approval} = await start_waiting_approval(page, scenario);
    const need_id = `approval:${approval.approval_id}`;
    const card = `.needs-card[data-need-id="${need_id}"]`;
    const done_row = `.needs-done-row[data-need-id="${need_id}"]`;

    await open_needs(page);
    await page.waitForSelector(card, {visible: true});

    await page.click(`.needs-card-primary[data-need-id="${need_id}"]`);
    await page.waitForSelector(card, {hidden: true});
    await page.waitForSelector(".sj-toast__action", {visible: true});

    await common.go_to_hash(page, "#feed");
    await open_needs(page);
    assert.equal(await page.$(card), null, "a pending item must not come back after a new load");

    await page.waitForSelector(done_row, {visible: true, timeout: 15000});
    const done_count = await page.$$eval(done_row, (nodes) => nodes.length);
    assert.equal(done_count, 1, "the resolved item must show once");

    finish_job(job_id, approval.operation_id);
}

// ----- A list that the test controls -----
//
// The page loads a list from the server, and sends an action to it.
// This answers both from here, so that every kind of item, and every
// state of the page, shows up without a real agent run.

type FakeItem = {id: string; job_id: string | null} & Record<string, unknown>;

type FakeServer = {
    open: FakeItem[];
    done: FakeItem[];
    // Each action that reached the server: the path, and its body.
    calls: {path: string; payload: Record<string, unknown>}[];
    // Ids of the items whose action the server refuses.
    refuse: Set<string>;
    fail_list: boolean;
    list_delay_ms: number;
};

const payload_schema = z.record(z.string(), z.unknown());

function fake_item(overrides: Record<string, unknown>): FakeItem {
    return {
        id: "approval:a1",
        kind: "approval",
        actor: {
            type: "agent",
            id: 900,
            name: "Kaki",
            agent_role: "answer",
            shape: "circle",
            color: "#ff6a3d",
            initials: "KA",
        },
        stream_id: null,
        topic: "release",
        time: new Date(Date.now() - 5 * 60_000).toISOString(),
        text: "Copy harga ada 2 versi. Pilih salah satu biar Ayame bisa lanjut.",
        attachment: null,
        actions: ["approve"],
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        expired: false,
        resolved_at: null,
        resolved_action: null,
        job_id: "job-a1",
        approval_version: 1,
        operation_hash: "hash-a1",
        nonce: "nonce-a1",
        job_version: null,
        ...overrides,
    };
}

function fake_list(stream_id: number): FakeItem[] {
    const ayame = {
        type: "agent",
        id: 901,
        name: "Ayame",
        agent_role: "answer",
        shape: "ring",
        color: "#8b74ff",
        initials: "AY",
    };
    const matcha = {
        type: "agent",
        id: 902,
        name: "Matcha",
        agent_role: "answer",
        shape: "box",
        color: "#16c784",
        initials: "MA",
    };
    const raka = {
        type: "human",
        id: 903,
        name: "Raka Putra",
        agent_role: null,
        shape: null,
        color: null,
        initials: "RP",
    };
    return [
        fake_item({
            id: "approval:a1",
            stream_id,
            text: 'PR #214 "Landing page rilis v2" siap digabung. Matcha sudah lolos cek.',
            attachment: {kind: "diff", id: "diff-1", filename: "PR #214 · 18 file"},
            job_id: "job-a1",
        }),
        fake_item({
            id: "approval:a2",
            actor: ayame,
            stream_id,
            text: "Ringkasan riset churn siap dibagikan ke tim.",
            expired: true,
            job_id: "job-a2",
        }),
        fake_item({
            id: "approval:a3",
            stream_id,
            text: "Kirim invoice ke klien.",
            job_id: "job-a3",
            approval_version: 2,
            operation_hash: "hash-a3",
            nonce: "nonce-a3",
        }),
        fake_item({
            id: "approval:a4",
            stream_id,
            text: "Bagikan dokumen ke klien.",
            job_id: "job-a4",
            approval_version: 3,
            operation_hash: "hash-a4",
            nonce: "nonce-a4",
        }),
        fake_item({
            id: "mention:101",
            kind: "mention",
            actor: raka,
            stream_id,
            text: "@Dita klien minta laporan Agustus dikirim sebelum jam 3. Sudah oke dikirim?",
            attachment: {kind: "file", id: "7", filename: "Laporan Agustus.pdf"},
            actions: [],
            job_id: null,
            approval_version: null,
            operation_hash: null,
            nonce: null,
        }),
        fake_item({
            id: "decision:job-d1",
            kind: "decision",
            actor: matcha,
            stream_id,
            text: "Copy harga ada 2 versi. Pilih salah satu biar Ayame bisa lanjut.",
            actions: ["Version A", "Version B"],
            job_id: "job-d1",
            job_version: 3,
            approval_version: null,
            operation_hash: null,
            nonce: null,
        }),
        fake_item({
            id: "decision:job-d2",
            kind: "decision",
            stream_id,
            text: "Which client should get the report first?",
            actions: [],
            job_id: "job-d2",
            job_version: 1,
            approval_version: null,
            operation_hash: null,
            nonce: null,
        }),
    ];
}

function fake_done(stream_id: number): FakeItem[] {
    return [
        fake_item({
            id: "approval:done1",
            stream_id,
            text: "Draft brief klien Kopi Senja",
            resolved_at: new Date().toISOString(),
            resolved_action: "approve",
        }),
        fake_item({
            id: "approval:done2",
            stream_id,
            text: "Jadwal konten Oktober",
            resolved_at: new Date().toISOString(),
            resolved_action: "approve",
        }),
    ];
}

function counts_of(items: FakeItem[]): Record<string, number> {
    const count = (kind: string): number => items.filter((item) => item["kind"] === kind).length;
    return {
        approval: count("approval"),
        mention: count("mention"),
        decision: count("decision"),
        all: items.length,
    };
}

const JSON_HEADERS = {contentType: "application/json"};

// Moves an item to the done list, as the server does once it has
// taken an action.
function settle(server: FakeServer, need_id: string): void {
    const index = server.open.findIndex((item) => item.id === need_id);
    const [item] = server.open.splice(index, 1);
    if (item !== undefined) {
        server.done.unshift({...item, resolved_at: new Date().toISOString()});
    }
}

async function answer(server: FakeServer, request: HTTPRequest): Promise<void> {
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (method === "GET" && path === "/json/needs") {
        await sleep(server.list_delay_ms);
        if (server.fail_list) {
            await request.respond({
                status: 500,
                ...JSON_HEADERS,
                body: JSON.stringify({result: "error", msg: "Try later.", code: "BAD_REQUEST"}),
            });
            return;
        }
        const items = url.searchParams.get("status") === "resolved" ? server.done : server.open;
        await request.respond({
            status: 200,
            ...JSON_HEADERS,
            body: JSON.stringify({result: "success", msg: "", items, counts: counts_of(items)}),
        });
        return;
    }

    const approval = /^\/json\/agent\/approvals\/([^/]+)\/decision$/.exec(path);
    const input = /^\/json\/agent\/jobs\/([^/]+)\/inputs$/.exec(path);
    const mention = /^\/json\/needs\/mentions\/(\d+)\/resolve$/.exec(path);
    const target = approval ?? input ?? mention;
    if (method === "POST" && target?.[1] !== undefined) {
        const need_id = approval
            ? `approval:${target[1]}`
            : input
              ? `decision:${target[1]}`
              : `mention:${target[1]}`;
        const raw = new URLSearchParams((await request.fetchPostData()) ?? "").get("payload");
        server.calls.push({
            path,
            payload: raw === null ? {} : payload_schema.parse(JSON.parse(raw)),
        });
        if (server.refuse.has(need_id)) {
            await request.respond({
                status: 400,
                ...JSON_HEADERS,
                body: JSON.stringify({result: "error", msg: "Not now.", code: "BAD_REQUEST"}),
            });
            return;
        }
        settle(server, need_id);
        await request.respond({
            status: 200,
            ...JSON_HEADERS,
            body: JSON.stringify({
                result: "success",
                msg: "",
                schema_version: 1,
                approval_id: target[1],
                decision: "approved",
                version: 2,
            }),
        });
        return;
    }

    await request.continue();
}

async function serve_fake_list(page: Page, server: FakeServer): Promise<() => Promise<void>> {
    const handler = (request: HTTPRequest): void => {
        void (async () => {
            try {
                await answer(server, request);
            } catch {
                // A reload can cut a request short. Nobody waits for its answer.
            }
        })();
    };
    await page.setRequestInterception(true);
    page.on("request", handler);
    return async () => {
        page.off("request", handler);
        await page.setRequestInterception(false);
    };
}

async function wait_for_calls(server: FakeServer, count: number): Promise<void> {
    const deadline = Date.now() + UNDO_WINDOW_MS * 3;
    while (server.calls.length < count) {
        assert.ok(
            Date.now() < deadline,
            `expected ${count} actions to reach the server, got ${server.calls.length}`,
        );
        await sleep(100);
    }
}

const card_selector = (need_id: string): string => `.needs-card[data-need-id="${need_id}"]`;

async function card_ids(page: Page): Promise<string[]> {
    return await page.$$eval(".needs-card", (nodes) =>
        nodes.map((node) => node.getAttribute("data-need-id") ?? ""),
    );
}

async function sidebar_badge(page: Page): Promise<string | null> {
    return await page.$eval(
        '.sanji-nav-item[data-nav-id="needs"] .unread_count',
        (node) => node.textContent,
    );
}

async function click_tab(page: Page, tab: string): Promise<void> {
    await page.click(`.needs-tab[data-tab="${tab}"]`);
    await page.waitForSelector(`.needs-tab[data-tab="${tab}"][aria-pressed="true"]`);
}

type Box = {x: number; y: number; width: number; height: number};

// A card that is new pops in for 0.3s, and its box moves while it does.
// A box is read only when the motion on the page has ended.
async function box_of(page: Page, selector: string): Promise<Box> {
    await page.waitForFunction(
        () =>
            (document.querySelector(".needs-page")?.getAnimations({subtree: true}).length ?? 0) ===
            0,
    );
    return await page.$eval(selector, (node) => {
        const {x, y, width, height} = node.getBoundingClientRect();
        return {x, y, width, height};
    });
}

async function style_of(page: Page, selector: string, property: string): Promise<string> {
    return await page.$eval(
        selector,
        (node, name) => window.getComputedStyle(node).getPropertyValue(name),
        property,
    );
}

// The sizes and colors of the inbox block in the Dashboard mockup, in
// a 1600x1000 window: the page box, the header, the tabs, a card, the
// avatars, the tags, the buttons, and the done list.
async function test_measure_layout(page: Page): Promise<void> {
    await page.setViewport({width: 1600, height: 1000});
    const page_box = await box_of(page, ".needs-page");
    assert_near(page_box.width, 1024, 0.5, "page width (960 plus 32px on each side)");

    assert.equal(await style_of(page, ".needs-title", "font-weight"), "900", "title weight");
    assert_near(
        Number.parseFloat(await style_of(page, ".needs-title", "font-size")),
        48,
        0.5,
        "title size in a wide window",
    );
    assert_near(
        Number.parseFloat(await style_of(page, ".needs-eyebrow", "font-size")),
        12,
        0.1,
        "label size",
    );
    assert_near((await box_of(page, ".needs-header")).height, 69, 3, "header height");

    const tab_boxes = await Promise.all(
        ["all", "approval", "mention", "decision"].map(async (tab) =>
            box_of(page, `.needs-tab[data-tab="${tab}"]`),
        ),
    );
    for (const tab_box of tab_boxes) {
        assert_near(tab_box.height, 34, 3, "tab height");
    }
    assert.equal(
        await style_of(page, ".needs-tab-active", "background-color"),
        "rgb(22, 22, 29)",
        "active tab is ink",
    );
    assert.equal(
        await style_of(page, ".needs-tab-active", "color"),
        "rgb(255, 250, 240)",
        "active tab text",
    );
    assert.equal(
        await style_of(page, '.needs-tab[data-tab="approval"]', "background-color"),
        "rgb(255, 250, 240)",
        "resting tab",
    );

    // A card without a chip: 78.59 high in the mockup.
    const plain = card_selector("decision:job-d1");
    const plain_box = await box_of(page, plain);
    assert_near(plain_box.width, 960, 0.5, "card width");
    assert_near(plain_box.height, 78.59, 2.5, "card height");
    // The browser may round 1.5px down to a whole pixel of the screen.
    assert_near(
        Number.parseFloat(await style_of(page, plain, "border-top-width")),
        1.25,
        0.25,
        "card border",
    );
    assert.equal(await style_of(page, plain, "border-radius"), "18px", "card radius");

    // The cards sit 20px apart, and the first is 20px below the header.
    const header_box = await box_of(page, ".needs-header");
    const first_box = await box_of(page, card_selector("approval:a1"));
    const second_box = await box_of(page, card_selector("approval:a2"));
    assert_near(
        first_box.y - (header_box.y + header_box.height),
        20,
        1,
        "gap between header and cards",
    );
    assert_near(second_box.y - (first_box.y + first_box.height), 20, 1, "gap between cards");

    // Avatars: circle, ring, rounded square, initials.
    const circle = `${card_selector("approval:a1")} .needs-avatar`;
    assert_near((await box_of(page, circle)).width, 34, 0.5, "circle avatar size");
    assert.equal(await style_of(page, circle, "background-color"), "rgb(255, 106, 61)");
    assert.equal(await style_of(page, circle, "border-radius"), "50%");
    const ring = `${card_selector("approval:a2")} .needs-avatar`;
    assert_near((await box_of(page, ring)).height, 34, 0.5, "ring avatar size");
    assert.equal(await style_of(page, ring, "border-top-width"), "8px", "ring avatar width");
    assert.equal(await style_of(page, ring, "border-top-color"), "rgb(139, 116, 255)");
    const box_avatar = `${plain} .needs-avatar`;
    assert_near((await box_of(page, box_avatar)).width, 32, 0.5, "box avatar size");
    assert.equal(await style_of(page, box_avatar, "border-radius"), "9px", "box avatar radius");
    assert.equal(await style_of(page, box_avatar, "background-color"), "rgb(22, 199, 132)");
    const human = `${card_selector("mention:101")} .needs-avatar`;
    assert_near((await box_of(page, human)).width, 34, 0.5, "initials avatar size");
    assert.equal(await style_of(page, human, "background-color"), "rgb(22, 22, 29)");
    assert.equal(await style_of(page, human, "font-size"), "11px", "initials text size");
    assert.equal(await page.$eval(human, (node) => node.textContent), "RP");

    // Tags, meta, text, chip.
    assert.equal(
        await style_of(page, `${card_selector("approval:a1")} .needs-tag`, "background-color"),
        "rgb(255, 225, 212)",
        "approval tag color",
    );
    assert.equal(
        await style_of(page, `${card_selector("mention:101")} .needs-tag`, "background-color"),
        "rgb(231, 225, 255)",
        "mention tag color",
    );
    assert.equal(
        await style_of(page, `${plain} .needs-tag`, "background-color"),
        "rgb(255, 241, 184)",
        "decision tag color",
    );
    assert.equal(await style_of(page, `${plain} .needs-tag`, "font-size"), "10px");
    assert.equal(
        await style_of(page, `${plain} .needs-meta`, "font-size"),
        "13px",
        "meta text size",
    );
    assert.equal(await style_of(page, `${plain} .needs-text`, "font-size"), "16px", "text size");
    assert.equal(await style_of(page, `${plain} .needs-text`, "font-weight"), "700", "text size");
    const chip = `${card_selector("approval:a1")} .needs-attachment`;
    assert.equal(await style_of(page, chip, "font-size"), "12px", "chip text");
    assert.equal(await style_of(page, chip, "border-top-color"), "rgb(226, 223, 215)");
    assert_near(
        Number.parseFloat(await style_of(page, chip, "border-radius")),
        8,
        0.05,
        "chip radius",
    );
    // The emoji comes from another font, and its line is a little taller.
    assert_near((await box_of(page, chip)).height, 25, 3.5, "chip height");
    assert.equal(await page.$eval(chip, (node) => node.textContent), "📎 PR #214 · 18 file");

    // Buttons: the second action is flat, the main action is ink.
    const secondary = `${plain} .needs-card-secondary`;
    const primary = `${plain} .needs-card-primary`;
    assert.equal(await style_of(page, secondary, "font-weight"), "700", "second button weight");
    assert.equal(await style_of(page, secondary, "background-color"), "rgb(255, 250, 240)");
    assert.equal(await style_of(page, secondary, "color"), "rgb(22, 22, 29)", "explicit color");
    assert.equal(await style_of(page, primary, "font-weight"), "800", "main button weight");
    assert.equal(await style_of(page, primary, "background-color"), "rgb(22, 22, 29)");
    assert.equal(await style_of(page, primary, "color"), "rgb(255, 250, 240)");
    await page.hover(primary);
    assert.equal(await style_of(page, primary, "background-color"), "rgb(22, 199, 132)", "hover");
    assert.equal(await style_of(page, primary, "color"), "rgb(22, 22, 29)");
    await page.hover(secondary);
    assert.equal(await style_of(page, secondary, "background-color"), "rgb(255, 216, 77)", "hover");
    // A link that looks like a button: no underline, under the pointer or with focus.
    const link = `${card_selector("approval:a1")} a.needs-btn`;
    await page.hover(link);
    assert.equal(await style_of(page, link, "text-decoration-line"), "none", "link under pointer");
    assert.equal(await style_of(page, link, "background-color"), "rgb(255, 216, 77)", "link hover");
    assert.equal(await style_of(page, link, "color"), "rgb(22, 22, 29)", "link hover text");
    await page.mouse.move(0, 0);
    await page.focus(link);
    assert.equal(await style_of(page, link, "text-decoration-line"), "none", "link with focus");
    assert.equal(await style_of(page, link, "color"), "rgb(22, 22, 29)", "link focus text");
    await page.evaluate(() => {
        if (document.activeElement instanceof HTMLElement) {
            document.activeElement.blur();
        }
    });
    await common.screenshot(page, "needs-light-desktop");

    // The done list: label 14 high, rows 38 high, checked box 16.
    assert_near((await box_of(page, ".needs-done-label")).height, 14, 2, "done label height");
    assert.equal(await style_of(page, ".needs-done-label", "font-size"), "11px");
    const row = ".needs-done-row";
    assert_near((await box_of(page, row)).height, 38, 2, "done row height");
    assert_near((await box_of(page, row)).width, 960, 0.5, "done row width");
    assert.equal(await style_of(page, row, "background-color"), "rgb(255, 246, 230)");
    assert.equal(await style_of(page, row, "border-radius"), "12px");
    assert_near((await box_of(page, `${row} .needs-done-check`)).width, 16, 0.5, "done check size");
    assert.equal(
        await style_of(page, `${row} .needs-done-text`, "text-decoration-line"),
        "line-through",
    );
    assert_near(
        Number.parseFloat(await style_of(page, `${row} .needs-done-room`, "font-size")),
        10,
        0.05,
        "done room size",
    );
    assert.equal(
        await page.$eval(`${row} .needs-done-room`, (node) => node.textContent),
        "# Verona",
    );
}

// The same class names as web/src/theme.ts sets on the root element.
async function set_theme(page: Page, scheme: string): Promise<void> {
    await page.evaluate((value) => {
        const root = document.documentElement;
        root.classList.remove("color-scheme-automatic", "dark-theme");
        if (value === "dark") {
            root.classList.add("dark-theme");
        }
    }, scheme);
}

// The text and background colors of every part of the page must have
// a contrast of 4.5 or better (3 for the large title), in both themes.
async function test_contrast(page: Page): Promise<void> {
    const parts: [string, number][] = [
        [".needs-title", 3],
        [".needs-eyebrow", 4.5],
        [".needs-tab-active", 4.5],
        ['.needs-tab[data-tab="approval"]', 4.5],
        [".needs-tag-approval", 4.5],
        [".needs-tag-mention", 4.5],
        [".needs-tag-decision", 4.5],
        [".needs-meta", 4.5],
        [".needs-text", 4.5],
        [".needs-attachment", 4.5],
        [".needs-card-secondary", 4.5],
        [".needs-card-primary", 4.5],
        [".needs-btn-expired", 4.5],
        [".needs-done-label", 4.5],
        [".needs-done-text", 4.5],
        [".needs-done-room", 4.5],
    ];
    for (const scheme of ["light", "dark"]) {
        await set_theme(page, scheme);
        await common.screenshot(page, `needs-${scheme}-contrast`);
        const ratios = await page.evaluate(
            (selectors) => {
                type Rgba = [number, number, number, number];
                const parse = (value: string): Rgba => {
                    const numbers = value.match(/[\d.]+/g)!.map(Number);
                    return [numbers[0]!, numbers[1]!, numbers[2]!, numbers[3] ?? 1];
                };
                const background = (element: Element | null): Rgba => {
                    for (let node = element; node !== null; node = node.parentElement) {
                        const value = parse(window.getComputedStyle(node).backgroundColor);
                        if (value[3] > 0) {
                            return value;
                        }
                    }
                    return [255, 255, 255, 1];
                };
                const luminance = ([red, green, blue]: Rgba): number => {
                    const channel = (value: number): number => {
                        const unit = value / 255;
                        return unit <= 0.039_28 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4;
                    };
                    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue);
                };
                return selectors.map((selector) => {
                    const element = document.querySelector(selector);
                    if (element === null) {
                        return {selector, ratio: -1};
                    }
                    const text = parse(window.getComputedStyle(element).color);
                    const light = Math.max(luminance(text), luminance(background(element)));
                    const dark = Math.min(luminance(text), luminance(background(element)));
                    return {selector, ratio: (light + 0.05) / (dark + 0.05)};
                });
            },
            parts.map(([selector]) => selector),
        );
        for (const [index, {selector, ratio}] of ratios.entries()) {
            const minimum = parts[index]![1];
            assert.ok(ratio !== -1, `${selector} is not on the page`);
            assert.ok(
                ratio >= minimum,
                `${selector} has a contrast of ${ratio.toFixed(2)} in the ${scheme} theme, under ${minimum}`,
            );
        }
        // The flat button turns yellow under the pointer, with dark text in both themes.
        for (const flat of [".needs-card-secondary", "a.needs-btn"]) {
            await page.hover(flat);
            assert.equal(
                await style_of(page, flat, "color"),
                "rgb(22, 22, 29)",
                `hover text of ${flat} in the ${scheme} theme`,
            );
        }
    }
    await page.evaluate(() => {
        document.documentElement.classList.remove("dark-theme");
        document.documentElement.classList.add("color-scheme-automatic");
    });
}

async function test_filters_and_states(page: Page, stream_id: number): Promise<void> {
    const tabs = await page.$$eval(".needs-tab", (nodes) => nodes.map((node) => node.textContent));
    assert.deepEqual(tabs, ["All · 7", "Approval · 4", "Mention · 1", "Decision · 2"]);
    // The sidebar entry marks this page, and shows how many items wait.
    await page.waitForSelector('.sanji-nav-item-active[data-nav-id="needs"]', {visible: true});
    assert.equal(await sidebar_badge(page), "7", "sidebar count");
    assert.match(
        await page.$eval(".needs-eyebrow", (node) => node.textContent ?? ""),
        /^FROM ALL ROOMS IN \S/,
    );
    assert.equal(
        await page.$eval(".needs-done-label", (node) => node.textContent),
        "DONE TODAY · 2",
    );

    await click_tab(page, "approval");
    assert.deepEqual(await card_ids(page), [
        "approval:a1",
        "approval:a2",
        "approval:a3",
        "approval:a4",
    ]);
    await click_tab(page, "mention");
    assert.deepEqual(await card_ids(page), ["mention:101"]);
    await click_tab(page, "decision");
    assert.deepEqual(await card_ids(page), ["decision:job-d1", "decision:job-d2"]);
    await click_tab(page, "all");
    assert.equal((await card_ids(page)).length, 7);

    // An expired approval cannot be approved, and says why.
    const expired = card_selector("approval:a2");
    assert.equal(
        await page.$eval(`${expired} .needs-btn-expired`, (node) => node.textContent),
        "Expired. Ask the agent to try again.",
    );
    assert.equal(
        await page.$eval(`${expired} .needs-btn-expired`, (node) =>
            node.getAttribute("aria-disabled"),
        ),
        "true",
    );
    assert.equal(await page.$(`${expired} .needs-card-primary`), null);

    // Where the second action leads.
    const room_hash = `#narrow/channel/${stream_id}-Verona/topic/release`;
    assert.equal(
        await page.$eval(`${card_selector("approval:a1")} a.needs-btn`, (node) => node.textContent),
        "View",
    );
    assert.equal(
        await page.$eval(`${card_selector("approval:a1")} a.needs-btn`, (node) =>
            node.getAttribute("href"),
        ),
        room_hash,
    );
    assert.equal(
        await page.$eval(`${card_selector("mention:101")} a.needs-btn`, (node) =>
            node.getAttribute("href"),
        ),
        `${room_hash}/near/101`,
    );
    assert.equal(
        await page.$eval(
            `${card_selector("mention:101")} .needs-card-primary`,
            (node) => node.textContent,
        ),
        "Mark done",
    );

    // A question with no choices only offers a way to answer it.
    const open_question = card_selector("decision:job-d2");
    assert.equal(await page.$(`${open_question} .needs-card-primary`), null);
    assert.equal(
        await page.$eval(`${open_question} a.needs-btn`, (node) => node.textContent),
        "Reply",
    );
}

// Undo, then three actions in one window, then a refused action that
// the person tries again.
async function test_actions(page: Page, server: FakeServer): Promise<void> {
    const undone = card_selector("decision:job-d1");
    await page.click(`${undone} .needs-card-primary`);
    await page.waitForSelector(undone, {hidden: true});
    assert.equal(
        await page.$eval(".sj-toast__text", (node) => node.textContent),
        "Version A chosen. Matcha continues working on it.",
    );
    assert.equal(await page.$eval(".needs-tab-active", (node) => node.textContent), "All · 6");
    await page.click(".sj-toast__action");
    await page.waitForSelector(undone, {visible: true});
    assert.equal(await page.$eval(".needs-tab-active", (node) => node.textContent), "All · 7");
    assert.equal(await page.$eval(".sj-toast__text", (node) => node.textContent), "Cancelled.");

    await page.click(`${card_selector("approval:a1")} .needs-card-primary`);
    await page.click(`${card_selector("mention:101")} .needs-card-primary`);
    await page.click(`${undone} .needs-card-secondary`);
    await page.waitForSelector(undone, {hidden: true});
    assert.deepEqual(await card_ids(page), [
        "approval:a2",
        "approval:a3",
        "approval:a4",
        "decision:job-d2",
    ]);
    assert.equal(await page.$eval(".needs-tab-active", (node) => node.textContent), "All · 4");
    assert.equal(await sidebar_badge(page), "4", "sidebar count follows the list at once");
    assert.deepEqual(server.calls, [], "nothing is sent inside the window");

    await wait_for_calls(server, 3);
    await sleep(UNDO_WINDOW_MS / 2);
    assert.equal(server.calls.length, 3, "the action that was undone is not sent again");
    const sent = (path: string): Record<string, unknown> | undefined =>
        server.calls.find((call) => call.path === path)?.payload;
    assert.deepEqual(sent("/json/agent/approvals/a1/decision"), {
        schema_version: 1,
        expected_version: 1,
        operation_hash: "hash-a1",
        nonce: "nonce-a1",
        decision: "approved",
    });
    const answer_payload = sent("/json/agent/jobs/job-d1/inputs");
    assert.equal(answer_payload?.["text"], "Version B");
    assert.equal(answer_payload?.["input_type"], "answer");
    assert.equal(answer_payload?.["expected_version"], 3);
    assert.ok(sent("/json/needs/mentions/101/resolve") !== undefined);

    // The three items are in the done list now, newest first.
    await page.waitForFunction(() => document.querySelectorAll(".needs-done-row").length === 5);
    assert.equal(
        await page.$eval(".needs-done-label", (node) => node.textContent),
        "DONE TODAY · 5",
    );

    // The mention tab has nothing left: the empty state, 88 high.
    await click_tab(page, "mention");
    await page.waitForSelector(".needs-empty");
    assert.equal(
        await page.$eval(".needs-empty-text", (node) => node.textContent),
        "Nothing is waiting in this category.",
    );
    await common.screenshot(page, "needs-empty");
    const empty_box = await box_of(page, ".needs-empty");
    assert_near(empty_box.height, 88, 2, "empty state height");
    assert_near(empty_box.width, 960, 0.5, "empty state width");
    assert.equal(await style_of(page, ".needs-empty", "background-color"), "rgb(210, 247, 229)");
    assert.equal(
        await style_of(page, ".needs-empty-icon", "background-color"),
        "rgb(22, 199, 132)",
    );
    await click_tab(page, "all");

    // The server refuses an action: the card comes back with a message
    // and a way to try again.
    server.refuse.add("approval:a3");
    const refused = card_selector("approval:a3");
    await page.click(`${refused} .needs-card-primary`);
    await page.waitForSelector(refused, {hidden: true});
    await page.waitForSelector(".sj-toast--error", {visible: true, timeout: UNDO_WINDOW_MS * 2});
    assert.equal(
        await page.$eval(".sj-toast--error .sj-toast__text", (node) => node.textContent),
        "Could not save. Try again.",
    );
    await page.waitForSelector(refused, {visible: true});
    assert.equal(await page.$eval(".needs-tab-active", (node) => node.textContent), "All · 4");

    server.refuse.clear();
    await page.click(".sj-toast--error .sj-toast__action");
    await page.waitForSelector(refused, {hidden: true});
    await page.waitForSelector(`.needs-done-row[data-need-id="approval:a3"]`, {
        visible: true,
        timeout: UNDO_WINDOW_MS * 2,
    });
}

// A push notification opens the page at an approval and asks once.
async function test_confirm_link(page: Page, server: FakeServer): Promise<void> {
    const calls_before = server.calls.length;
    await common.go_to_hash(page, "#needs/approval:a4/confirm");
    await common.wait_for_micromodal_to_open(page);
    assert.match(
        await common.get_text_from_selector(page, ".micromodal .modal__title"),
        /^\s*Confirm this approval\s*$/,
    );
    assert.match(
        await common.get_text_from_selector(page, ".micromodal .modal__content"),
        /Kaki asks you to approve: Bagikan dokumen ke klien\./,
    );
    await page.click(".micromodal .dialog_submit_button");
    await common.wait_for_micromodal_to_close(page);
    await page.waitForSelector(card_selector("approval:a4"), {hidden: true});
    await wait_for_calls(server, calls_before + 1);
    assert.equal(server.calls.at(-1)?.path, "/json/agent/approvals/a4/decision");

    // A link to an item that no longer waits gives a short message.
    await common.go_to_hash(page, "#needs/approval:gone/confirm");
    await page.waitForFunction(() =>
        document.querySelector(".sj-toast__text")?.textContent?.includes("no longer waits"),
    );
    assert.equal(await page.$(".modal--open"), null);
}

// How far any part of the page reaches past the right edge of the window.
async function overflow_of(page: Page): Promise<number> {
    return await page.evaluate(() => {
        let furthest = 0;
        for (const element of document.querySelectorAll("#needs-view *")) {
            furthest = Math.max(furthest, element.getBoundingClientRect().right);
        }
        return furthest - window.innerWidth;
    });
}

// The page at the width of a phone, a tablet, and a wide screen.
async function test_widths(page: Page): Promise<void> {
    await common.go_to_hash(page, "#needs");
    await page.waitForSelector(".needs-card");

    await page.setViewport({width: 390, height: 844});
    // The sidebar slides out of a narrow window. Wait for it to be gone.
    await sleep(600);
    const phone_padding = await style_of(page, ".needs-page", "padding-left");
    assert.equal(phone_padding, "14px", "14px gutter under 820px");
    await common.screenshot(page, "needs-light-phone");
    await set_theme(page, "dark");
    await common.screenshot(page, "needs-dark-phone");
    await set_theme(page, "light");
    const overflow = await overflow_of(page);
    assert.ok(overflow <= 0.5, `a part of the page is ${overflow}px past the window at 390px`);
    const card = card_selector("approval:a2");
    const body_box = await box_of(page, `${card} .needs-body`);
    const actions_box = await box_of(page, `${card} .needs-actions`);
    assert.ok(
        actions_box.y >= body_box.y + body_box.height - 1,
        "the actions sit under the text on a phone",
    );
    for (const height of await page.$$eval(".needs-btn, .needs-tab", (nodes) =>
        nodes.map((node) => node.getBoundingClientRect().height),
    )) {
        assert.ok(height >= 35.5, `a touch target is ${height}px high, under 36px`);
    }

    await page.setViewport({width: 819, height: 900});
    assert.equal(await style_of(page, ".needs-page", "padding-left"), "14px");
    await page.setViewport({width: 820, height: 900});
    assert.equal(await style_of(page, ".needs-page", "padding-left"), "32px");
    const tablet_overflow = await overflow_of(page);
    assert.ok(tablet_overflow <= 0.5, `a part is ${tablet_overflow}px past the window at 820px`);

    await page.setViewport({width: 1920, height: 1080});
    const wide_box = await box_of(page, ".needs-page");
    assert_near(wide_box.width, 1024, 0.5, "the page stops growing at 1024px");
    await page.setViewport({width: common.window_size.width, height: common.window_size.height});
}

// The page while the list loads, when the list does not load, and
// once it loads again.
async function test_loading_states(page: Page, server: FakeServer): Promise<void> {
    server.list_delay_ms = 1500;
    await page.reload();
    await page.waitForSelector(".needs-skeleton.sj-skeleton", {visible: true});
    await common.screenshot(page, "needs-loading");
    assert.equal(
        await page.$eval(".needs-tab-active", (node) => node.textContent),
        "All",
        "the tabs show no number until the list is here",
    );
    await page.waitForSelector(".needs-card", {visible: true});
    server.list_delay_ms = 0;

    server.fail_list = true;
    await page.reload();
    await page.waitForSelector(".needs-retry", {visible: true});
    await common.screenshot(page, "needs-load-error");
    assert.equal(
        await page.$eval(".needs-empty-text", (node) => node.textContent),
        "Your list did not load.",
    );
    server.fail_list = false;
    await page.click(".needs-retry");
    await page.waitForSelector(".needs-card", {visible: true});
    assert.equal(await page.$(".needs-retry"), null);
}

async function test_fake_list(page: Page): Promise<void> {
    const stream_id = await common.get_stream_id(page, "Verona");
    assert.ok(stream_id !== undefined);
    const server: FakeServer = {
        open: fake_list(stream_id),
        done: fake_done(stream_id),
        calls: [],
        refuse: new Set(),
        fail_list: false,
        list_delay_ms: 0,
    };
    const stop = await serve_fake_list(page, server);
    try {
        await open_needs(page);
        await page.reload();
        await page.waitForSelector(".needs-card", {visible: true});
        await page.waitForSelector(".needs-done-label", {visible: true});

        await test_measure_layout(page);
        await test_contrast(page);
        await test_filters_and_states(page, stream_id);
        await test_widths(page);
        await test_loading_states(page, server);
        await test_actions(page, server);
        await test_confirm_link(page, server);
    } finally {
        await stop();
    }
}

async function test_needs_view(page: Page): Promise<void> {
    const scenario = start_scenario([
        "--owner",
        "iago@zulip.com",
        "--member",
        common.test_credentials.default_user.username,
        "--kind",
        "manage",
    ]);
    await test_approve_from_needs(page, scenario);

    const second_scenario = start_scenario([
        "--owner",
        "iago@zulip.com",
        "--member",
        common.test_credentials.default_user.username,
        "--kind",
        "manage",
    ]);
    await test_reload_during_undo_window(page, second_scenario);

    await test_fake_list(page);
}

await common.run_test(test_needs_view);
