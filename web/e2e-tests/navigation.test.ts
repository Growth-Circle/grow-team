import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

// The sanji navbar hides #message_view_header (message_view_header.css:
// "the top bar holds no view title or breadcrumb"), so a narrow change shows
// through the browser tab title instead of a header icon. The title updates
// only once the new narrow is in place (narrow_title.ts), including for a
// view with no messages of its own (e.g. this user's direct message feed),
// so it stays a reliable signal where a wait for a message row would not.
async function wait_for_title(page: Page, title_prefix: string): Promise<void> {
    await page.waitForFunction((prefix) => document.title.startsWith(prefix), {}, title_prefix);
}

async function navigate_to_channel(page: Page, stream_name: string): Promise<void> {
    console.log("Visiting #" + stream_name);
    const stream_id = await common.get_stream_id(page, stream_name);
    assert.ok(stream_id !== undefined);
    await common.go_to_hash(page, `#narrow/channel/${stream_id}-${stream_name}`);
    await wait_for_title(page, `#${stream_name}`);
}

async function navigate_to_all_messages(page: Page): Promise<void> {
    await common.go_to_hash(page, "#feed");
    await wait_for_title(page, "Combined feed");
}

async function navigate_to_settings(page: Page): Promise<void> {
    console.log("Navigating to settings");

    await common.open_personal_settings(page);

    const profile_section_tab_selector = "li[data-section='profile']";
    await page.waitForSelector(profile_section_tab_selector, {visible: true});
    await page.click(profile_section_tab_selector);
    await page.waitForSelector(`${profile_section_tab_selector}:focus`, {visible: true});

    await page.click("#settings_page .content-wrapper .exit");
    // Wait until the overlay is completely closed.
    await page.waitForSelector("#settings_overlay_container", {hidden: true});
}

async function navigate_to_subscriptions(page: Page): Promise<void> {
    console.log("Navigate to subscriptions");

    await common.go_to_hash(page, "#channels");
    await page.waitForSelector("#subscription_overlay", {visible: true});

    await page.click("#subscription_overlay .exit");
    // Wait until the overlay is completely closed.
    await page.waitForSelector("#subscription_overlay", {hidden: true});
}

async function navigate_to_private_messages(page: Page): Promise<void> {
    console.log("Navigate to direct messages");

    await common.go_to_hash(page, "#narrow/is/dm");
    await wait_for_title(page, "Direct message feed");
}

async function test_reload_hash(page: Page): Promise<void> {
    const initial_page_load_time = await page.evaluate(() => zulip_test.page_load_time);
    assert.ok(initial_page_load_time !== undefined);
    console.log(`initial load time: ${initial_page_load_time}`);

    const initial_hash = await page.evaluate(() => window.location.hash);

    await page.evaluate(() => {
        zulip_test.initiate_reload({immediate: true});
    });
    await page.waitForNavigation();
    const message_list_id = await common.get_current_msg_list_id(page, true);
    await page.waitForSelector(`.message-list[data-message-list-id='${message_list_id}']`, {
        visible: true,
    });

    const page_load_time = await page.evaluate(() => zulip_test.page_load_time);
    assert.ok(page_load_time !== undefined);
    assert.ok(page_load_time > initial_page_load_time, "Page not reloaded.");

    const hash = await page.evaluate(() => window.location.hash);
    assert.strictEqual(hash, initial_hash, "Hash not preserved.");
}

// Every container that can fill the main column.
const VIEW_CONTAINERS = [
    "#today-view",
    "#needs-view",
    "#agents-view",
    "#agent-create-view",
    "#drive-view",
    "#runners-view",
    "#runner-add-view",
    "#mcp-view",
    "#workspace-settings-view",
    "#recent_view",
    "#inbox-view",
    "#task-board-view",
    "#message_feed_container",
];

type ViewCase = {
    hash: string;
    container: string;
    title: string;
    // A view without conversations hides the closed compose bar.
    hides_compose_bar: boolean;
};

