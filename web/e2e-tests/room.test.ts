import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";

import type {Page} from "puppeteer";
import * as z from "zod/mini";

import * as common from "./lib/common.ts";

const FIXTURE = "web/e2e-tests/fixtures/agent_fake_runner.py";
const REALM_URL = "http://zulip.zulipdev.com:9981/";

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

function start_scenario(owner: string): Scenario {
    const output = execFileSync(
        "python3",
        [FIXTURE, "scenario", "--owner", owner, "--kind", "answer"],
        {encoding: "utf8"},
    );
    return scenario_schema.parse(JSON.parse(output));
}

function room_url(scenario: Scenario, topic?: string): string {
    const base = `${REALM_URL}#narrow/channel/${scenario.stream_id}-e`;
    return topic === undefined ? base : `${base}/topic/${encodeURIComponent(topic)}`;
}

async function open_room(page: Page, scenario: Scenario): Promise<void> {
    await page.goto(room_url(scenario, scenario.topic));
    await page.waitForSelector("#room-header .room-header-name", {visible: true});
    await page.waitForSelector("#room-topics-column .room-topic-item-active", {visible: true});
    await page.waitForSelector(".message_row", {visible: true});
}

async function rect(
    page: Page,
    selector: string,
): Promise<{x: number; y: number; w: number; h: number}> {
    return await page.evaluate((sel) => {
        const box = document.querySelector(sel)!.getBoundingClientRect();
        return {x: box.x, y: box.y, w: box.width, h: box.height};
    }, selector);
}

function assert_close(actual: number, expected: number, tolerance: number, what: string): void {
    assert.ok(
        Math.abs(actual - expected) <= tolerance,
        `${what}: expected ${expected} (+/- ${tolerance}), got ${actual}`,
    );
}

// RM-01..16: the header, the topic column, and the switch.
async function test_header_and_topics(page: Page, scenario: Scenario): Promise<void> {
    await page.setViewport({width: 1600, height: 1000});
    await open_room(page, scenario);

    const name = await common.get_text_from_selector(page, "#room-header .room-header-name");
    assert.ok(name.startsWith("# fake-e2e-"), `The header names the room, got ${name}`);
    const owner_line = await common.get_text_from_selector(page, "#room-header .room-header-owner");
    assert.match(owner_line, /\d+ (people|person) · \d+ agents?/);

    const active = await common.get_text_from_selector(
        page,
        "#room-topics-column .room-topic-item-active",
    );
    assert.ok(active.includes(scenario.topic), "The open topic is marked");
    assert.ok(active.includes("Open"), "The open topic says Open");
    assert.equal(
        await page.$eval("#room-topics-column .room-topic-item-active", (link) =>
            link.getAttribute("aria-current"),
        ),
        "page",
    );

    // The Today and Drive screens are placeholders, so the header has no links to them.
    assert.equal(await page.$(".room-header-back"), null);
    assert.equal(await page.$(".room-header-drive"), null);

    // Measures from the mockup at 1600x1000 (Ruang, terukur).
    // The header sits under the banners, so measure its own bar. The
    // 2px border below it makes the 69px of the mockup.
    await page.evaluate(async () => {
        await document.fonts.ready;
    });
    const header = await rect(page, "#room-header");
    const bar = await rect(page, "#room-header .room-header-bar");
    const topics = await rect(page, "#room-topics-column");
    const first_item = await rect(page, "#room-topics-column .room-topic-item");
    console.log("room measures", JSON.stringify({header, bar, topics, first_item}));
    assert_close(header.x, 260, 1, "header x");
    assert_close(header.w, 1340, 1, "header width");
    assert_close(bar.h + 2, 69, 3, "header height");
    assert_close(topics.w, 240, 1, "topic column width");
    assert_close(first_item.h, 51, 2, "topic item height");
    await common.screenshot(page, "room-1600-light");

    // The switch flips and says so.
    const toggle = "#room-header .room-header-summary-toggle";
    const before = await page.$eval(toggle, (button) => button.getAttribute("aria-checked"));
    await page.click(toggle);
    await page.waitForFunction(
        (selector, old_value) =>
            document.querySelector(selector)?.getAttribute("aria-checked") !== old_value,
        {},
        toggle,
        before,
    );
    await page.click(toggle);
    await page.waitForFunction(
        (selector, old_value) =>
            document.querySelector(selector)?.getAttribute("aria-checked") === old_value,
        {},
        toggle,
        before,
    );
}

