import {test} from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {AnthropicRuntime, ModelOutputLimitError} from "../dist/anthropic-runtime.js";
import {ToolRejected} from "../dist/runtime.js";
import {SecretFilter} from "../dist/redaction.js";

// FL-15 and FL-13 (internals/docs/spec/2026-09-24-agent-fast-lane.md §8.1):
// the two ways one model turn can end short of a clean answer, and the fast
// lane's own rule for each.
async function fixture(handler: any) {
    const bodies: any[] = [];
    const headers: any[] = [];
    const server = createServer(async (req, res) => {
        let body = "";
        for await (const b of req) body += b.toString();
        bodies.push(JSON.parse(body));
        headers.push(req.headers);
        handler(req, res, bodies.length);
    });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const port = (server.address() as any).port;
    const network = {
        targets: [{hostname: "127.0.0.1", port, allow_private: true, allow_http_loopback: true}],
    };
    const d = {
        provider: {
            base_url: `http://127.0.0.1:${port}/v1`,
            model_id: "test-model",
            max_output_tokens: 256,
            network,
        },
        policy: {network},
        budget: {output_tokens: 256, tool_rounds: 3},
    };
    return {
        d,
        bodies,
        headers,
        close: async () => {
            server.closeAllConnections();
            await new Promise<void>((r) => server.close(() => r()));
        },
    };
}

function runtime(d: any, tools: any = {catalog: [], call: async () => ""}, bearerAuth = false) {
    return new AnthropicRuntime(
        d,
        async () => "test-key",
        tools,
        {
            signal: new AbortController().signal,
            deadline: Date.now() + 60000,
            assertCurrent: async () => {},
        } as any,
        new SecretFilter(),
        async () => {},
        undefined,
        bearerAuth,
    );
}

// Walks error.cause chains: the SDK can wrap a guardedFetch rejection in its
// own APIConnectionError, so a plain top-level message match is not reliable.
function errorChainMatches(error: unknown, pattern: RegExp): boolean {
    for (let e: any = error; e; e = e.cause) if (pattern.test(String(e?.message ?? e))) return true;
    return false;
}

const MESSAGE_START =
    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message",' +
    '"role":"assistant","model":"test-model","content":[],"stop_reason":null,"stop_sequence":null,' +
    '"usage":{"input_tokens":5,"output_tokens":0}}}\n\n';

function sseFullTurn(text: string, stopReason = "end_turn"): string {
    return (
        MESSAGE_START +
        'event: content_block_start\ndata: {"type":"content_block_start","index":0,' +
        '"content_block":{"type":"text","text":""}}\n\n' +
        `event: content_block_delta\ndata: ${JSON.stringify({
            type: "content_block_delta",
            index: 0,
            delta: {type: "text_delta", text},
        })}\n\n` +
        'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n' +
        `event: message_delta\ndata: ${JSON.stringify({
            type: "message_delta",
            delta: {stop_reason: stopReason, stop_sequence: null},
            usage: {output_tokens: 1},
        })}\n\n` +
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    );
}

const TOOL_USE_TURN =
    MESSAGE_START +
    'event: content_block_start\ndata: {"type":"content_block_start","index":0,' +
    '"content_block":{"type":"tool_use","id":"toolu_1","name":"grow_search","input":{}}}\n\n' +
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,' +
    '"delta":{"type":"input_json_delta","partial_json":"{}"}}\n\n' +
    'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n' +
    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use",' +
    '"stop_sequence":null},"usage":{"output_tokens":3}}\n\n' +
    'event: message_stop\ndata: {"type":"message_stop"}\n\n';

test("FL-15: stop_reason max_tokens fails the turn instead of returning truncated text", async () => {
    const f = await fixture((_req: any, res: any) => {
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.end(sseFullTurn("this got cut off before it finished", "max_tokens"));
    });
    try {
        await assert.rejects(
            () => runtime(f.d).sendTurn("hi"),
            (error: unknown) => error instanceof ModelOutputLimitError,
        );
    } finally {
        await f.close();
    }
});

test("FL-13: a call rejected before dispatch becomes an is_error tool_result, not a failed turn", async () => {
    const f = await fixture((_req: any, res: any, count: number) => {
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.end(count === 1 ? TOOL_USE_TURN : sseFullTurn("done after the tool failed"));
    });
    const tools = {
        catalog: [
            {name: "grow_search", description: "d", parameters: {type: "object", properties: {}}},
        ],
        call: async () => {
            // Only a ToolRejected (the call never ran) is safe to hand back
            // as a tool_result the model can retry from.
            throw new ToolRejected("boom");
        },
    };
    try {
        const answer = await runtime(f.d, tools).sendTurn("hi");
        assert.equal(answer, "done after the tool failed");
        assert.equal(f.bodies.length, 2);
        const secondTurnUserMessage = f.bodies[1].messages.at(-1);
        assert.equal(secondTurnUserMessage.role, "user");
        const toolResult = secondTurnUserMessage.content[0];
        assert.equal(toolResult.type, "tool_result");
        assert.equal(toolResult.is_error, true);
        assert.equal(toolResult.content, "boom");
    } finally {
        await f.close();
    }
});

