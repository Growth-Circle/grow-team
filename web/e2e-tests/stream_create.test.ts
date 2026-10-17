import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function user_row_selector(page: Page, name: string): Promise<string> {
    const user_id = await common.get_user_id_from_name(page, name);
    const selector = `.settings-subscriber-row[data-user-id="${user_id}"]`;
    return selector;
}

async function await_user_visible(page: Page, name: string): Promise<void> {
    const selector = await user_row_selector(page, name);
    await page.waitForSelector(selector, {visible: true});
}

async function await_user_hidden(page: Page, name: string): Promise<void> {
    const selector = await user_row_selector(page, name);
    await page.waitForSelector(selector, {hidden: true});
}

async function add_user_to_stream(page: Page, name: string): Promise<void> {
    const user_id = await common.get_user_id_from_name(page, name);
    assert.ok(user_id !== undefined);
    await page.evaluate((user_id) => {
        zulip_test.add_user_id_to_new_stream(user_id);
    }, user_id);
    await await_user_visible(page, name);
}

async function stream_name_error(page: Page): Promise<string> {
    await page.waitForSelector("#stream_name_error", {visible: true});
    return await common.get_text_from_selector(page, "#stream_name_error");
}

async function click_create_new_stream(page: Page): Promise<void> {
    await page.click("#add_new_subscription .create_stream_button");
    await page.type("#create_stream_name", "Test Stream 1");
    await page.waitForSelector(".finalize_create_stream", {visible: true});

    // sanity check that desdemona is the initial subsscriber
    await await_user_visible(page, "desdemona");
}

async function topics_policy_value(page: Page): Promise<string> {
    return await page.$eval("select#id_new_topics_policy", (select) => select.value);
}

async function test_chat_type_choice(page: Page): Promise<void> {
    // By topic is the default and follows the organization.
    await page.waitForSelector('input[name="create_stream_chat_type"][value="topic"]:checked');
    assert.strictEqual(await topics_policy_value(page), "inherit");

    await page.click('input[name="create_stream_chat_type"][value="flat"]');
    assert.strictEqual(await topics_policy_value(page), "empty_topic_only");

    await page.click('input[name="create_stream_chat_type"][value="topic"]');
    assert.strictEqual(await topics_policy_value(page), "inherit");
}

async function test_visibility_choice(page: Page): Promise<void> {
    // Only members is the default, and announcing is off for it.
    await page.waitForSelector('input[name="create_stream_visibility"][value="private"]:checked');
    await page.waitForSelector("#id_should_announce_new_stream:disabled");

    await page.click('input[name="create_stream_visibility"][value="public"]');
    await page.waitForSelector("#id_should_announce_new_stream:not(:disabled)");

    await page.click('input[name="create_stream_visibility"][value="private"]');
    await page.waitForSelector("#id_should_announce_new_stream:disabled");
}

async function test_all_team_button(page: Page): Promise<void> {
    const button_text = await common.get_text_from_selector(page, "#create_stream_add_all_team");
    const team_size = Number(/\((\d+)\)/.exec(button_text)?.[1]);
    assert.ok(team_size > 1, button_text);

    await page.click("#create_stream_add_all_team");
    await page.waitForFunction(
        (team_size) =>
            document.querySelectorAll("#create_stream_subscribers .settings-subscriber-row")
                .length === team_size,
        {},
        team_size,
    );

    // Each member can still be removed on its own.
    const pill_exits = await page.$$("#people_to_add .pill .exit");
    await pill_exits.at(-1)!.click();
    await page.waitForFunction(
        (team_size) =>
            document.querySelectorAll("#create_stream_subscribers .settings-subscriber-row")
                .length ===
            team_size - 1,
        {},
        team_size,
    );
    assert.strictEqual(
        await common.get_text_from_selector(page, "#create_stream_members_count"),
        `${team_size - 1} people`,
    );
}

async function clear_ot_filter_with_backspace(page: Page): Promise<void> {
    await page.click(".add-user-list-filter");
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
}

async function test_user_filter_ui(page: Page): Promise<void> {
    await page.waitForSelector("form#stream_creation_form", {visible: true});
    // Desdemona should be there by default
    await await_user_visible(page, "desdemona");

    await add_user_to_stream(page, common.fullname.cordelia);
    await add_user_to_stream(page, common.fullname.othello);

    await page.type(`form#stream_creation_form [name="user_list_filter"]`, "ot", {delay: 100});
    await page.waitForSelector("#create_stream_subscribers", {visible: true});
    // Wait until filtering is completed.
    await page.waitForFunction(
        () =>
            document.querySelectorAll("#create_stream_subscribers .remove_potential_subscriber")
                .length === 1,
    );

    await await_user_hidden(page, common.fullname.cordelia);
    await await_user_hidden(page, "desdemona");
    await await_user_visible(page, common.fullname.othello);

    // Clear the filter.
    await clear_ot_filter_with_backspace(page);

    await await_user_visible(page, common.fullname.cordelia);
    await await_user_visible(page, "desdemona");
    await await_user_visible(page, common.fullname.othello);
}

