import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

// Checks of the Sanji modal and toast look. The "Delete message?"
// dialog is a real Zulip modal. No page shows a toast at this time,
// thus this test adds the same elements that show_toast
// (feedback_widget.ts) makes, and checks the toast CSS.

type Sample = {x: number; y: number; done: boolean};

function assert_near(actual: number, expected: number, tolerance: number, label: string): void {
    assert.ok(
        Math.abs(actual - expected) <= tolerance,
        `${label}: expected ${expected} (±${tolerance}), got ${actual}`,
    );
}

// Gives the numbers of a computed value, for example "12px 14px" or
// "rgba(22, 22, 29, 0.5)".
function numbers(value: string): number[] {
    return [...value.matchAll(/-?\d+(?:\.\d+)?/g)].map((match) => Number(match[0]));
}

function assert_numbers(value: string, expected: number[], label: string): void {
    const actual = numbers(value);
    assert.equal(actual.length, expected.length, `${label}: got "${value}"`);
    for (const [i, number] of actual.entries()) {
        assert_near(number, expected[i]!, 0.05, `${label} [${i}] in "${value}"`);
    }
}

async function show_message_actions(page: Page): Promise<void> {
    const msg = (await page.$$(".message-list .message_row")).at(-1);
    assert.ok(msg !== undefined);
    const id = await (await msg.getProperty("id")).jsonValue();
    await msg.hover();
    const info = await page.waitForSelector(
        `#${CSS.escape(id)} .message_control_button.actions_hover`,
        {visible: true},
    );
    assert.ok(info !== null);
    await info.click();
    await page.waitForSelector(".delete_message", {visible: true});
}

// Opens the "Delete message?" modal. It takes two samples of the
// center of the modal box: 40ms after the box shows, while the modal
// animates, and at 300ms or later, when the animation is complete.
async function open_delete_dialog(page: Page): Promise<{at_40ms: Sample; settled: Sample}> {
    await show_message_actions(page);
    const samples = await page.evaluate(async () => {
        const selector = '.micromodal[aria-hidden="false"] .modal__container';
        document.querySelector<HTMLElement>(".delete_message")!.click();
        while (document.querySelector(selector) === null) {
            await new Promise((resolve) => window.requestAnimationFrame(resolve));
        }
        const container = document.querySelector(selector)!;
        function sample(): Sample {
            const box = container.getBoundingClientRect();
            return {
                x: box.left + box.width / 2,
                y: box.top + box.height / 2,
                done: document.querySelector(".modal--open") !== null,
            };
        }
        await new Promise((resolve) => setTimeout(resolve, 40));
        const at_40ms = sample();
        await new Promise((resolve) => setTimeout(resolve, 260));
        // A busy test machine can start the animation late. Then wait
        // for the end of the animation before the second sample.
        while (document.querySelector(".modal--open") === null) {
            await new Promise((resolve) => window.requestAnimationFrame(resolve));
        }
        const settled = sample();
        return {at_40ms, settled};
    });
    await common.wait_for_micromodal_to_open(page);
    return samples;
}

function check_modal_does_not_jump(samples: {at_40ms: Sample; settled: Sample}): void {
    // At 40ms the modal must still animate. Else the sample tells
    // nothing about the animation.
    assert.ok(!samples.at_40ms.done, "the modal animation ended before 40ms");
    assert_near(samples.at_40ms.x, samples.settled.x, 0.5, "modal center x at 40ms");
    // The animation moves the modal up by 8px.
    assert_near(samples.at_40ms.y, samples.settled.y, 8, "modal center y at 40ms");
}

async function close_modal(page: Page, key: "Escape" | null): Promise<void> {
    if (key === null) {
        await page.click('.micromodal[aria-hidden="false"] .dialog_exit_button');
    } else {
        // A drawer uses the same Micromodal code for Esc.
        await page.keyboard.press(key);
    }
    await common.wait_for_micromodal_to_close(page);
}