test("FL-13: an error after dispatch still fails the whole turn, not just the one call", async () => {
    const f = await fixture((_req: any, res: any) => {
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.end(TOOL_USE_TURN);
    });
    const tools = {
        catalog: [
            {name: "grow_search", description: "d", parameters: {type: "object", properties: {}}},
        ],
        call: async () => {
            // A plain Error, standing in for a failure after the call already
            // dispatched (e.g. a team-tool topic.post whose reply timed out
            // after the server ran it): the outcome is unresolved, so a
            // second attempt in the same turn must never look safe.
            throw new Error("transient network error");
        },
    };
    try {
        await assert.rejects(() => runtime(f.d, tools).sendTurn("hi"), /transient network error/);
        // The turn ended on the first (and only) request; there was no
        // second round asking the model to retry the failed call.
        assert.equal(f.bodies.length, 1);
    } finally {
        await f.close();
    }
});

test("FL-13: a successful tool call still returns a plain (non-error) tool_result", async () => {
    const f = await fixture((_req: any, res: any, count: number) => {
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.end(count === 1 ? TOOL_USE_TURN : sseFullTurn("done"));
    });
    const tools = {
        catalog: [
            {name: "grow_search", description: "d", parameters: {type: "object", properties: {}}},
        ],
        call: async () => "search result",
    };
    try {
        const answer = await runtime(f.d, tools).sendTurn("hi");
        assert.equal(answer, "done");
        const toolResult = f.bodies[1].messages.at(-1).content[0];
        assert.equal(toolResult.is_error, undefined);
        assert.equal(toolResult.content, "search result");
    } finally {
        await f.close();
    }
});

// §9 "budget dan usage": the fast lane has no ModelBroker to enforce
// budget.input_tokens/output_tokens across a multi-round tool loop.
test("fast lane budget: max_tokens shrinks each round; over-budget usage fails the turn", async () => {
    const f = await fixture((_req: any, res: any) => {
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        // Every round is a tool_use turn (usage: input 5, output 3 - see
        // MESSAGE_START and TOOL_USE_TURN above), so the loop would otherwise
        // run for all 5 tool_rounds; the budget below must cut it short.
        res.end(TOOL_USE_TURN);
    });
    f.d.budget = {output_tokens: 5, tool_rounds: 5, input_tokens: 1000};
    const tools = {
        catalog: [
            {name: "grow_search", description: "d", parameters: {type: "object", properties: {}}},
        ],
        call: async () => "search result",
    };
    try {
        await assert.rejects(() => runtime(f.d, tools).sendTurn("hi"), /budget exhausted/);
        // Round 1 asked for the full 5 remaining; round 2 asked for only the
        // 2 left after round 1's usage (3) - not the same max_tokens twice.
        assert.equal(f.bodies.length, 2);
        assert.equal(f.bodies[0].max_tokens, 5);
        assert.equal(f.bodies[1].max_tokens, 2);
    } finally {
        await f.close();
    }
});

// §9 "Egress fetch": guardedFetch must bind to an address approveAddress
// already checked, not let the platform fetch() re-resolve DNS on its own.
test("guardedFetch: a private address the network policy did not approve is rejected", async () => {
    const f = await fixture((_req: any, res: any) => {
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.end(sseFullTurn("should never be reached"));
    });
    // Deny what the fixture's own policy normally allows for this loopback
    // target, so approveAddress rejects it before any request is sent.
    f.d.provider.network.targets[0].allow_private = false;
    try {
        await assert.rejects(
            () => runtime(f.d).sendTurn("hi"),
            (error: unknown) => errorChainMatches(error, /not approved/),
        );
        assert.equal(f.bodies.length, 0);
    } finally {
        await f.close();
    }
});

test("guardedFetch: a redirect from the provider is denied, not followed", async () => {
    const f = await fixture((_req: any, res: any) => {
        res.writeHead(307, {Location: "https://evil.example/"});
        res.end();
    });
    try {
        await assert.rejects(
            () => runtime(f.d).sendTurn("hi"),
            (error: unknown) => errorChainMatches(error, /Provider redirect denied/),
        );
    } finally {
        await f.close();
    }
});

// §12 step 12 / RUNTIME.md "fast_lane_bearer_providers".
test("client() sends the header style bearerAuth asks for, and never the other", async () => {
    for (const bearerAuth of [false, true]) {
        const f = await fixture((_req: any, res: any) => {
            res.writeHead(200, {"Content-Type": "text/event-stream"});
            res.end(sseFullTurn("ok"));
        });
        try {
            await runtime(f.d, undefined, bearerAuth).sendTurn("hi");
            const sent = f.headers[0];
            if (bearerAuth) {
                assert.equal(sent.authorization, "Bearer test-key");
                assert.equal(sent["x-api-key"], undefined);
            } else {
                assert.equal(sent["x-api-key"], "test-key");
                assert.equal(sent.authorization, undefined);
            }
        } finally {
            await f.close();
        }
    }
});
