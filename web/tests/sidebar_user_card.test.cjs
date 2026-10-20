"use strict";

const assert = require("node:assert/strict");

const render_sidebar_user_card = require("../templates/popovers/personal_menu_popover.hbs");

const {run_test} = require("./lib/test.cjs");

function render(settings_hash) {
    return render_sidebar_user_card({
        full_name: "Ari",
        email: "ari@example.com",
        avatar_url: "/avatar.png",
        role_label: "Owner",
        workspace_name: "GCRD",
        settings_hash,
        shortcut_hint: "Ctrl+/",
    });
}

run_test("gear_opens_the_settings_for_the_role", () => {
    // AC-S3-001: the link points at the settings that the role gets.
    assert.match(render("#organization"), /<a [^>]*href="#organization"/);
    assert.match(render("#settings/profile"), /<a [^>]*href="#settings\/profile"/);
});

run_test("gear_is_a_named_link", () => {
    // AC-S3-002: a link is in the tab order, and its icon is not read out.
    const html = render("#settings/profile");
    const [gear] = html.match(/<a class="sidebar-user-card-settings"[^>]*>[\s\S]*?<\/a>/);
    assert.match(gear, /aria-label="translated: Settings"/);
    assert.match(gear, /<i [^>]*aria-hidden="true"/);
    assert.doesNotMatch(gear, /tabindex/);
});

run_test("gear_is_beside_the_card_button", () => {
    // A button cannot hold another control, so the gear is its sibling.
    const html = render("#settings/profile");
    const button = html.match(/<button[^>]*id="sidebar-user-card-button"[\s\S]*?<\/button>/)[0];
    assert.doesNotMatch(button, /sidebar-user-card-settings/);
});
