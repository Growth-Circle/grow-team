import {test} from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {AnthropicRuntime} from "../dist/anthropic-runtime.js";
import {SecretFilter} from "../dist/redaction.js";

// FL-10 (internals/docs/spec/2026-09-24-agent-fast-lane.md §2.3 step 3, §8.1):
// a fast-lane stream that stops sending events is aborted after its idle
// window, instead of surviving to the job's full active_seconds budget
// (3,600 s in production). The window is passed in short for the test;
// production uses 30 s.
async function fixture(handler: any) {
    const server = createServer(async (req, res) => {
        let body = "";
        for await (const b of req) body += b.toString();
        handler(req, res, JSON.parse(body));
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
        budget: {output_tokens: 256, tool_rounds: 2},
    };
    return {
        d,
        close: async () => {
            server.closeAllConnections();
            await new Promise<void>((r) => server.close(() => r()));
        },
    };
}

function runtime(d: any, idleMs: number) {
    return new AnthropicRuntime(
        d,
        async () => "test-key",
        {catalog: [], call: async () => ""} as any,
        {
            signal: new AbortController().signal,
            deadline: Date.now() + 60000,
            assertCurrent: async () => {},
        } as any,
        new SecretFilter(),
        async () => {},
        idleMs,
    );
}

const MESSAGE_START =
    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message",' +
    '"role":"assistant","model":"test-model","content":[],"stop_reason":null,"stop_sequence":null,' +
    '"usage":{"input_tokens":5,"output_tokens":0}}}\n\n';

// A complete one-block text turn, written in a single response: message_start,
// one content_block_delta, then the three closing events.
function sseFullTurn(text: string): string {
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
        'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn",' +
        '"stop_sequence":null},"usage":{"output_tokens":1}}\n\n' +
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    );
}

test("a stream that goes silent is aborted after its idle window", async () => {
    const f = await fixture((_req: any, res: any) => {
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.write(MESSAGE_START);
        // No more bytes, and the connection is never closed: silence.
    });
    const idleMs = 40;
    const started = Date.now();
    try {
        await assert.rejects(
            () => runtime(f.d, idleMs).sendTurn("hi"),
            (error: unknown) => {
                // guardedFetch's own node:http(s) request surfaces the abort as a
                // plain Error, not the SDK's APIUserAbortError (that wrapping only
                // applies to the SDK's own fetch layer, which guardedFetch replaces).
                assert(
                    /abort/i.test(String((error as any)?.message ?? error)),
                    `wrong error: ${error}`,
                );
                assert(
                    Date.now() - started >= idleMs,
                    "aborted before the idle window actually elapsed",
                );
                return true;
            },
        );
    } finally {
        await f.close();
    }
});

test("a stream that keeps sending events survives past its idle window", async () => {
    const f = await fixture((_req: any, res: any) => {
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.write(MESSAGE_START);
        res.write(
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,' +
                '"content_block":{"type":"text","text":""}}\n\n',
        );
        let sent = 0;
        const pump = () => {
            res.write(
                `event: content_block_delta\ndata: ${JSON.stringify({
                    type: "content_block_delta",
                    index: 0,
                    delta: {type: "text_delta", text: "hi "},
                })}\n\n`,
            );
            sent++;
            // 15 * 25ms (375ms) of activity outlives a single 250ms idle
            // window with comfortable margin on a busy shared host: each gap
            // between pumps (25ms) leaves 225ms of slack before the window
            // (250ms) would fire, so ordinary scheduler jitter cannot flip
            // this from "survives" to "aborted".
            if (sent < 15) {
                setTimeout(pump, 25);
                return;
            }
            res.write(
                'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            );
            res.write(
                'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn",' +
                    '"stop_sequence":null},"usage":{"output_tokens":15}}\n\n',
            );
            res.write('event: message_stop\ndata: {"type":"message_stop"}\n\n');
            res.end();
        };
        pump();
    });
    try {
        const answer = await runtime(f.d, 250).sendTurn("hi");
        assert.equal(answer, "hi ".repeat(15));
    } finally {
        await f.close();
    }
});

test("the idle window is per model turn, not shared across the whole attempt", async () => {
    // A turn that finishes quickly must not leave a dangling timer that fires
    // during a later, unrelated turn on the same runtime instance.
    let turn = 0;
    const f = await fixture((_req: any, res: any) => {
        turn++;
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.end(sseFullTurn(`turn ${turn}`));
    });
    try {
        const r = runtime(f.d, 40);
        assert.equal(await r.sendTurn("first"), "turn 1");
        await new Promise((resolve) => setTimeout(resolve, 80)); // Past the first turn's window.
        assert.equal(await r.sendTurn("second"), "turn 2");
    } finally {
        await f.close();
    }
});
