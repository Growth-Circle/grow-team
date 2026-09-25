"use strict";

const assert = require("node:assert/strict");

const {make_realm} = require("./lib/example_realm.cjs");
const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

// See BRANDING.md: the navbar logo must switch to the night file in a
// dark theme, except when no night logo was uploaded and the day logo
// is also the default (nothing theme-specific to switch to).
const settings_data = mock_esm("../src/settings_data");

const {set_realm} = zrequire("state_data");
const realm_logo = zrequire("realm_logo");

function render_navbar_logo(overrides) {
    set_realm(
        make_realm({
            realm_logo_url: "/day.svg",
            realm_night_logo_url: "/night.svg",
            ...overrides,
        }),
    );
    realm_logo.render();
    return $("#realm-navbar-wide-logo").attr("src");
}

run_test("dark theme, both sources default: night file", ({override}) => {
    override(settings_data, "using_dark_theme", () => true);
    const src = render_navbar_logo({realm_logo_source: "D", realm_night_logo_source: "D"});
    assert.equal(src, "/night.svg");
});

run_test("dark theme, day uploaded and night default: day file", ({override}) => {
    override(settings_data, "using_dark_theme", () => true);
    const src = render_navbar_logo({realm_logo_source: "U", realm_night_logo_source: "D"});
    assert.equal(src, "/day.svg");
});

run_test("dark theme, night uploaded: night file", ({override}) => {
    override(settings_data, "using_dark_theme", () => true);
    const src = render_navbar_logo({realm_logo_source: "D", realm_night_logo_source: "U"});
    assert.equal(src, "/night.svg");
});

run_test("light theme: day file", ({override}) => {
    override(settings_data, "using_dark_theme", () => false);
    const src = render_navbar_logo({realm_logo_source: "D", realm_night_logo_source: "D"});
    assert.equal(src, "/day.svg");
});
