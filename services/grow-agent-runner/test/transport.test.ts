import {test} from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore} from "../dist/config.js";
import {Journal} from "../dist/journal.js";
import {Transport, Connection, TransportError} from "../dist/transport.js";
async function server(handler: any) {
    const s = createServer(handler);
    await new Promise<void>((r) => s.listen(0, "127.0.0.1", r));
    const a = s.address() as any;
    return {
        origin: `http://127.0.0.1:${a.port}`,
        close: () => new Promise<void>((r) => s.close(() => r())),
    };
}
const ok = (res: any, data: any) => {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({schema_version: 1, result: "success", msg: "", ...data}));
};
const root = () => mkdtempSync(join(tmpdir(), "grow-transport-test-"));
test("event replay keeps its exact identity after a lost response and restart", async () => {
    const persisted = new Map();
    let lose = true;
    const s = await server(async (req: any, res: any) => {
        let raw = "";
        for await (const c of req) raw += c;
        const p = JSON.parse(raw);
        persisted.set(p.events[0].event_id, p);
        if (lose) {
            lose = false;
            req.socket.destroy();
            return;
        }
        ok(res, {receipts: [{event_sequence: 1, job_version: 3, status: "active"}]});
    });
    const dir = root();
    let j = new Journal(dir);
    let t = new Transport(s.origin, j, () => "access");
    const p = {schema_version: 1, job_version: 2, events: [{event_id: "event1"}]};
    j.prepare("event", "event1", "/runner/events", p);
    await assert.rejects(() => t.flush());
    j.close();
    j = new Journal(dir);
    t = new Transport(s.origin, j, () => "access");
    await t.flush();
    await t.flush();
    assert.equal(persisted.size, 1);
    assert.deepEqual(j.get("event1")?.request, p);
    assert.equal(j.get("event1")?.state, "done");
    j.close();
    await s.close();
});
test("uncertain consume never becomes execution permission", async () => {
    let calls = 0;
    const s = await server((req: any, res: any) => {
        calls++;
        req.socket.destroy();
    });
    const j = new Journal(root()),
        t = new Transport(s.origin, j, () => "access");
    await assert.rejects(() =>
        t.mutate("consume", "op1", "/runner/operations/consume", {operation_id: "op1"}),
    );
    await assert.rejects(
        () => t.mutate("consume", "op1", "/runner/operations/consume", {operation_id: "op1"}),
        /uncertain/,
    );
    assert.equal(calls, 1);
    j.close();
    await s.close();
});
test("redirects, policy failure and credential rejection remain distinct", async () => {
    let mode = "redirect";
    const s = await server((req: any, res: any) => {
        if (mode === "redirect") {
            res.writeHead(302, {location: "http://127.0.0.1:1/stolen"});
            res.end();
        } else {
            res.statusCode = mode === "credential" ? 401 : 400;
            res.end(
                JSON.stringify({
                    schema_version: 1,
                    result: "error",
                    code: mode === "credential" ? "credential_revoked" : undefined,
                }),
            );
        }
    });
    const j = new Journal(root()),
        t = new Transport(s.origin, j, () => "access");
    await assert.rejects(
        () => t.request("/runner/leases"),
        (e: any) => e.kind === "protocol",
    );
    mode = "policy";
    await assert.rejects(
        () => t.request("/runner/leases"),
        (e: any) => e.kind === "policy",
    );
    mode = "credential";
    await assert.rejects(
        () => t.request("/runner/leases"),
        (e: any) => e.kind === "credential" && e.code === "credential_revoked",
    );
    j.close();
    await s.close();
});
test("a busy server answer is retried until the server accepts the request", async () => {
    let calls = 0;
    const s = await server((req: any, res: any) => {
        calls++;
        if (calls <= 2) {
            res.writeHead(503, {"Retry-After": "1", "content-type": "application/json"});
            res.end(JSON.stringify({schema_version: 1, result: "error"}));
            return;
        }
        ok(res, {leases: []});
    });
    const j = new Journal(root()),
        t = new Transport(s.origin, j, () => "access");
    assert.deepEqual((await t.request("/runner/leases")).leases, []);
    assert.equal(calls, 3);
    j.close();
    await s.close();
});
test("lost exchange and rotation require explicit recovery across restart", async () => {
    let exchanged = false,
        rotated = false;
    const s = await server(async (req: any, res: any) => {
        let raw = "";
        for await (const c of req) raw += c;
        const data = JSON.parse(raw);
        assert(!req.url.includes("secret"));
        if (req.url.endsWith("/pairings"))
            return ok(res, {
                pairing_id: "p1",
                user_code: "ABCD",
                state: "pending",
                expires_at: "2099-01-01T00:00:00Z",
            });
        if (req.url.endsWith("/status"))
            return ok(res, {
                pairing_id: "p1",
                state: exchanged ? "exchanged" : "approved",
                runner_id: exchanged ? "orphan1" : null,
            });
        if (req.url.endsWith("/exchange")) {
            exchanged = true;
            req.socket.destroy();
            return;
        }
        if (req.url.endsWith("/refresh")) {
            rotated = true;
            req.socket.destroy();
            return;
        }
    });
    const dir = root(),
        store = new PrivateStore(dir),
        j = new Journal(dir),
        t = new Transport(s.origin, j, () => null);
    let c = new Connection(store, t);
    await c.start("device");
    await assert.rejects(() => c.poll());
    c = new Connection(store, t);
    const status = await c.poll();
    assert.equal(status.state, "re_pair_required");
    assert.equal(store.read("connection.json")?.orphan_runner_id, "orphan1");
    store.write("connection.json", {
        origin: s.origin,
        state: "connected",
        runner_id: "r1",
        token: "old",
        refresh_token: "refresh",
        expires_at: "2000-01-01T00:00:00Z",
        refresh_expires_at: "2099-01-01T00:00:00Z",
    });
    await assert.rejects(() => c.rotate());
    assert(rotated);
    c = new Connection(store, t);
    await assert.rejects(() => c.rotate(), /re-pair/);
    assert.equal(store.read("connection.json")?.state, "rotation_uncertain");
    j.close();
    await s.close();
});
test("rotation cannot change the connected runner identity", async () => {
    const s = await server((_req: any, res: any) =>
        ok(res, {
            runner_id: "other",
            token: "new",
            refresh_token: "new-refresh",
            expires_at: "2099-01-01T00:00:00Z",
            refresh_expires_at: "2099-02-01T00:00:00Z",
        }),
    );
    const dir = root(),
        store = new PrivateStore(dir),
        j = new Journal(dir);
    store.write("connection.json", {
        origin: s.origin,
        state: "connected",
        runner_id: "original",
        token: "old",
        refresh_token: "old-refresh",
        expires_at: "2099-01-01T00:00:00Z",
        refresh_expires_at: "2099-02-01T00:00:00Z",
    });
    const c = new Connection(store, new Transport(s.origin, j, () => null));
    await assert.rejects(
        () => c.rotate(),
        (e: any) => e.code === "runner_identity_changed",
    );
    assert.equal(store.read("connection.json")?.runner_id, "original");
    assert.equal(store.read("connection.json")?.state, "rotation_uncertain");
    j.close();
    await s.close();
});
