"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const blueslip = require("./lib/zblueslip.cjs");

const channel = mock_esm("../src/channel");

const live_updates = zrequire("live_updates");
const permissions = zrequire("permissions");

function make_response(my_role) {
    return {
        my_role,
        permissions: [
            {
                key: "ws_settings",
                group: "workspace",
                cells: {
                    owner: {allowed: true, locked: true, reason: "owner_always"},
                    admin: {allowed: true, locked: false, reason: null},
                    moderator: {allowed: false, locked: false, reason: null},
                    member: {allowed: false, locked: false, reason: null},
                    guest: {allowed: false, locked: false, reason: null},
                },
            },
        ],
    };
}

async function flush_promises() {
    // Enough microtask turns for fetch_and_apply() to finish after its
    // request resolves.
    for (let i = 0; i < 10; i += 1) {
        await Promise.resolve();
    }
}

// The tests below share the module state of permissions.ts, so their
// order matters: these first two run before initialize().

run_test("before_any_load_every_permission_is_denied", () => {
    assert.equal(permissions.my_role(), "guest");
    assert.equal(permissions.can("ws_settings"), false);
});

run_test("refetch_before_initialize_does_nothing", ({disallow}) => {
    disallow(channel, "get");

    permissions.refetch();

    assert.equal(permissions.my_role(), "guest");
});

run_test("failed_fetch_keeps_safe_defaults", async ({override}) => {
    override(channel, "get", async () => {
        throw new Error("network error");
    });
    blueslip.expect("warn", "permissions: could not load /json/realm/permissions.");

    await permissions.initialize();

    // A failed load never grants a permission that the server did
    // not confirm.
    assert.equal(permissions.my_role(), "guest");
    assert.equal(permissions.can("ws_settings"), false);
});

run_test("initialize_applies_role_and_can", async ({override}) => {
    override(channel, "get", async () => make_response("admin"));

    await permissions.initialize();

    assert.equal(permissions.my_role(), "admin");
    assert.equal(permissions.can("ws_settings"), true);
    assert.equal(permissions.can("no_such_key"), false);
});

run_test("refetch_after_initialize_loads_again", async ({override}) => {
    override(channel, "get", async () => make_response("member"));

    permissions.refetch();
    await flush_promises();

    assert.equal(permissions.my_role(), "member");
    assert.equal(permissions.can("ws_settings"), false);
});

run_test("realm_permissions_event_loads_again", async ({override}) => {
    let call_count = 0;
    override(channel, "get", async () => {
        call_count += 1;
        return make_response("owner");
    });

    live_updates.dispatch({type: "realm_permissions"});
    await flush_promises();

    // A second initialize() does not subscribe to the event again.
    await permissions.initialize();
    live_updates.dispatch({type: "realm_permissions"});
    await flush_promises();

    assert.equal(call_count, 3);
    assert.equal(permissions.my_role(), "owner");
});

run_test("newest_answer_wins", async ({override}) => {
    const resolvers = [];
    override(
        channel,
        "get",
        () =>
            new Promise((resolve) => {
                resolvers.push(resolve);
            }),
    );

    permissions.refetch();
    permissions.refetch();
    assert.equal(resolvers.length, 2);

    // The answer to the newer request comes back first.
    resolvers[1](make_response("moderator"));
    await flush_promises();
    resolvers[0](make_response("owner"));
    await flush_promises();

    assert.equal(permissions.my_role(), "moderator");
});