async function check_modal_style(page: Page, prefers_dark: boolean): Promise<void> {
    const style = await page.evaluate(() => {
        const modal = document.querySelector('.micromodal[aria-hidden="false"]')!;
        const overlay = modal.querySelector(".modal__overlay")!;
        const container = modal.querySelector(".modal__container")!;
        const title = modal.querySelector(".modal__title")!;
        const close = modal.querySelector(".modal__close")!;
        const cancel = modal.querySelector(".dialog_exit_button")!;
        const cta = modal.querySelector(".dialog_submit_button")!;
        const container_style = window.getComputedStyle(container);
        const title_style = window.getComputedStyle(title);
        const close_style = window.getComputedStyle(close);
        const box = container.getBoundingClientRect();
        const title_box = title.getBoundingClientRect();
        const close_box = close.getBoundingClientRect();
        const cancel_box = cancel.getBoundingClientRect();
        const cta_box = cta.getBoundingClientRect();
        return {
            overlay_background: window.getComputedStyle(overlay).backgroundColor,
            border_radius: container_style.borderRadius,
            border_width: container_style.borderTopWidth,
            box_shadow: container_style.boxShadow,
            animation_name: container_style.animationName,
            max_width: Number.parseFloat(container_style.maxWidth),
            width: box.width,
            window_width: window.innerWidth,
            title_font_size: title_style.fontSize,
            title_font_weight: title_style.fontWeight,
            title_left: title_box.left - box.left,
            title_top: title_box.top - box.top,
            close_width: close_box.width,
            close_height: close_box.height,
            close_radius: close_style.borderRadius,
            close_border_width: close_style.borderTopWidth,
            close_top: close_box.top - box.top,
            close_right: box.right - close_box.right,
            cancel_height: cancel_box.height,
            cta_height: cta_box.height,
            footer_gap: cta_box.left - cancel_box.right,
            cancel_radius: window.getComputedStyle(cancel).borderRadius,
            cancel_border_width: window.getComputedStyle(cancel).borderTopWidth,
            cta_radius: window.getComputedStyle(cta).borderRadius,
        };
    });

    // The scrim is the ink color at 50% in the two themes.
    assert_numbers(style.overlay_background, [22, 22, 29, 0.5], "scrim color");
    assert.equal(style.border_radius, "24px");
    assert.equal(style.border_width, "2px");
    // The shadow color is ink in the light theme, and black in the
    // dark theme.
    const shadow_color = prefers_dark ? [0, 0, 0] : [22, 22, 29];
    assert_numbers(style.box_shadow, [...shadow_color, 8, 8, 0, 0], "modal shadow");
    assert.ok(style.animation_name.includes("sj-pop"), `animation is "${style.animation_name}"`);
    // min(560px, 92vw), plus the two 2px borders.
    assert_near(style.width, Math.min(560, style.window_width * 0.92) + 4, 0.5, "modal width");
    assert_near(style.max_width, style.window_width * 0.92 + 4, 0.5, "modal max width");
    assert.equal(style.title_font_size, "24px");
    assert.equal(style.title_font_weight, "900");
    // The title and the ✕ are 20px from the top and 22px from the
    // sides, inside the 2px border.
    assert_near(style.title_left, 24, 0.5, "title left");
    assert_near(style.title_top, 22, 0.5, "title top");
    assert_near(style.close_top, 22, 0.5, "✕ top");
    assert_near(style.close_right, 24, 0.5, "✕ right");
    assert_near(style.close_width, 32, 0.5, "✕ width");
    assert_near(style.close_height, 32, 0.5, "✕ height");
    assert.equal(style.close_radius, "50%");
    // The ✕ and the Cancel button have the same 1.5px border. Chrome
    // shows 1px on a screen with one device pixel for each CSS pixel.
    assert.notEqual(style.close_border_width, "0px");
    assert.equal(style.close_border_width, style.cancel_border_width);
    assert_near(style.cancel_height, 44, 1.5, "Cancel height");
    assert_near(style.cta_height, 44, 1.5, "submit button height");
    assert_near(style.footer_gap, 8, 0.5, "gap between the footer buttons");
    assert.equal(style.cancel_radius, "999px");
    assert.equal(style.cta_radius, "999px");
}

async function check_focus_ring(page: Page): Promise<void> {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => {
        const element = document.activeElement!;
        return {
            in_modal: element.closest(".modal__container") !== null,
            box_shadow: window.getComputedStyle(element).boxShadow,
        };
    });
    assert.ok(focused.in_modal, "Tab moved the focus out of the modal");
    // The 3px yellow ring, #ffd84d.
    assert_numbers(focused.box_shadow, [255, 216, 77, 0, 0, 0, 3], "focus ring");
}