// A bare room opens on its top topic.
async function test_bare_room_opens_top_topic(page: Page, scenario: Scenario): Promise<void> {
    await common.go_to_hash(page, `#narrow/channel/${scenario.stream_id}-e`);
    await page.waitForFunction(() => window.location.hash.includes("/topic/"));
    await page.waitForSelector("#room-topics-column .room-topic-item-active", {visible: true});
    const active = await common.get_text_from_selector(
        page,
        "#room-topics-column .room-topic-item-active",
    );
    assert.ok(active.includes(scenario.topic));
}

// Reply in the topic that is open: the selected message sets it.
async function open_reply_compose(page: Page): Promise<void> {
    const is_open = await page.evaluate(
        () => (document.querySelector("#compose-textarea")?.getClientRects().length ?? 0) > 0,
    );
    if (is_open) {
        await page.keyboard.press("Escape");
    }
    await page.keyboard.press("KeyR");
    await page.waitForSelector("#compose-textarea", {visible: true});
}

// RM-24..34, RM-48..52: send a message and mention the agent.
async function test_messages_and_composer(page: Page, scenario: Scenario): Promise<void> {
    await open_room(page, scenario);
    await open_reply_compose(page);

    // The placeholder names the topic and the agent of the room.
    const bot_name = await page.evaluate(
        (id) => zulip_test.get_person_by_user_id(id).full_name,
        scenario.bot_user_id,
    );
    await page.waitForFunction(
        (bot) => {
            const textarea = document.querySelector<HTMLTextAreaElement>("#compose-textarea");
            return (
                textarea !== null &&
                textarea.placeholder.includes("to call an agent") &&
                textarea.placeholder.includes(`@${bot}`)
            );
        },
        {},
        bot_name,
    );
    const placeholder = await page.evaluate(
        () => document.querySelector<HTMLTextAreaElement>("#compose-textarea")!.placeholder,
    );
    assert.ok(placeholder.startsWith(`Write in ${scenario.topic}…`), placeholder);

    // The toolbar is hidden. The attachment button stays.
    assert.ok(await page.$(".compose_upload_file"));
    assert.equal(
        await page.$eval(".compose_upload_file", (button) => button.getClientRects().length > 0),
        true,
        "The attachment button shows",
    );
    assert.equal(
        await page.$eval(
            ".compose_control_button.emoji_map",
            (button) => button.getClientRects().length,
        ),
        0,
        "The emoji button is hidden",
    );

    // A person sends a message: an initials avatar.
    await page.type("#compose-textarea", "Hello from the room");
    await page.click("#compose-send-button");
    await common.wait_for_fully_processed_message(page, "Hello from the room");
    const initials = await page.$eval(
        ".room-avatar-person",
        (avatar) => avatar.textContent?.trim() ?? "",
    );
    assert.match(initials, /^[A-Z]{1,2}$/);

    // Mention the agent: the eyes reaction shows on the message, and
    // the agent's answer has the AGENT label and its own avatar shape.
    await open_reply_compose(page);
    await common.select_item_via_typeahead(page, "#compose-textarea", `@**${bot_name}`, bot_name);
    await page.type("#compose-textarea", " Please give a short answer.");
    await page.click("#compose-send-button");
    // The server adds the reaction while it handles the send. Its event can
    // reach the page before the reply to the send does, and the page drops a
    // reaction on a message it does not know yet. Load the room again to read it.
    await page.waitForSelector("#compose_banners .agent_task_receipt_banner", {visible: true});
    await page.reload();
    await page.waitForSelector(".message_row", {visible: true});
    await page.waitForSelector(".message_reaction .emoji-1f440", {visible: true, timeout: 30000});
    await page.waitForSelector(".message_row .room-agent-label", {visible: true, timeout: 30000});
    // RM-26, RM-32: the default agent is a 34px circle, and its label is small.
    const label = await rect(page, ".room-agent-label");
    assert.ok(label.h < 24, `The AGENT label is small, got ${label.h}px tall`);
    const avatar = await rect(page, ".room-avatar-circle");
    assert_close(avatar.w, 34, 1, "agent avatar width");
    assert_close(avatar.h, 34, 1, "agent avatar height");

    await common.screenshot(page, "room-1600-messages");
}

