import {test} from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {ModelBroker} from "../dist/model-broker.js";
async function fixture(handler: any, assertLocal?: () => void) {
    let calls = 0;
    const server = createServer(async (req, res) => {
        let body = "";
        for await (const b of req) body += b.toString();
        calls++;
        handler(req, res, JSON.parse(body), calls);
    });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const port = (server.address() as any).port;
    const network = {
        targets: [{hostname: "127.0.0.1", port, allow_private: true, allow_http_loopback: true}],
    };
    const abort = new AbortController();
    const provider = {
        base_url: `http://127.0.0.1:${port}/v1`,
        api_mode: "responses",
        model_id: "fixed",
        allowed_models: ["fixed"],
        network,
        context_window_tokens: 100000,
        max_output_tokens: 32,
    };
    const model = new ModelBroker(
        provider,
        {network},
        {
            input_tokens: 100000,
            output_tokens: 256,
            tool_rounds: 4,
            context_recoveries: 1,
            transport_retries: 2,
        },
        {
            signal: abort.signal,
            deadline: Date.now() + 5000,
            assertLocal,
            assertCurrent: async () => {
                if (abort.signal.aborted) throw Error("revoked");
            },
        },
        async () => "CANARY_PROVIDER_SECRET",
    );
    return {
        model,
        calls: () => calls,
        abort,
        close: async () => {
            server.closeAllConnections();
            await new Promise<void>((r) => server.close(() => r()));
        },
    };
}
test("explicit transient response retries within budget and retains fixed authorization", async () => {
    const f = await fixture((req: any, res: any, body: any, count: number) => {
        assert.equal(req.url, "/v1/responses");
        assert.equal(body.model, "fixed");
        assert.equal(req.headers.authorization, "Bearer CANARY_PROVIDER_SECRET");
        if (count === 1) {
            res.writeHead(429, {"Retry-After": "0"}).end("{}");
            return;
        }
        res.writeHead(200, {"Content-Type": "application/json"}).end(
            JSON.stringify({
                status: "completed",
                output: [
                    {
                        type: "message",
                        content: [{type: "output_text", text: "CANARY_PROVIDER_SECRET"}],
                    },
                ],
                usage: {input_tokens: 1, output_tokens: 1},
            }),
        );
    });
    try {
        assert.equal(
            (await f.model.turn([{role: "user", text: "synthetic"}], [])).text,
            "[REDACTED]",
        );
        assert.equal(f.calls(), 2);
    } finally {
        await f.close();
    }
});
for (const status of [302, 401, 403, 400, 429])
    test(`provider ${status} recovery failure has no secret echo or fallback`, async () => {
        const f = await fixture((_req: any, res: any) =>
            res
                .writeHead(status, {
                    Location: "https://unapproved.invalid/",
                    "Content-Type": "application/json",
                })
                .end(
                    JSON.stringify({
                        error: {
                            code: status === 429 ? "insufficient_quota" : "bad",
                            message: "CANARY_PROVIDER_SECRET",
                        },
                    }),
                ),
        );
        try {
            await assert.rejects(
                () => f.model.turn([{role: "user", text: "synthetic"}], []),
                (error: Error) => !error.message.includes("CANARY_PROVIDER_SECRET"),
            );
            assert.equal(f.calls(), 1);
        } finally {
            await f.close();
        }
    });

test("local probe freshness loss aborts an in-flight provider stream", async () => {
    let current = true,
        entered!: () => void;
    const reached = new Promise<void>((r) => {
        entered = r;
    });
    const f = await fixture(
        (_req: any, res: any) => {
            res.writeHead(200, {"Content-Type": "text/event-stream"});
            res.write('data: {"partial":');
            entered();
        },
        () => {
            if (!current) throw Error("Probe stale");
        },
    );
    try {
        const pending = assert.rejects(() => f.model.turn([{role: "user", text: "synthetic"}], []));
        await reached;
        current = false;
        await pending;
        assert.equal(f.calls(), 1);
    } finally {
        await f.close();
    }
});

test("text-only endpoint accepts a chat probe without tool fields", async () => {
    const f = await fixture((_req: any, res: any, body: any) => {
        if ("tools" in body || "parallel_tool_calls" in body) {
            res.writeHead(400).end('{"error":{"code":"tools_unsupported"}}');
            return;
        }
        res.writeHead(200, {"Content-Type": "application/json"}).end(
            JSON.stringify({
                status: "completed",
                output: [{type: "message", content: [{type: "output_text", text: "PROBE_OK"}]}],
                usage: {input_tokens: 1, output_tokens: 1},
            }),
        );
    });
    try {
        assert.equal(
            (await f.model.turn([{role: "user", text: "Reply PROBE_OK"}], [])).text,
            "PROBE_OK",
        );
        assert.equal(f.calls(), 1);
    } finally {
        await f.close();
    }
});
