"use strict";

const assert = require("node:assert/strict");

const {run_test} = require("./lib/test.cjs");

// The dynamic favicon draws the sanji mark inside the same rounded box
// as the static favicon, and swaps the whole box to the Kaki accent
// (instead of adding an extra dot) when the count is a direct message.
// See BRANDING.md.
const INK = "#16161D";
const PAPER = "#FFFAF0";
const KAKI = "#FF6A3D";

function render(props) {
    return require("../templates/favicon.svg.hbs")({
        favicon_font_url_html: "fake-font-url",
        count: "",
        count_long: false,
        have_pm: false,
        ...props,
    });
}

run_test("unread count uses the ink box and paper mark", () => {
    const svg = render({count: "7"});
    assert.match(svg, /viewBox="0 0 32 32"/);
    assert.match(svg, new RegExp(`rect width="32" height="32" rx="8" fill="${INK}"`));
    assert.match(svg, new RegExp(`scale\\(\\.45\\)" fill="${PAPER}"`));
    assert.match(svg, />\s*7\s*</);
    assert.ok(!svg.includes(KAKI), "no DM accent when there is no direct message");
});

run_test("a direct message swaps the box and mark, without adding a dot", () => {
    const svg = render({count: "3", have_pm: true});
    assert.match(svg, new RegExp(`rect width="32" height="32" rx="8" fill="${KAKI}"`));
    assert.match(svg, new RegExp(`scale\\(\\.45\\)" fill="${INK}"`));
    assert.ok(!svg.includes("#f00"), "brand forbids adding a dot; recolor the box instead");
});

run_test("a long count uses the compressed digit size", () => {
    const short_svg = render({count: "7", count_long: false});
    assert.match(short_svg, /font-size="11"/);
    assert.ok(!short_svg.includes("textLength"));

    const long_svg = render({count: "12K", count_long: true});
    assert.match(long_svg, /font-size="9" textLength="14" lengthAdjust="spacingAndGlyphs"/);
    assert.match(long_svg, />\s*12K\s*</);
});

run_test("the count digits are drawn at double size, with a paper fill and an ink halo", () => {
    const svg = render({count: "7"});
    assert.match(svg, /<g transform="scale\(2\)">/);
    assert.match(svg, new RegExp(`stroke="${INK}"[^>]*>\\s*7\\s*<`));
    assert.match(svg, new RegExp(`fill="${PAPER}"[^>]*>\\s*7\\s*<`));
});
