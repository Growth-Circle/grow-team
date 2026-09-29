"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const sidebar_targets = zrequire("sidebar_targets");

// The pages that ship. When a page ships, its entry moves from the
// second list to the first, in sidebar_targets.ts and here.
const BUILT = ["tasks"];
const HIDDEN_OR_FALLBACK = [
    "search",
    "home",
    "needs",
    "agents",
    "mcp",
    "runners",
    "drive",
    "settings",
];

run_test("built_pages", () => {
    for (const id of BUILT) {
        assert.equal(sidebar_targets.is_built(id), true, id);
        assert.equal(sidebar_targets.is_shown(id), true, id);
    }
    for (const id of HIDDEN_OR_FALLBACK) {
        assert.equal(sidebar_targets.is_built(id), false, id);
    }
});

run_test("get_hash", () => {
    assert.equal(sidebar_targets.get_hash("tasks"), "#tasks");
    // A built page opens the section that the caller names.
    assert.equal(sidebar_targets.get_hash("tasks", "mine"), "#tasks/mine");
    // A page that is not built opens its fallback, or is hidden.
    assert.equal(sidebar_targets.get_hash("home"), "#inbox");
    assert.equal(sidebar_targets.get_hash("settings", "members"), "#organization");
    assert.equal(sidebar_targets.get_hash("needs"), undefined);
    assert.equal(sidebar_targets.get_hash("drive"), undefined);
});

run_test("is_shown", () => {
    assert.equal(sidebar_targets.is_shown("home"), true);
    assert.equal(sidebar_targets.is_shown("settings"), true);
    for (const id of ["search", "needs", "agents", "mcp", "runners", "drive"]) {
        assert.equal(sidebar_targets.is_shown(id), false, id);
    }
});

run_test("id_for_hash", () => {
    assert.equal(sidebar_targets.id_for_hash("#tasks"), "tasks");
    assert.equal(sidebar_targets.id_for_hash("#tasks/mine"), "tasks");
    assert.equal(sidebar_targets.id_for_hash("#tasksboard"), undefined);
    // The fallback of a page marks that page.
    assert.equal(sidebar_targets.id_for_hash("#inbox"), "home");
    assert.equal(sidebar_targets.id_for_hash("#organization/organization-profile"), "settings");
    // A page that is not built and has no fallback marks nothing.
    assert.equal(sidebar_targets.id_for_hash("#needs"), undefined);
    assert.equal(sidebar_targets.id_for_hash("#narrow/channel/1-general"), undefined);
    assert.equal(sidebar_targets.id_for_hash(""), undefined);
});

run_test("role_of", () => {
    const base = {is_owner: false, is_admin: false, is_guest: false};
    assert.equal(sidebar_targets.role_of({...base, is_owner: true, is_admin: true}), "owner");
    assert.equal(sidebar_targets.role_of({...base, is_admin: true}), "admin");
    assert.equal(sidebar_targets.role_of({...base, is_moderator: true}), "moderator");
    assert.equal(sidebar_targets.role_of({...base, is_guest: true}), "guest");
    assert.equal(sidebar_targets.role_of(base), "member");

    assert.equal(sidebar_targets.can_manage_workspace("owner"), true);
    assert.equal(sidebar_targets.can_manage_workspace("admin"), true);
    for (const role of ["moderator", "member", "guest"]) {
        assert.equal(sidebar_targets.can_manage_workspace(role), false, role);
    }
});

run_test("search_shortcut_hint", () => {
    assert.equal(sidebar_targets.search_shortcut_hint(true), "⌘K");
    assert.equal(sidebar_targets.search_shortcut_hint(false), "Ctrl K");
});
