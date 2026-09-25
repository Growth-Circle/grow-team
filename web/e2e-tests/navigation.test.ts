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
}

await common.run_test(navigation_tests);
