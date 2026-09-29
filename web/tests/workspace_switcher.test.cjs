"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

mock_esm("../src/state_data", {
    current_user: {is_owner: false, is_admin: true, is_guest: false},
    realm: {realm_name: "Kopi Senja", realm_url: "https://kopisenja.sanji.space"},
});

const workspace_switcher = zrequire("workspace_switcher");

run_test("hostname_for", () => {
    assert.equal(workspace_switcher.hostname_for("https://team.growc.id"), "team.growc.id");
    assert.equal(
        workspace_switcher.hostname_for("https://kopisenja.sanji.space/path"),
        "kopisenja.sanji.space",
    );
    // Text that is not an address shows as it is.
    assert.equal(workspace_switcher.hostname_for("not a url"), "not a url");
});

run_test("role_label", () => {
    assert.equal(workspace_switcher.role_label("owner"), "translated: Owner");
    assert.equal(workspace_switcher.role_label("admin"), "translated: Admin");
    assert.equal(workspace_switcher.role_label("moderator"), "translated: Moderator");
    assert.equal(workspace_switcher.role_label("guest"), "translated: Guest");
    assert.equal(workspace_switcher.role_label("member"), "translated: Member");
    assert.equal(workspace_switcher.role_label("anything else"), "translated: Member");
});

run_test("to_row", () => {
    const workspace = {
        realm_id: 2,
        name: "Rumah Hijau",
        url: "https://rumahhijau.sanji.space",
        role: "owner",
        brand_color: "#16C784",
        initial: "R",
        current: false,
    };
    assert.deepEqual(workspace_switcher.to_row(workspace), {
        realm_id: 2,
        name: "Rumah Hijau",
        initial: "R",
        avatar_color: "#16C784",
        hostname: "rumahhijau.sanji.space",
        role_label: "translated: Owner",
        current: false,
    });
    // A workspace without a chosen color gets the yellow one.
    assert.equal(
        workspace_switcher.to_row({...workspace, brand_color: ""}).avatar_color,
        "#FFD84D",
    );
});

run_test("build_view_before_the_list_arrives", () => {
    // The button names the current workspace from what the page knows.
    const view = workspace_switcher.build_view();
    assert.equal(view.current.name, "Kopi Senja");
    assert.equal(view.current.initial, "K");
    assert.equal(view.current.hostname, "kopisenja.sanji.space");
    assert.equal(view.current.role_label, "translated: Admin");
    assert.deepEqual(view.rows, []);
    assert.equal(view.is_loading, true);
    assert.equal(view.load_failed, false);
    // An admin can open the settings, but the server has not allowed creating a workspace.
    assert.equal(view.can_manage_workspace, true);
    assert.equal(view.can_create_workspace, false);
});
