import {test} from "node:test";
import assert from "node:assert/strict";
import {createServer, type IncomingMessage, type ServerResponse} from "node:http";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {exchange, requestTimeoutMs} from "../dist/broker-exchange.js";

test("every ceiling is the remaining supervisor deadline, not a flat guess", () => {
    const now = Date.now();
    assert.equal(requestTimeoutMs(now + 90 * 60 * 1000, now), 90 * 60 * 1000);
    assert.equal(requestTimeoutMs(now + 500, now), 500);
});

test("the ceiling never drops below one millisecond, even once the deadline has passed", () => {
    const now = Date.now();
    assert.equal(requestTimeoutMs(now - 5000, now), 1);
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

test("a call held longer than the old shell-plus-30s ceiling still completes before the deadline", async () => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    await withBroker(
        async (req, res) => {
            for await (const _chunk of req);
            // Held past what the old fixed 30s tool margin allowed, scaled down so the
            // test stays fast: comfortably inside this call's real, deadline-based ceiling.
            await held;
            res.writeHead(200, {"Content-Type": "application/json"}).end(JSON.stringify({text: "ok"}));
        },
        async (socket) => {
            const pending = exchange(socket, "/tool", {argv: ["true"]}, requestTimeoutMs(Date.now() + 300));
            await new Promise((r) => setTimeout(r, 120));
            release();
            assert.deepEqual(await pending, {text: "ok"});
        },
    );
});

test("a call is cut off at the deadline with no response", async () => {
    await withBroker(
        async (req) => {
            for await (const _chunk of req);
            // Never responds inside the short ceiling given to exchange() below.
        },
        async (socket) => {
            await assert.rejects(() =>
                exchange(socket, "/tool", {argv: ["true"]}, requestTimeoutMs(Date.now() + 50)),
            );
        },
    );
});
