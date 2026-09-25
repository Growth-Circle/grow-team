import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

// Sanji brand tokens (map-brand.md §11, §11.1): `body` gets its font,
// text color, and background from `--sj-*` tokens, which resolve
// through `light-dark()`. This checks both themes end to end, not
// just that the CSS parses.
const themes = [
    {
        name: "light",
        dark: false,
        background: "rgb(255, 250, 240)", // --sj-kertas
        text: "rgb(22, 22, 29)", // --sj-tinta
    },
    {
        name: "dark",
        dark: true,
        background: "rgb(31, 31, 39)", // --sj-gelap-1
        text: "rgb(255, 250, 240)", // --sj-kertas
    },
] as const;

async function check_theme(page: Page, theme: (typeof themes)[number]): Promise<void> {
    await page.evaluate((dark) => {
        document.documentElement.classList.toggle("dark-theme", dark);
    }, theme.dark);

    const style = await page.evaluate(() => {
        const computed = getComputedStyle(document.body);
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

    await common.screenshot(page, `brand-theme-${theme.name}`);
}

async function brand_theme_test(page: Page): Promise<void> {
    await common.log_in(page);
    await page.waitForSelector("body", {visible: true});

    for (const theme of themes) {
        await check_theme(page, theme);
    }
}

await common.run_test(brand_theme_test);
