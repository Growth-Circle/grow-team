import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

// The measured sizes of the sidebar at 1600x1000 (map-mockup-a.md 4.6),
// each within 1px.

type Box = {x: number; y: number; width: number; height: number};

async function get_box(page: Page, selector: string): Promise<Box> {
    return await page.$eval(selector, (element) => {
        const rect = element.getBoundingClientRect();
        return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
    });
}

// A test reports every size that is wrong, not only the first one.
const size_errors: string[] = [];

function assert_box(actual: Box, expected: Partial<Box>, label: string): void {
    for (const [key, value] of Object.entries(expected)) {
        const measured = actual[key as keyof Box];
        if (Math.abs(measured - value) > 1) {
            size_errors.push(`${label}: ${key} is ${measured}, expected ${value} (within 1px)`);
        }
    }
}

// The menus and the modal fade in for 200 ms, so a picture waits for the end.
async function screenshot_after_animation(page: Page, name: string): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, 400));
    await common.screenshot(page, name);
}

async function wait_for_toast(page: Page, text: string): Promise<void> {
    await page.waitForFunction(
        (expected) =>
            [...document.querySelectorAll(".sj-toast__text")].some((element) =>
                element.textContent?.includes(expected),
            ),
        {},
        text,
    );
}

async function dismiss_banners(page: Page): Promise<void> {
    // A banner above the page pushes the sidebar down, and the measured
    // places assume there is none.
    for (const close_button of await page.$$("#navbar_alerts_wrapper .banner-close-action")) {
        await close_button.click();
    }
    await page.waitForFunction(
        () => document.querySelector("#navbar_alerts_wrapper .banner-close-action") === null,
    );
}

async function check_sizes(page: Page): Promise<void> {
    console.log("Measuring the sidebar");
    await page.setViewport({width: 1600, height: 1000});
    await dismiss_banners(page);
    await common.go_to_hash(page, "#tasks");
    await page.waitForSelector("#left-sidebar-workspace-button", {visible: true});
    await page.waitForSelector("#sidebar-user-card-button", {visible: true});

    assert_box(await get_box(page, "#left-sidebar"), {x: 0, y: 0, width: 260, height: 1000}, "sidebar");
    assert_box(
        await get_box(page, "#left-sidebar-workspace-button"),
        {x: 12, y: 14, width: 236, height: 54},
        "workspace button",
    );
    assert_box(
        await get_box(page, ".workspace-switcher-avatar"),
        {x: 20, y: 22, width: 38, height: 38},
        "workspace avatar",
    );
    // The search slot holds the palette button or, until the palette is
    // ready, the filter for rooms.
    const search_selector = (await page.$("#left-sidebar-command-search:not(.hidden)"))
        ? "#left-sidebar-command-search"
        : "#left-sidebar-search .input-element-wrapper";
    assert_box(await get_box(page, search_selector), {x: 12, y: 78, width: 236, height: 38}, "search");
    assert_box(await get_box(page, "#left-sidebar-navigation-area"), {x: 0, y: 126, width: 260}, "nav");
    assert_box(
        await get_box(page, ".sanji-nav-item:not(.hidden) .sanji-nav-link"),
        {x: 8, y: 130, width: 244, height: 37},
        "first nav item",
    );
    assert_box(await get_box(page, "#sidebar-rooms-header"), {x: 8, width: 244, height: 34}, "rooms header");
    assert_box(
        await get_box(page, ".stream-list-section-container:not(.no-display) .stream-list-subsection-header"),
        {x: 8, width: 244, height: 25},
        "group label",
    );
    assert_box(
        await get_box(page, ".stream-list-section-container:not(.no-display) .narrow-filter .subscription_block"),
        {x: 8, width: 244, height: 32},
        "room row",
    );
    assert_box(
        await get_box(page, "#sidebar-user-card-button"),
        {x: 0, y: 942, width: 260, height: 58},
        "user card",
    );
    await common.screenshot(page, "sidebar-1600x1000");
    assert.deepEqual(size_errors, []);
}

async function check_workspace_switcher(page: Page): Promise<void> {
    console.log("Opening the workspace switcher");
    await page.click("#left-sidebar-workspace-button");
    await page.waitForSelector(".workspace-switcher-dropdown", {visible: true});
    await page.waitForSelector(".workspace-switcher-item", {visible: true});
    assert.equal(
        await page.$eval("#left-sidebar-workspace-button", (button) =>
            button.getAttribute("aria-expanded"),
        ),
        "true",
    );
    assert_box(
        await get_box(page, ".workspace-switcher-dropdown"),
        {x: 12, y: 64, width: 236},
        "workspace dropdown",
    );
    // The current workspace is the one with the check mark.
    const current_rows = await page.$$(".workspace-switcher-item-current .workspace-switcher-item-check");
    assert.equal(current_rows.length, 1);
    await screenshot_after_animation(page, "sidebar-workspace-switcher");

    // A click outside closes the list.
    await page.mouse.click(900, 500);
    await page.waitForSelector(".workspace-switcher-dropdown", {hidden: true});

    // So does Escape.
    await page.click("#left-sidebar-workspace-button");
    await page.waitForSelector(".workspace-switcher-dropdown", {visible: true});
    await page.keyboard.press("Escape");
    await page.waitForSelector(".workspace-switcher-dropdown", {hidden: true});
}