// A search inside the room and a direct message keep the Zulip look.
async function test_other_narrows_keep_zulip_look(page: Page, scenario: Scenario): Promise<void> {
    await page.goto(`${room_url(scenario)}/search/Hello`);
    await page.waitForSelector(".message_row, .empty-feed-notice-title", {visible: true});
    assert.equal(await page.$eval("#room-header", (header) => header.innerHTML.trim()), "");
    assert.equal(await page.$(".room-avatar"), null, "A search keeps the stock avatar");
    assert.equal(
        await page.$eval(".column-middle-inner", (element) =>
            element.classList.contains("sj-room-active"),
        ),
        false,
    );

    const iago_id = await common.get_user_id_from_name(page, "Iago");
    assert.ok(iago_id !== undefined);
    await common.go_to_hash(page, `#narrow/dm/${iago_id}-iago`);
    await page.waitForFunction(
        () => !document.querySelector(".column-middle-inner")?.classList.contains("sj-room-active"),
    );
    assert.equal(await page.$eval("#room-header", (header) => header.innerHTML.trim()), "");
}

// 390px: the topics become a row of chips, the composer stays at the bottom.
async function test_narrow_window(page: Page, scenario: Scenario): Promise<void> {
    await page.setViewport({width: 390, height: 844});
    await open_room(page, scenario);
    // Load the page again at this size, as a phone does.
    await page.reload();
    await open_room(page, scenario);
    await page.evaluate(() => {
        window.scrollTo(0, 0);
    });
    const direction = await page.$eval(
        "#room-topics-column",
        (column) => window.getComputedStyle(column).flexDirection,
    );
    assert.equal(direction, "row");
    const topics = await rect(page, "#room-topics-column");
    const header = await rect(page, "#room-header");
    assert.ok(topics.y >= header.y + header.h - 1, "The chips sit under the header");
    const item = await rect(page, "#room-topics-column .room-topic-item");
    assert.ok(item.h >= 36, `A chip is at least 36px tall, got ${item.h}`);
    const no_sideways_scroll = await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
    );
    assert.ok(no_sideways_scroll, "The page does not scroll sideways");
    const compose = await rect(page, "#compose");
    assert_close(compose.y + compose.h, 844, 2, "composer bottom");
    await common.screenshot(page, "room-390-light");
}

// Both themes are readable: the header background follows the theme.
async function test_dark_theme(page: Page, scenario: Scenario): Promise<void> {
    await page.setViewport({width: 1600, height: 1000});
    await page.emulateMediaFeatures([{name: "prefers-color-scheme", value: "dark"}]);
    await open_room(page, scenario);
    const colors = await page.evaluate(() => {
        const style = (selector: string): CSSStyleDeclaration =>
            window.getComputedStyle(document.querySelector(selector)!);
        return {
            header_bg: style("#room-header").backgroundColor,
            header_text: style(".room-header-name").color,
            active_bg: style(".room-topic-item-active").backgroundColor,
            active_text: style(".room-topic-item-active").color,
        };
    });
    console.log("dark colors", JSON.stringify(colors));
    assert.notEqual(
        colors.header_bg,
        colors.header_text,
        "Header text differs from its background",
    );
    assert.notEqual(colors.active_bg, colors.active_text, "The open topic is readable");
    await common.screenshot(page, "room-1600-dark");
    await page.emulateMediaFeatures([{name: "prefers-color-scheme", value: "light"}]);
}

async function test_room(page: Page): Promise<void> {
    await common.log_in(page);
    const scenario = start_scenario(common.test_credentials.default_user.username);
    await test_header_and_topics(page, scenario);
    await test_bare_room_opens_top_topic(page, scenario);
    await test_messages_and_composer(page, scenario);
    await test_other_narrows_keep_zulip_look(page, scenario);
    await test_narrow_window(page, scenario);
    await test_dark_theme(page, scenario);
}

await common.run_test(test_room);