function view_cases(verona_id: number): ViewCase[] {
    return [
        {hash: "#today", container: "#today-view", title: "Today", hides_compose_bar: true},
        {hash: "#needs", container: "#needs-view", title: "Needs you", hides_compose_bar: true},
        {
            hash: "#needs/1/confirm",
            container: "#needs-view",
            title: "Needs you",
            hides_compose_bar: true,
        },
        {hash: "#agents", container: "#agents-view", title: "Agents", hides_compose_bar: true},
        {
            hash: "#agents/new",
            container: "#agent-create-view",
            title: "Add agent",
            hides_compose_bar: true,
        },
        {
            hash: "#agents/new/coding/1",
            container: "#agent-create-view",
            title: "Add agent",
            hides_compose_bar: true,
        },
        {hash: "#drive", container: "#drive-view", title: "Drive", hides_compose_bar: true},
        {
            hash: `#drive/room/${verona_id}`,
            container: "#drive-view",
            title: "Drive",
            hides_compose_bar: true,
        },
        {hash: "#runners", container: "#runners-view", title: "Runners", hides_compose_bar: true},
        {
            hash: "#runners/new",
            container: "#runner-add-view",
            title: "Add runner",
            hides_compose_bar: true,
        },
        {hash: "#mcp", container: "#mcp-view", title: "MCP connections", hides_compose_bar: true},
        {
            hash: "#mcp/catalog",
            container: "#mcp-view",
            title: "MCP connections",
            hides_compose_bar: true,
        },
        {
            hash: "#workspace-settings/general",
            container: "#workspace-settings-view",
            title: "Settings",
            hides_compose_bar: true,
        },
        {
            hash: "#recent",
            container: "#recent_view",
            title: "Recent conversations",
            hides_compose_bar: false,
        },
        {hash: "#inbox", container: "#inbox-view", title: "Inbox", hides_compose_bar: false},
        {
            hash: "#tasks",
            container: "#task-board-view",
            title: "Task board",
            hides_compose_bar: false,
        },
        {
            hash: "#tasks/mine",
            container: "#task-board-view",
            title: "Task board",
            hides_compose_bar: false,
        },
    ];
}

async function navigate_center_views(page: Page): Promise<void> {
    console.log("Navigating to every view of the main column");

    const verona_id = await common.get_stream_id(page, "Verona");
    assert.ok(verona_id !== undefined);

    for (const view_case of view_cases(verona_id)) {
        await common.go_to_hash(page, view_case.hash);
        await page.waitForSelector(view_case.container, {visible: true});
        await wait_for_title(page, view_case.title);

        // Only one view fills the main column at a time.
        for (const other of VIEW_CONTAINERS) {
            if (other !== view_case.container) {
                await page.waitForSelector(other, {hidden: true});
            }
        }
        if (view_case.hides_compose_bar) {
            await page.waitForSelector("#compose", {hidden: true});
        }
    }

    // #agent-jobs opens the task list overlay over the current view.
    await common.go_to_hash(page, "#agent-jobs");
    await page.waitForSelector("#agent-job-list-overlay", {visible: true});
    await page.keyboard.press("Escape");
    await page.waitForSelector("#agent-job-list-overlay", {hidden: true});
}

type Box = {x: number; y: number; width: number; height: number};

async function get_box(page: Page, selector: string): Promise<Box> {
    return await page.$eval(selector, (element) => {
        const rect = element.getBoundingClientRect();
        return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
    });
}

function assert_near(actual: number, expected: number, label: string): void {
    assert.ok(Math.abs(actual - expected) <= 1, `${label}: ${actual} is not ${expected}`);
}

async function check_desktop_frame(page: Page, width: number, height: number): Promise<void> {
    await page.setViewport({width, height});
    await common.go_to_hash(page, "#feed");
    await wait_for_title(page, "Combined feed");
    await page.waitForSelector("#compose-content", {visible: true});

    const sidebar = await get_box(page, "#left-sidebar-container .left-sidebar");
    assert_near(sidebar.x, 0, `sidebar x at ${width}px`);
    assert_near(sidebar.width, 260, `sidebar width at ${width}px`);
    const middle = await get_box(page, ".app-main .column-middle");
    assert_near(middle.x, 260, `main column x at ${width}px`);
    const client_width = await page.evaluate(() => document.documentElement.clientWidth);
    assert_near(middle.x + middle.width, client_width, `main column right edge at ${width}px`);
    const compose = await get_box(page, "#compose-content");
    assert_near(compose.x, 260, `compose box x at ${width}px`);
    await page.waitForSelector("#app-topbar", {hidden: true});

    // The page root scrolls, not the main column: the message feed
    // reads and sets the scroll position of the root element.
    const main_column_overflow = await page.$eval(
        ".column-middle-inner",
        (element) => window.getComputedStyle(element).overflowY,
    );
    assert.equal(main_column_overflow, "visible");

    await common.screenshot(page, `shell-feed-${width}x${height}`);
}