async function create_stream(page: Page): Promise<void> {
    // The description lives under "More settings".
    await page.waitForSelector("#create_stream_description", {hidden: true});
    await page.click(".create-channel-more-settings-toggle");
    await page.waitForSelector("#create_stream_description", {visible: true});

    await common.fill_form(page, "form#stream_creation_form", {
        stream_name: "Puppeteer",
        stream_description: "Everything Puppeteer",
    });
    await page.click("form#stream_creation_form .finalize_create_stream");

    // We redirect to the channel message view.
    await page.waitForSelector("#subscription_overlay", {hidden: true});
    await page.waitForSelector(
        `xpath///*[${common.has_class_x("room-header-name")} and text()="# Puppeteer"]`,
    );

    const stream_id = await common.get_stream_id(page, "Puppeteer");
    assert.ok(stream_id !== undefined);
    await common.go_to_hash(page, `#channels/${stream_id}/Puppeteer`);
    await page.waitForSelector(".stream_section");
    await page.waitForSelector(
        `xpath///*[${common.has_class_x("stream-name-title")} and text()="Puppeteer"]`,
        {visible: true},
    );
    const stream_name = await common.get_text_from_selector(
        page,
        ".selected-stream-title .stream-name-title",
    );
    const stream_description = await common.get_text_from_selector(
        page,
        ".stream-description .sub-stream-description",
    );
    assert.strictEqual(stream_name, "Puppeteer");
    assert.strictEqual(stream_description, "Everything Puppeteer");

    // Assert subscriber count becomes 3 (cordelia, desdemona, othello)
    await page.waitForSelector(
        `xpath///*[@data-stream-name="Puppeteer"]//*[${common.has_class_x(
            "subscriber-count",
        )} and normalize-space()="3"]`,
    );
}

async function test_streams_with_empty_names_cannot_be_created(page: Page): Promise<void> {
    await page.click("#add_new_subscription .create_stream_button");
    await page.waitForSelector("form#stream_creation_form", {visible: true});
    await common.fill_form(page, "form#stream_creation_form", {stream_name: "  "});
    await page.click("form#stream_creation_form .finalize_create_stream");
    assert.strictEqual(await stream_name_error(page), "Choose a name for the new channel.");
}

async function test_streams_with_duplicate_names_cannot_be_created(page: Page): Promise<void> {
    await common.fill_form(page, "form#stream_creation_form", {stream_name: "Puppeteer"});
    await page.click("form#stream_creation_form .finalize_create_stream");
    assert.strictEqual(await stream_name_error(page), "A channel with this name already exists.");

    const cancel_button_selector = "form#stream_creation_form button.create_stream_cancel";
    await page.click(cancel_button_selector);
}

async function test_stream_creation(page: Page): Promise<void> {
    await click_create_new_stream(page);
    await test_chat_type_choice(page);
    await test_visibility_choice(page);
    await test_all_team_button(page);

    // The form starts again with only the creator when it is reopened.
    await page.click("form#stream_creation_form button.create_stream_cancel");
    await click_create_new_stream(page);
    await test_user_filter_ui(page);
    await create_stream(page);
    await test_streams_with_empty_names_cannot_be_created(page);
    await test_streams_with_duplicate_names_cannot_be_created(page);
}

async function test_streams_search_feature(page: Page): Promise<void> {
    assert.strictEqual(await common.get_text_from_selector(page, "#search_stream_name"), "");
    const hidden_streams_selector = ".stream-row.notdisplayed .stream-name";
    assert.strictEqual(
        await common.get_text_from_selector(
            page,
            '.stream-row[data-stream-name="Verona"] .stream-name',
        ),
        "Verona",
    );
    assert.ok(
        !(await common.get_text_from_selector(page, hidden_streams_selector)).includes("Verona"),
        "#Verona is hidden",
    );

    await page.type('#stream_filter input[type="text"]', "Puppeteer");
    await page.waitForSelector(".stream-row[data-stream-name='core team']", {hidden: true});
    assert.strictEqual(
        await common.get_text_from_selector(page, ".stream-row:not(.notdisplayed) .stream-name"),
        "Puppeteer",
    );
    assert.ok(
        (await common.get_text_from_selector(page, hidden_streams_selector)).includes("Verona"),
        "#Verona is not hidden",
    );
    assert.ok(
        !(await common.get_text_from_selector(page, hidden_streams_selector)).includes("Puppeteer"),
        "Puppeteer is hidden after searching.",
    );
}

async function subscriptions_tests(page: Page): Promise<void> {
    await common.log_in(page);
    await common.open_streams_modal(page);
    await test_stream_creation(page);
    await test_streams_search_feature(page);
}

await common.run_test(subscriptions_tests);
