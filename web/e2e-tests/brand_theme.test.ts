import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

// Sanji brand tokens: `body` gets its font, text color, and background
// from `--sj-*` tokens, which resolve through `light-dark()`. This
// checks both themes end to end, not just that the CSS parses: real
// font loading, the automatic (OS-following) theme path that most
// users are on, and the left sidebar, whose fixed-dark background
// needs its own light-dark() branch regardless of the page theme.
const themes = [
    {
        name: "light",
        prefers_dark: false,
        background: "rgb(255, 250, 240)", // --sj-kertas
        text: "rgb(22, 22, 29)", // --sj-tinta
    },
    {
        name: "dark",
        prefers_dark: true,
        background: "rgb(31, 31, 39)", // --sj-gelap-1
        text: "rgb(255, 250, 240)", // --sj-kertas
    },
] as const;

// The left sidebar is a fixed dark surface in both page themes (P-16),
// so its own text stays light in both themes too.
const sidebar_background = "rgb(22, 22, 29)"; // --sj-tinta
const sidebar_text_min_luminance = 400; // out of a 765 (255*3) max; a light gray or lighter

function channel_sum(rgb: string): number {
    const match = /rgb\((\d+), (\d+), (\d+)\)/.exec(rgb);
    assert.ok(match, `expected an "rgb(r, g, b)" color, got ${rgb}`);
    const [, r, g, b] = match;
    return Number(r) + Number(g) + Number(b);
}

async function set_prefers_dark(page: Page, prefers_dark: boolean): Promise<void> {
    // The real OS-preference media feature, not a manual .dark-theme
    // class: most users are on the "automatic" theme setting, which
    // only the automatic-theme media query below exercises.
    await page.emulateMediaFeatures([
        {name: "prefers-color-scheme", value: prefers_dark ? "dark" : "light"},
    ]);
}

async function check_fonts_loaded(page: Page): Promise<void> {
    await page.evaluate(async () => {
        await document.fonts.ready;
    });
    const schibsted_loaded = await page.evaluate(() =>
        document.fonts.check('1em "Schibsted Grotesk Variable"'),
    );
    assert.ok(schibsted_loaded, "Schibsted Grotesk Variable did not finish loading");
}

async function check_body_theme(page: Page, theme: (typeof themes)[number]): Promise<void> {
    const style = await page.evaluate(() => {
        const computed = window.getComputedStyle(document.body);
        return {
            fontFamily: computed.fontFamily,
            backgroundColor: computed.backgroundColor,
            color: computed.color,
        };
    });

    assert.ok(
        style.fontFamily.includes("Schibsted Grotesk"),
        `expected Schibsted Grotesk in body font-family, got ${style.fontFamily}`,
    );
    assert.equal(style.backgroundColor, theme.background);
    assert.equal(style.color, theme.text);
}

async function check_sidebar_theme(page: Page): Promise<void> {
    await page.waitForSelector(".top_left_task_board .left-sidebar-navigation-label", {
        visible: true,
    });
    const style = await page.evaluate(() => {
        const sidebar = document.querySelector<HTMLElement>("#left-sidebar")!;
        const label = document.querySelector<HTMLElement>(
            ".top_left_task_board .left-sidebar-navigation-label",
        )!;
        return {
            sidebarBackground: window.getComputedStyle(sidebar).backgroundColor,
            labelColor: window.getComputedStyle(label).color,
        };
    });

    assert.equal(style.sidebarBackground, sidebar_background);
    assert.ok(
        channel_sum(style.labelColor) >= sidebar_text_min_luminance,
        `expected a light sidebar label color on the dark sidebar, got ${style.labelColor}`,
    );
}

async function brand_theme_test(page: Page): Promise<void> {
    await check_fonts_loaded(page);

    await common.log_in(page);
    await page.waitForSelector("body", {visible: true});

    for (const theme of themes) {
        await set_prefers_dark(page, theme.prefers_dark);
        await check_body_theme(page, theme);
        await check_sidebar_theme(page);
        await common.screenshot(page, `brand-theme-${theme.name}`);
    }

    // log_out lands back on /login/, still under whichever
    // prefers-color-scheme the loop above left active (dark).
    await common.log_out(page);
    await page.waitForSelector(".login-split", {visible: true});
    await common.screenshot(page, "brand-theme-login-dark");

    await set_prefers_dark(page, false);
    await page.reload();
    await page.waitForSelector(".login-split", {visible: true});
    await common.screenshot(page, "brand-theme-login-light");

    // Leave a session behind: run_test's own teardown logs out again,
    // which needs one to find.
    await common.log_in(page);
}

await common.run_test(brand_theme_test);