async function check_mobile_frame(page: Page): Promise<void> {
    await page.setViewport({width: 375, height: 812});
    await common.go_to_hash(page, "#today");
    await page.waitForSelector("#today-view", {visible: true});
    await page.waitForSelector("#app-topbar", {visible: true});

    // Banners, when there are any, sit above the top bar.
    const banners = await get_box(page, "#navbar_alerts_wrapper");
    const topbar = await get_box(page, "#app-topbar");
    assert_near(topbar.x, 0, "top bar x");
    assert_near(topbar.y, banners.height, "top bar y");
    assert_near(topbar.width, 375, "top bar width");
    assert_near(topbar.height, 64, "top bar height");
    const menu_button = await get_box(page, "#app-topbar-menu");
    assert_near(menu_button.width, 44, "menu button width");
    assert_near(menu_button.height, 44, "menu button height");
    const search_button = await get_box(page, "#app-topbar-search");
    assert_near(search_button.width, 44, "search button width");
    const avatar = await get_box(page, "#app-topbar-workspace-initial");
    assert_near(avatar.width, 26, "workspace avatar width");
    assert_near(avatar.height, 26, "workspace avatar height");
    // The view content starts below the banners and the top bar.
    const today_padding = await page.$eval("#today-view", (element) =>
        Number.parseFloat(window.getComputedStyle(element).paddingTop),
    );
    assert_near(today_padding, banners.height + 64, "Today top padding");
    await page.waitForSelector("#left-sidebar-container", {hidden: true});
    await common.screenshot(page, "shell-today-375x812");

    await page.click("#app-topbar-menu");
    await page.waitForSelector("#left-sidebar-container", {visible: true});
    await page.waitForSelector("#app-nav-scrim", {visible: true});
    // Wait for the slide to end before the geometry checks.
    await page.waitForFunction(
        () => document.querySelector("#left-sidebar-container")!.getBoundingClientRect().x === 0,
    );
    const drawer = await get_box(page, "#left-sidebar-container");
    assert_near(drawer.width, 300, "drawer width");
    await common.screenshot(page, "shell-drawer-375x812");

    await page.keyboard.press("Escape");
    await page.waitForSelector("#app-nav-scrim", {hidden: true});
    await page.waitForSelector("#left-sidebar-container", {hidden: true});
}

async function check_frame(page: Page): Promise<void> {
    console.log("Checking the app frame at several window sizes");

    await check_desktop_frame(page, 820, 900);
    await check_desktop_frame(page, 1280, 800);
    await check_desktop_frame(page, 1600, 1000);
    await check_desktop_frame(page, 1920, 1080);

    await page.setViewport({width: 1600, height: 1000});
    await common.go_to_hash(page, "#today");
    await page.waitForSelector("#today-view", {visible: true});
    await common.screenshot(page, "shell-today-1600x1000");

    await check_mobile_frame(page);
}

async function navigation_tests(page: Page): Promise<void> {
    await common.log_in(page);

    await navigate_to_settings(page);

    await navigate_to_channel(page, "Verona");

    await navigate_to_all_messages(page);

    await navigate_to_subscriptions(page);

    await navigate_to_all_messages(page);

    await navigate_to_settings(page);
    await navigate_to_private_messages(page);
    await navigate_to_subscriptions(page);
    await navigate_to_channel(page, "Verona");

    await test_reload_hash(page);

    // Verify that we're still narrowed to the target stream after the reload.
    assert.ok((await page.title()).startsWith("#Verona"), "Not narrowed to the Verona channel.");

    await navigate_center_views(page);
    await check_frame(page);
}

await common.run_test(navigation_tests);
