"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let next_response;
let last_call;
mock_esm("../src/channel", {
    get(options) {
        last_call = {method: "GET", ...options};
        return Promise.resolve(next_response);
    },
    put(options) {
        last_call = {method: "PUT", ...options};
        return Promise.resolve(next_response);
    },
});

const mcp_data = zrequire("mcp_data");

function make_tool(overrides) {
    return {
        name: "get_me",
        description: "Read the profile of the account that approved this connection.",
        kind: "read",
        policy: "allow",
        default_policy: "allow",
        ...overrides,
    };
}

function make_tools_data(tools, overrides) {
    return {
        grant: {id: "g-1", name: "Claude Cowork", policy_mode: "managed"},
        enforced: true,
        tools,
        ...overrides,
    };
}

run_test("the tools of a connection come from its own address", async () => {
    next_response = make_tools_data([make_tool({})]);
    const data = await mcp_data.fetch_tools("g-1");
    assert.equal(last_call.method, "GET");
    assert.equal(last_call.url, "/json/mcp/access/g-1/tools");
    assert.equal(data.tools[0].name, "get_me");
    assert.ok(data.enforced);
});

run_test("a policy the server does not know is refused", async () => {
    next_response = make_tools_data([make_tool({policy: "maybe"})]);
    await assert.rejects(async () => mcp_data.fetch_tools("g-1"));
});

run_test("saving sends the changes as JSON and returns the tools as they are now", async () => {
    next_response = make_tools_data([
        make_tool({name: "send_message", kind: "write", policy: "block"}),
    ]);
    const data = await mcp_data.save_policies("g-1", {send_message: "block"});
    assert.equal(last_call.method, "PUT");
    assert.equal(last_call.url, "/json/mcp/access/g-1/tools");
    assert.deepEqual(JSON.parse(last_call.data.policies), {send_message: "block"});
    assert.equal(data.tools[0].policy, "block");
});

run_test("the activity pages back from the oldest entry shown", async () => {
    next_response = {entries: [], has_more: false};
    await mcp_data.fetch_audit(undefined);
    assert.equal(last_call.url, "/json/mcp/audit");
    assert.deepEqual(last_call.data, {});
    await mcp_data.fetch_audit(120);
    assert.deepEqual(last_call.data, {before: 120});
});

run_test("an entry that has no digest or actor still parses", async () => {
    next_response = {
        entries: [
            {
                id: 1,
                created_at: "2026-10-18T09:00:00+00:00",
                connection: "Claude Cowork",
                owner: "Ari",
                tool: "get_me",
                outcome: "success",
                digest: "",
                actor: null,
                policy: null,
            },
        ],
        has_more: true,
    };
    const page = await mcp_data.fetch_audit(undefined);
    assert.equal(page.entries[0].actor, null);
    assert.ok(page.has_more);
});

run_test("a read tool is Auto or Off and any other tool can also ask first", () => {
    assert.deepEqual(mcp_data.policy_options(make_tool({kind: "read"})), ["allow", "block"]);
    assert.deepEqual(mcp_data.policy_options(make_tool({kind: "write"})), [
        "allow",
        "approve",
        "block",
    ]);
    assert.deepEqual(mcp_data.policy_options(make_tool({kind: "destructive"})), [
        "allow",
        "approve",
        "block",
    ]);
});

run_test("setting all read tools to Auto changes only the read tools that are not", () => {
    const tools = [
        make_tool({name: "get_me", policy: "block"}),
        make_tool({name: "search", policy: "allow"}),
        make_tool({
            name: "send_message",
            kind: "write",
            policy: "block",
            default_policy: "approve",
        }),
    ];
    assert.deepEqual(mcp_data.read_tools_to_auto(tools), {get_me: "allow"});
    assert.deepEqual(mcp_data.read_tools_to_auto([make_tool({})]), {});
});

run_test("the recommended settings change only the tools that differ from their default", () => {
    const tools = [
        make_tool({name: "get_me", policy: "block"}),
        make_tool({name: "search", policy: "allow"}),
        make_tool({
            name: "send_message",
            kind: "write",
            policy: "allow",
            default_policy: "approve",
        }),
        make_tool({
            name: "delete_task",
            kind: "destructive",
            policy: "approve",
            default_policy: "approve",
        }),
    ];
    assert.deepEqual(mcp_data.recommended_changes(tools), {
        get_me: "allow",
        send_message: "approve",
    });
});

run_test("a connection made before policies is legacy", () => {
    assert.ok(
        mcp_data.is_legacy(
            make_tools_data([], {grant: {id: "g", name: "x", policy_mode: "legacy"}}),
        ),
    );
    assert.ok(!mcp_data.is_legacy(make_tools_data([])));
});

run_test("entries about the connection itself name no tool", () => {
    const entry = {tool: "consent"};
    assert.ok(mcp_data.is_connection_event(entry));
    assert.ok(mcp_data.is_connection_event({tool: "token.create"}));
    assert.ok(mcp_data.is_connection_event({tool: "token.revoke"}));
    assert.ok(!mcp_data.is_connection_event({tool: "send_message"}));
});
