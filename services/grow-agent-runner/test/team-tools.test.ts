import {test} from "node:test";
import assert from "node:assert/strict";
import {teamToolCatalog, validateTeamToolCall, teamToolExecutor} from "../dist/team-tools.js";
import {TransportError} from "../dist/transport.js";
import {MANAGE_INSTRUCTION} from "../dist/runtime-supervisor.js";
const CATALOG_NAMES = [
    "grow_team_find",
    "grow_channel_create",
    "grow_channel_subscribe",
    "grow_channel_unsubscribe",
    "grow_group_create",
    "grow_group_add_members",
    "grow_group_remove_members",
    "grow_topic_post",
    "grow_topic_add_person",
    "grow_topic_resolve",
    "grow_topic_move",
];
const call = (name: string) => ({id: "call1", name, arguments: ""}) as any;
const lease = () => ({schema_version: 1, job_id: "job1", attempt_id: "a1", lease_epoch: 1, job_version: 1});
test("the manage-mode instruction covers all four required rules", () => {
    assert.match(MANAGE_INSTRUCTION, /commander/);
    assert.match(MANAGE_INSTRUCTION, /grow_team_find/);
    assert.match(MANAGE_INSTRUCTION, /succeeded/);
    assert.match(MANAGE_INSTRUCTION, /data.*never.*instructions/is);
});
test("the team catalog is exactly the 11 tools of contract 2.3, closed to extra fields", () => {
    const catalog = teamToolCatalog();
    assert.deepEqual(
        catalog.map((t) => t.name).sort(),
        [...CATALOG_NAMES].sort(),
    );
    for (const tool of catalog) {
        assert.equal(tool.parameters.additionalProperties, false);
        assert(!("tool" in tool.parameters.properties), `${tool.name} must not ask the model for "tool"`);
        assert(!tool.parameters.required.includes("tool"));
    }
});
test("team.find and channel.create carry their contract field limits", () => {
    const byName = Object.fromEntries(teamToolCatalog().map((t) => [t.name, t]));
    assert.deepEqual(byName.grow_team_find!.parameters.required, ["query", "kinds"]);
    assert.equal(byName.grow_team_find!.parameters.properties.query.maxLength, 100);
    assert.equal(byName.grow_team_find!.parameters.properties.kinds.maxItems, 3);
    assert.deepEqual(byName.grow_channel_create!.parameters.required, [
        "name",
        "description",
        "is_private",
        "subscriber_user_ids",
    ]);
    assert.equal(byName.grow_channel_create!.parameters.properties.subscriber_user_ids.maxItems, 50);
});
test("validateTeamToolCall accepts a well-formed call for every tool in the catalog", () => {
    const inputs: Record<string, unknown> = {
        grow_team_find: {query: "budi", kinds: ["person"]},
        grow_channel_create: {
            name: "launch",
            description: "",
            is_private: false,
            subscriber_user_ids: [],
        },
        grow_channel_subscribe: {channel_id: 1, user_ids: [2]},
        grow_channel_unsubscribe: {channel_id: 1, user_ids: [2]},
        grow_group_create: {name: "team", description: "", member_user_ids: []},
        grow_group_add_members: {group_id: 1, user_ids: [2]},
        grow_group_remove_members: {group_id: 1, user_ids: [2]},
        grow_topic_post: {channel_id: 1, topic: "plan", content: "hi"},
        grow_topic_add_person: {channel_id: 1, topic: "plan", user_ids: [2]},
        grow_topic_resolve: {channel_id: 1, topic: "plan", resolved: true},
        grow_topic_move: {channel_id: 1, topic: "plan", new_topic: "plan2", new_channel_id: null},
    };
    for (const [name, raw] of Object.entries(inputs)) {
        const args = validateTeamToolCall(call(name), raw as any);
        assert.equal(args.action, "team.manage");
        assert.equal(args.input.tool, name.slice(5).replace("_", "."));
    }
});
test("validateTeamToolCall rejects a field the tool's own schema does not declare", () => {
    assert.throws(() =>
        validateTeamToolCall(call("grow_channel_subscribe"), {
            channel_id: 1,
            user_ids: [2],
            extra: true,
        }),
    );
});
test("validateTeamToolCall rejects a call name outside the catalog", () => {
    assert.throws(() => validateTeamToolCall(call("grow_channel_archive"), {}), /catalog/);
});
test("validateTeamToolCall derives the tool id from the call name, never from the model's own arguments", () => {
    const args = validateTeamToolCall(call("grow_team_find"), {
        query: "budi",
        kinds: ["person"],
        tool: "channel.create",
    });
    assert.equal(args.input.tool, "team.find");
});
test("an authorized proposal calls execute once with no approval wait", async () => {
    const calls: string[] = [];
    const channel: any = {
        operations: {
            propose: async (_lease: any, id: string) => {
                calls.push("propose");
                return {status: "authorized", operation_id: id, operation_hash: "h1", version: 1};
            },
            execute: async () => {
                calls.push("execute");
                return {
                    server_receipt: {
                        tool: "team.find",
                        outcome: "succeeded",
                        summary: "Found 1 person.",
                        objects: {},
                        error: null,
                    },
                };
            },
            reconcile: async () => {
                throw new Error("must not reconcile an authorized operation");
            },
        },
        request: async () => {
            throw new Error("must not poll controls for an authorized operation");
        },
    };
    const executor = teamToolExecutor(channel, lease, new AbortController().signal, "a1", 1);
    const output = await executor("op1", {
        action: "team.manage",
        input: {tool: "team.find", query: "budi", kinds: ["person"]},
    });
    assert.deepEqual(calls, ["propose", "execute"]);
    assert.deepEqual(JSON.parse(output), {
        tool: "team.find",
        outcome: "succeeded",
        summary: "Found 1 person.",
        objects: {},
        error: null,
    });
});
test("a proposed operation waits for the commander's decision, then executes once", async () => {
    const calls: string[] = [];
    let reconciled = 0;
    const proposal = {
        status: "proposed",
        operation_id: "op1",
        operation_hash: "h1",
        version: 1,
        approval_id: "ap1",
        nonce: "n1",
    };
    const channel: any = {
        operations: {
            propose: async () => {
                calls.push("propose");
                return proposal;
            },
            reconcile: async () => {
                reconciled++;
                return {operations: [proposal]};
            },
            execute: async () => {
                calls.push("execute");
                return {
                    server_receipt: {
                        tool: "channel.create",
                        outcome: "succeeded",
                        summary: "Created #launch.",
                        objects: {channel_id: 9},
                        error: null,
                    },
                };
            },
        },
        request: async (route: string) => {
            assert.equal(route, "/runner/controls");
            return {
                controls: [
                    {
                        attempt_id: "a1",
                        lease_epoch: 1,
                        approvals: [
                            {
                                operation_id: "op1",
                                id: "ap1",
                                nonce: "n1",
                                decision: reconciled >= 1 ? "approved" : "pending",
                            },
                        ],
                    },
                ],
            };
        },
    };
    const executor = teamToolExecutor(channel, lease, new AbortController().signal, "a1", 1);
    const output = await executor("op1", {
        action: "team.manage",
        input: {
            tool: "channel.create",
            name: "launch",
            description: "",
            is_private: false,
            subscriber_user_ids: [],
        },
    });
    assert.deepEqual(calls, ["propose", "execute"]);
    assert.equal(reconciled, 1);
    assert.equal(JSON.parse(output).objects.channel_id, 9);
});
test("a rejected approval becomes an error tool output instead of a thrown failure", async () => {
    const calls: string[] = [];
    const proposal = {
        status: "proposed",
        operation_id: "op1",
        operation_hash: "h1",
        version: 1,
        approval_id: "ap1",
        nonce: "n1",
    };
    const channel: any = {
        operations: {
            propose: async () => {
                calls.push("propose");
                return proposal;
            },
            reconcile: async () => ({operations: [proposal]}),
            execute: async () => {
                calls.push("execute");
                throw new Error("must not execute a rejected step");
            },
        },
        request: async () => ({
            controls: [
                {
                    attempt_id: "a1",
                    lease_epoch: 1,
                    approvals: [{operation_id: "op1", id: "ap1", nonce: "n1", decision: "rejected"}],
                },
            ],
        }),
    };
    const executor = teamToolExecutor(channel, lease, new AbortController().signal, "a1", 1);
    const output = await executor("op1", {
        action: "team.manage",
        input: {tool: "channel.unsubscribe", channel_id: 1, user_ids: [2]},
    });
    const receipt = JSON.parse(output);
    assert.equal(receipt.tool, "channel.unsubscribe");
    assert.equal(receipt.outcome, "failed");
    assert.match(receipt.error, /rejected/);
    assert.deepEqual(calls, ["propose"]);
});
test("a lost execute response becomes the contract sentence and is not retried", async () => {
    let executeCalls = 0;
    const channel: any = {
        operations: {
            propose: async () => ({status: "authorized", operation_id: "op1", operation_hash: "h1", version: 1}),
            execute: async () => {
                executeCalls++;
                throw new TransportError("policy", "outcome_unknown");
            },
        },
    };
    const executor = teamToolExecutor(channel, lease, new AbortController().signal, "a1", 1);
    const output = await executor("op1", {
        action: "team.manage",
        input: {tool: "topic.resolve", channel_id: 1, topic: "plan", resolved: true},
    });
    const receipt = JSON.parse(output);
    assert.equal(executeCalls, 1);
    assert.equal(receipt.outcome, "unknown");
    assert.match(receipt.error, /may have finished/);
});
test("an execute failure other than outcome_unknown still propagates as a fatal error", async () => {
    const channel: any = {
        operations: {
            propose: async () => ({status: "authorized", operation_id: "op1", operation_hash: "h1", version: 1}),
            execute: async () => {
                throw new TransportError("credential", "credential_invalid");
            },
        },
    };
    const executor = teamToolExecutor(channel, lease, new AbortController().signal, "a1", 1);
    await assert.rejects(
        () =>
            executor("op1", {
                action: "team.manage",
                input: {tool: "topic.resolve", channel_id: 1, topic: "plan", resolved: true},
            }),
        (e: any) => e.kind === "credential",
    );
});
test("a successful execute without a server_receipt still returns a receipt-shaped output", async () => {
    const channel: any = {
        operations: {
            propose: async () => ({status: "authorized", operation_id: "op1", operation_hash: "h1", version: 1}),
            execute: async () => ({status: "succeeded"}),
        },
    };
    const executor = teamToolExecutor(channel, lease, new AbortController().signal, "a1", 1);
    const receipt = JSON.parse(
        await executor("op1", {
            action: "team.manage",
            input: {tool: "team.find", query: "x", kinds: ["person"]},
        }),
    );
    assert.equal(receipt.outcome, "succeeded");
    assert.equal(receipt.tool, "team.find");
});