async function check_user_card(page: Page): Promise<void> {
    console.log("Opening the user card menu");
    await page.click("#sidebar-user-card-button");
    await page.waitForSelector(".sidebar-user-card-dropdown", {visible: true});
    assert_box(
        await get_box(page, ".sidebar-user-card-dropdown"),
        {x: 12, width: 252},
        "user card menu",
    );
    assert_box(
        await get_box(page, ".sidebar-user-card-item"),
        {width: 236, height: 36},
        "user card menu item",
    );
    const labels = await page.$$eval(".sidebar-user-card-item", (items) =>
        items.map((item) => item.textContent?.trim()),
    );
    assert.equal(labels.at(0), "My profile");
    assert.equal(labels.at(-1), "Log out");
    await screenshot_after_animation(page, "sidebar-user-card");

    await page.mouse.click(900, 500);
    await page.waitForSelector(".sidebar-user-card-dropdown", {hidden: true});
}

async function check_navigation(page: Page): Promise<void> {
    console.log("Using the navigation entries");
    // Entries for pages that do not exist yet stay out of the list.
    const hidden_ids = await page.$$eval(".sanji-nav-item.hidden", (items) =>
        items.map((item) => item.getAttribute("data-nav-id")),
    );
    for (const id of ["needs", "agents", "mcp", "runners", "drive"]) {
        assert.ok(hidden_ids.includes(id), `${id} should be hidden`);
    }

    await common.go_to_hash(page, "#tasks");
    await page.waitForSelector('.sanji-nav-item-active[data-nav-id="tasks"]');
    // The active entry keeps its look under the pointer.
    const link = '.sanji-nav-item[data-nav-id="tasks"] .sanji-nav-link';
    const before = await page.$eval(link, (element) => getComputedStyle(element).backgroundColor);
    await page.hover(link);
    const after = await page.$eval(link, (element) => getComputedStyle(element).backgroundColor);
    assert.equal(after, before);
}

async function check_quiet_rooms(page: Page): Promise<void> {
    console.log("Showing the quiet rooms");
    const stream_id = await common.get_stream_id(page, "Verona");
    assert.ok(stream_id !== undefined);
    const now = Math.floor(Date.now() / 1000);
    await page.setRequestInterception(true);
    const on_request = (request: import("puppeteer").HTTPRequest): void => {
        if (new URL(request.url()).pathname === "/json/channels/quiet") {
            void request.respond({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    result: "success",
                    msg: "",
                    threshold_days: 30,
                    quiet_channels: [{stream_id, last_message_at: now - 42 * 86400}],
                }),
            });
        } else {
            void request.continue();
        }
    };
    page.on("request", on_request);
    await page.reload();
    await page.waitForSelector("#sidebar-quiet-rooms-toggle", {visible: true});
    assert.equal(
        (await common.get_text_from_selector(page, "#sidebar-quiet-rooms-toggle")).replaceAll(/\s+/g, " ").trim(),
        "1 quiet rooms, over 30 days ▼",
    );
    await page.click("#sidebar-quiet-rooms-toggle");
    await page.waitForSelector(".sidebar-quiet-room-row", {visible: true});
    assert.equal(
        await common.get_text_from_selector(page, ".sidebar-quiet-room-name"),
        "# Verona",
    );
    assert.equal(
        await common.get_text_from_selector(page, ".sidebar-quiet-room-idle"),
        "42 days",
    );
    assert_box(await get_box(page, "#sidebar-quiet-rooms-toggle"), {x: 8, width: 244, height: 36}, "quiet toggle");
    assert_box(await get_box(page, ".sidebar-quiet-room-row"), {x: 8, width: 244, height: 28}, "quiet row");
    await common.screenshot(page, "sidebar-quiet-rooms");
    page.off("request", on_request);
    await page.setRequestInterception(false);
}

async function check_create_room(page: Page): Promise<void> {
    console.log("Creating a room");
    await page.click("#left-sidebar-new-room-button");
    await page.waitForSelector("#room_create_name", {visible: true});
    await common.wait_for_micromodal_to_open(page);
    await screenshot_after_animation(page, "sidebar-new-room");

    await page.type("#room_create_name", "Spring Campaign");
    await page.click('.room-create-type-pill[data-room-type="team"]');
    await page.waitForSelector(".room-create-due-date", {hidden: true});
    await page.click('.room-create-type-pill[data-room-type="project"]');
    await page.waitForSelector(".room-create-due-date", {visible: true});
    await page.click(".dialog_submit_button");

    await wait_for_toast(page, "Room # spring-campaign created.");
    await page.waitForFunction(() => document.title.startsWith("#spring-campaign"));
    await page.waitForFunction(
        () =>
            [...document.querySelectorAll("#streams_list .stream-name")].some(
                (element) => element.textContent === "spring-campaign",
            ),
    );
}

async function sidebar_tests(page: Page): Promise<void> {
    await common.log_in(page);
    await check_sizes(page);
    await check_workspace_switcher(page);
    await check_user_card(page);
    await check_navigation(page);
    await check_create_room(page);
    await check_quiet_rooms(page);
}

await common.run_test(sidebar_tests);