async function check_toast(page: Page): Promise<void> {
    const toast = await page.evaluate(async () => {
        // The same elements as show_toast in feedback_widget.ts.
        const root = document.createElement("div");
        root.className = "sj-toast sj-toast--global sj-toast--success";
        const icon = document.createElement("i");
        icon.className = "sj-toast__icon";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "✓";
        const text = document.createElement("span");
        text.className = "sj-toast__text";
        text.textContent = "Workspace settings saved.";
        const action = document.createElement("button");
        action.type = "button";
        action.className = "sj-toast__action";
        action.textContent = "Undo";
        root.append(icon, text, action);
        document.body.append(root);

        function sample(): Sample {
            const box = root.getBoundingClientRect();
            return {
                x: box.left + box.width / 2,
                y: box.top + box.height / 2,
                done: root.getAnimations().length === 0,
            };
        }
        await new Promise((resolve) => setTimeout(resolve, 40));
        const at_40ms = sample();
        await new Promise((resolve) => setTimeout(resolve, 260));
        // A busy test machine can start the animation late. Then wait
        // for the end of the animation before the second sample.
        await Promise.all(root.getAnimations().map(async (animation) => animation.finished));
        const settled = sample();

        const root_style = window.getComputedStyle(root);
        const icon_style = window.getComputedStyle(icon);
        const action_style = window.getComputedStyle(action);
        const icon_box = icon.getBoundingClientRect();
        return {
            at_40ms,
            settled,
            page_center: document.documentElement.clientWidth / 2,
            position: root_style.position,
            background: root_style.backgroundColor,
            border_radius: root_style.borderRadius,
            gap: root_style.columnGap,
            padding: root_style.padding,
            font_size: root_style.fontSize,
            icon_width: icon_box.width,
            icon_height: icon_box.height,
            icon_radius: icon_style.borderRadius,
            icon_font_size: icon_style.fontSize,
            action_font_size: action_style.fontSize,
            action_padding: action_style.padding,
        };
    });
    await common.screenshot(page, "app-primitives-toast");
    await page.evaluate(() => {
        document.querySelector(".sj-toast")!.remove();
    });

    assert.ok(!toast.at_40ms.done, "the toast animation ended before 40ms");
    assert.ok(toast.settled.done);
    assert_near(toast.at_40ms.x, toast.settled.x, 0.5, "toast center x at 40ms");
    assert_near(toast.settled.x, toast.page_center, 0.5, "toast center x");
    // The animation moves the toast up by 8px.
    assert_near(toast.at_40ms.y, toast.settled.y, 8, "toast center y at 40ms");
    assert.equal(toast.position, "fixed");
    assert_numbers(toast.background, [22, 22, 29], "toast color");
    assert.equal(toast.border_radius, "14px");
    assert_numbers(toast.font_size, [14], "toast font size");
    assert_numbers(toast.gap, [12], "toast gap");
    assert_numbers(toast.padding, [12, 14, 12, 16], "toast padding");
    assert_near(toast.icon_width, 18, 0.05, "icon width");
    assert_near(toast.icon_height, 18, 0.05, "icon height");
    assert.equal(toast.icon_radius, "6px");
    assert_numbers(toast.icon_font_size, [11], "icon font size");
    assert_numbers(toast.action_font_size, [13], "Undo font size");
    assert_numbers(toast.action_padding, [0, 4], "Undo padding");
}

async function app_primitives_test(page: Page): Promise<void> {
    await common.log_in(page);
    await page.evaluate(() => {
        window.location.hash = "#feed";
    });
    const message_list_id = await common.get_current_msg_list_id(page, true);
    await page.waitForSelector(
        `.message-list[data-message-list-id='${message_list_id}'] .message_row`,
        {visible: true},
    );

    for (const [name, prefers_dark] of [
        ["light", false],
        ["dark", true],
    ] as const) {
        await page.emulateMediaFeatures([
            {name: "prefers-color-scheme", value: prefers_dark ? "dark" : "light"},
        ]);
        const samples = await open_delete_dialog(page);
        check_modal_does_not_jump(samples);
        await check_modal_style(page, prefers_dark);
        await common.screenshot(page, `app-primitives-modal-${name}`);
        await check_focus_ring(page);
        await close_modal(page, prefers_dark ? "Escape" : null);
    }

    await page.emulateMediaFeatures([{name: "prefers-color-scheme", value: "light"}]);
    await check_toast(page);
}

await common.run_test(app_primitives_test);
