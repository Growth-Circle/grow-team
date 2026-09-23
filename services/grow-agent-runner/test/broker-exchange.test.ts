import {test} from "node:test";
import assert from "node:assert/strict";
import {createServer, type IncomingMessage, type ServerResponse} from "node:http";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {exchange, requestTimeoutMs} from "../dist/broker-exchange.js";

test("a /tool ceiling follows the descriptor's shell budget, not a flat guess", () => {
    const deadline = Date.now() + 10 * 60 * 1000;
    assert.equal(requestTimeoutMs("/tool", 120, deadline), 120000 + 30000);
    assert.ok(
        requestTimeoutMs("/tool", 120, deadline) > 60000,
        "a full shell budget must outlive the old flat 60s cutoff",
    );
});

test("a /model ceiling is the remaining session deadline, not a flat guess", () => {
    const now = Date.now();
    assert.equal(requestTimeoutMs("/model", 120, now + 90 * 60 * 1000, now), 90 * 60 * 1000);
});

test("every ceiling stays bounded by what is left of the session", () => {
    const now = Date.now();
    const deadline = now + 500;
    assert.equal(requestTimeoutMs("/tool", 120, deadline, now), 500);
    assert.equal(requestTimeoutMs("/model", 120, deadline, now), 500);
});

async function withBroker(
    handler: (req: IncomingMessage, res: ServerResponse) => Promise<void>,
    run: (socket: string) => Promise<void>,
): Promise<void> {
    const dir = mkdtempSync(join(tmpdir(), "grow-broker-test-"));
    const socket = join(dir, "broker.sock");
    const server = createServer((req, res) => {
        handler(req, res).catch(() => {});
    });
    await new Promise<void>((resolve, reject) => {
        server.once("error", reject);
        server.listen(socket, resolve);
    });
    try {
        await run(socket);
    } finally {
        server.closeAllConnections();
        await new Promise<void>((r) => server.close(() => r()));
        rmSync(dir, {recursive: true, force: true});
    }
}

test("a call held open past a flat 60s guess still completes inside its real ceiling", async () => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    await withBroker(
        async (req, res) => {
            for await (const _chunk of req);
            await held; // Held well past the old flat 60s cutoff, comfortably inside a short test ceiling.
            res.writeHead(200, {"Content-Type": "application/json"}).end(JSON.stringify({text: "ok"}));
        },
        async (socket) => {
            const pending = exchange(socket, "/tool", {argv: ["true"]}, 300);
            await new Promise((r) => setTimeout(r, 120));
            release();
            assert.deepEqual(await pending, {text: "ok"});
        },
    );
});

test("a call is cut off once its own ceiling elapses with no response", async () => {
    await withBroker(
        async (req) => {
            for await (const _chunk of req);
            // Never responds inside the short ceiling given to exchange() below.
        },
        async (socket) => {
            await assert.rejects(() => exchange(socket, "/tool", {argv: ["true"]}, 50));
        },
    );
});
