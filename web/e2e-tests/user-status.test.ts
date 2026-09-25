import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function open_set_user_status_modal(page: Page): Promise<void> {
    // The app shell hides the navbar personal menu, so use the
    // Shift+Y shortcut that also opens this dialog.
    await page.keyboard.down("Shift");
    await page.keyboard.press("KeyY");
    await page.keyboard.up("Shift");

    // Wait for the modal to completely open.
    await common.wait_for_micromodal_to_open(page);
}

async function test_user_status(page: Page): Promise<void> {
    await open_set_user_status_modal(page);
    // Check by clicking on common statues.
    await page.click(".user-status-option:nth-child(2) .user-status-value");
    await page.waitForFunction(
        () => document.querySelector<HTMLInputElement>(".user-status")!.value === "In a meeting",
    );
    // It should select calendar emoji.
    await page.waitForSelector(".selected-emoji.emoji-1f4c5");

    // Clear everything.
    await page.click("#clear_status_message_button");
    await page.waitForFunction(
        () => document.querySelector<HTMLInputElement>(".user-status")!.value === "",
    );
    await page.waitForSelector(".status-emoji-wrapper .smiley-icon", {visible: true});

    // Manually adding everything.
    await page.type(".user-status", "Busy");
    const laughing_emoji_selector = ".emoji-1f606";
    await page.click(".status-emoji-wrapper .smiley-icon");
    // Wait until emoji popover is opened.
    await page.waitForSelector(`.emoji-popover ${laughing_emoji_selector}`, {visible: true});
    await page.click(`.emoji-popover  ${laughing_emoji_selector}`);
    await page.waitForSelector(".emoji-picker-popover", {hidden: true});
    await page.waitForSelector(`.selected-emoji${laughing_emoji_selector}`);

    await page.click("#set-user-status-modal .dialog_submit_button");
    // It should close the modal after saving.
    await page.waitForSelector("#set-user-status-modal", {hidden: true});

    // Check if the emoji is added in user presence list.
    await page.waitForSelector(`.user-presence-link .status-emoji${laughing_emoji_selector}`);
}

async function user_status_test(page: Page): Promise<void> {
    await common.log_in(page);
    await test_user_status(page);
}

await common.run_test(user_status_test);
