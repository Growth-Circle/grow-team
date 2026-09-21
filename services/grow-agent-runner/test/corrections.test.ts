import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, readFileSync, readdirSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Journal} from "../dist/journal.js";
import {PrivateStore} from "../dist/config.js";
import {Transport, Connection, TransportError} from "../dist/transport.js";
import {OwnerRegistry} from "../dist/owner.js";
import {Coordinator} from "../dist/supervisor.js";
import {digest, parse} from "../dist/protocol.js";
const fixtures = JSON.parse(
    readFileSync(
        new URL("../../../zerver/tests/fixtures/agents/protocol-v1.json", import.meta.url),
        "utf8",
    ),
);
function descriptor(n: number) {
    const d = structuredClone(fixtures.valid[0].payload);
    d.attempt_id = `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
    d.lease_expires_at = new Date(Date.now() + 60000).toISOString();
    d.configuration_digest = digest(d.tested_configuration);
    d.descriptor_digest = digest(
        Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
    );
    return d;
}
const payload = {
    process_state: "active",
    adapter_session_ref: null,
    stop_confirmed: false,
    summary: "",
};
const root = () => mkdtempSync(join(tmpdir(), "grow-correction-test-"));
async function harness() {
    const dir = root(),
        j = new Journal(dir),
        channels: any[] = [],
        events: any[] = [];
    let claim = 0;
    const t = new Transport("http://localhost", j, () => "access");
    t.request = async (route, p) => {
        if (route === "/runner/leases") return {leases: []};
        if (route === "/runner/claims") return {attempt: descriptor(++claim), job_version: 1};
        if (route === "/runner/events") {
            events.push(p!.events[0]);
            return {receipts: [{job_version: 2}]};
        }
        return {receipt: {job_version: 3}};
    };
    const s: any = {
        inspect: async () => [],
        canExecute: () => true,
        start: async (d: any, c: any) => channels.push(c),
        stop: async () => ({confirmed: true}),
        applyInput: async () => {
            throw Error("reply lost");
        },
    };
    const c = new Coordinator(j, t, s, descriptor(1).runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    return {dir, j, t, s, c, channels, events};
}
test("retired channel cannot read, emit, or propose under the next attempt", async () => {
    const h = await harness();
    await h.c.stopActive();
    await h.c.claim();
    assert.throws(() => h.channels[0].lease(), /retired/);
    await assert.rejects(() => h.channels[0].event("attempt.started", payload), /retired/);
    await assert.rejects(
        () =>
            h.channels[0].operations.propose(h.channels[1].lease(), "op", {
                action: "context.read",
                context_ids: ["00000000-0000-4000-8000-000000000001"],
            }),
        /retired/,
    );
    assert.equal(h.events.length, 0);
    await h.c.stopActive();
    h.j.close();
});
test("event queue replays the uncertain event before a later event", async () => {
    const h = await harness();
    const request = h.t.request.bind(h.t);
    let lose = true;
    h.t.request = async (r, p, a) => {
        if (r === "/runner/events" && lose) {
            lose = false;
            throw new TransportError("transient");
        }
        return request(r, p, a);
    };
    await assert.rejects(() => h.channels[0].event("attempt.started", payload));
    await h.channels[0].event("attempt.started", payload);
    assert.deepEqual(
        h.events.map((e) => e.sequence),
        [1, 2],
    );
    assert(h.j.list("event").every((e) => e.state === "done"));
    await h.c.stopActive();
    h.j.close();
});
test("queued old callback and delayed response cannot mutate a new session", async () => {
    const h = await harness();
    let release!: () => void;
    const barrier = new Promise<void>((r) => (release = r)),
        request = h.t.request.bind(h.t);
    h.t.request = async (r, p, a) => {
        if (r === "/runner/events") await barrier;
        return request(r, p, a);
    };
    const first = h.channels[0].event("attempt.started", payload);
    const firstRejected = assert.rejects(first, /retired/);
    await new Promise((r) => setImmediate(r));
    const queued = h.channels[0].event("attempt.started", payload);
    const queuedRejected = assert.rejects(queued, /retired/);
    await h.c.stopActive();
    await h.c.claim();
    release();
    await firstRejected;
    await queuedRejected;
    assert.equal(h.channels[1].lease().job_version, 1);
    assert.equal(h.events.length, 1);
    await h.c.stopActive();
    h.j.close();
});
test("re-pair preserves old evidence but reports catalogs and workspaces to the new runner", async () => {
    const dir = root(),
        store = new PrivateStore(dir),
        j = new Journal(dir),
        reported: string[] = [];
    store.write("connection.json", {
        origin: "http://localhost",
        state: "connected",
        runner_id: "old",
        token: "old",
        fingerprint: "f",
    });
    const t = new Transport("http://localhost", j, () => store.read("connection.json")!.token);
    t.request = async (r, p) => {
        if (r === "/runner/catalog" || r === "/runner/workspaces") {
            reported.push(`${store.read("connection.json")!.runner_id}:${r}`);
            return r.endsWith("catalog")
                ? {catalog_revision: 1}
                : {
                      repository: {
                          id: store.read("connection.json")!.runner_id,
                          revision: 1,
                          policy_version: 1,
                      },
                  };
        }
        if (r === "/pairings")
            return {pairing_id: "p", user_code: "CODE", expires_at: "2099-01-01T00:00:00Z"};
        if (r === "/pairings/status") return {state: "approved"};
        if (r === "/pairings/exchange")
            return {
                runner_id: "new",
                token: "new",
                refresh_token: "refresh",
                expires_at: "2099-01-01T00:00:00Z",
                refresh_expires_at: "2099-02-01T00:00:00Z",
            };
        throw Error(r);
    };
    const registry = new OwnerRegistry(store, t),
        connection = new Connection(store, t),
        catalog = {revision: 1, adapters: [], sandboxes: []},
        metadata = {
            canonical_origin: null,
            allowed_refs: ["main"],
            required_checks: [],
            revision: 1,
        };
    await registry.catalog(catalog);
    await registry.workspace("app", dir, metadata);
    const old = j.list().find((e) => e.kind === "catalog")!;
    j.prepare("event", "legacy-event", "/runner/events", {events: []});
    j.uncertain("legacy-event");
    await connection.start("device", true);
    await connection.poll();
    await assert.rejects(() => t.send(old), /runner|scope/);
    await registry.catalog(catalog);
    await registry.workspace("app", dir, metadata);
    assert.equal(reported.length, 4);
    assert.equal(store.read("registry.json")!.workspaces.app.path, dir);
    assert.equal(store.read("registry.json")!.workspaces.app.repository.id, "new");
    assert(readdirSync(dir).some((n) => n.startsWith("connection-history-")));
    assert(j.list().some((e) => e.id === old.id));
    const uncertain = j.get("runner:old/legacy-event")!;
    assert.equal(uncertain.state, "uncertain");
    await assert.rejects(() => t.send(uncertain), /runner|scope/);
    j.close();
});
for (const outcome of ["applied", "not_applied"] as const)
    test(`stopped input ${outcome} receipt clears local uncertainty without an execution lease`, async () => {
        const h = await harness(),
            input = {
                ...fixtures.valid.find((c: any) => c.schema === "input").payload,
                delivery_state: "delivered",
            };
        await assert.rejects(() => h.c.applyInput(descriptor(1), input));
        await h.c.stopActive();
        let calls = 0;
        h.t.request = async (r, p) => {
            assert.equal(r, "/runner/inputs/reconcile");
            assert.equal(p!.attempt_id, descriptor(1).attempt_id);
            calls++;
            return {
                input: {...input, delivery_state: outcome === "applied" ? "applied" : "pending"},
            };
        };
        await h.c.reconcileInput(descriptor(1).attempt_id, input, {
            outcome,
            receipt_id: "00000000-0000-4000-8000-000000000010",
        });
        assert.equal(calls, 1);
        assert.equal(h.j.get(`input:${descriptor(1).attempt_id}:${input.id}`)?.state, "done");
        assert.throws(() => h.channels[0].lease());
        h.j.close();
    });
test("authority event rejects every mismatched payload class", () => {
    const base = {
        schema_version: 1,
        job_id: "00000000-0000-4000-8000-000000000001",
        attempt_id: null,
        lease_epoch: null,
        event_id: "00000000-0000-4000-8000-000000000002",
        sequence: 1,
        occurred_at: new Date().toISOString(),
    };
    for (const type of [
        "job.queued",
        "attempt.starting",
        "attempt.interrupted",
        "job.completed",
        "attempt.stop_requested",
        "input.received",
        "approval.requested",
        "approval.resolved",
        "result.published",
        "publication.blocked",
    ])
        assert.throws(() =>
            parse("authority_event", {
                ...base,
                type,
                payload: ["result.published", "publication.blocked"].includes(type)
                    ? {status: "completed", job_version: 1, reason: ""}
                    : {result_message_id: null, reason: "wrong payload"},
            }),
        );
});
for (const state of ["rotation_uncertain", "expired_refresh"])
    test(`service contains old processes before ${state} credential failure`, async () => {
        const {runService} = await import("../dist/supervisor.js");
        const dir = root(),
            store = new PrivateStore(dir);
        store.write("connection.json", {
            origin: "http://localhost",
            state: state === "rotation_uncertain" ? state : "connected",
            runner_id: "old",
            token: "expired",
            refresh_token: "expired",
            expires_at: "2000-01-01T00:00:00Z",
            refresh_expires_at: "2000-01-01T00:00:00Z",
        });
        const j = new Journal(dir),
            t = new Transport("http://localhost", j, () => "expired"),
            log: string[] = [];
        t.request = async () => {
            log.push("network");
            throw Error("network not permitted");
        };
        const c = new Coordinator(
            j,
            t,
            {
                inspect: async () => {
                    log.push("inspect");
                    return [{attempt_id: "old-attempt"}];
                },
                stop: async () => {
                    log.push("stop");
                    return {confirmed: true};
                },
                canExecute: () => false,
            } as any,
            "old",
            {assertRuntime: () => {}},
        );
        await assert.rejects(() =>
            runService(c, new Connection(store, t), t, new AbortController().signal),
        );
        assert.deepEqual(log, ["inspect", "stop", "inspect", "stop"]);
        j.close();
    });
test("not_applied permits one fresh delivery and applied reconciliation suppresses duplication", async () => {
    const h = await harness(),
        input = {
            ...fixtures.valid.find((c: any) => c.schema === "input").payload,
            delivery_state: "delivered",
        },
        d = descriptor(1);
    let effects = 0;
    h.s.applyInput = async () => {
        effects++;
        throw Error("uncertain runtime receipt");
    };
    await assert.rejects(() => h.c.applyInput(d, input));
    const original = h.t.request.bind(h.t);
    h.t.request = async (r, p, a) =>
        r === "/runner/inputs/reconcile"
            ? {
                  input: {
                      ...input,
                      delivery_state: p!.outcome === "not_applied" ? "pending" : "applied",
                  },
              }
            : original(r, p, a);
    await h.c.reconcileInput(d.attempt_id, input, {
        outcome: "not_applied",
        receipt_id: "00000000-0000-4000-8000-000000000021",
    });
    await assert.rejects(() => h.c.applyInput(d, input), /fresh attempt/);
    assert.throws(() => h.channels[0].lease(), /retired/);
    await h.c.claim();
    const next = descriptor(2);
    await assert.rejects(() => h.c.applyInput(next, input));
    await assert.rejects(() => h.c.applyInput(next, input), /uncertain/);
    assert.equal(effects, 2);
    await h.c.reconcileInput(next.attempt_id, input, {
        outcome: "applied",
        receipt_id: "00000000-0000-4000-8000-000000000022",
    });
    await h.c.applyInput(next, input);
    assert.equal(effects, 2);
    await h.c.stopActive();
    h.j.close();
});
test("stopped input receipt survives journal restart", async () => {
    const h = await harness(),
        input = {
            ...fixtures.valid.find((c: any) => c.schema === "input").payload,
            delivery_state: "delivered",
        };
    await assert.rejects(() => h.c.applyInput(descriptor(1), input));
    await h.c.stopActive();
    h.j.close();
    const j = new Journal(h.dir),
        t = new Transport("http://localhost", j, () => "access");
    t.request = async (r, p) => {
        assert.equal(r, "/runner/inputs/reconcile");
        assert.equal(p!.job_version, 3);
        return {input: {...input, delivery_state: "applied"}};
    };
    const c = new Coordinator(j, t, h.s, descriptor(1).runner_id, {assertRuntime: () => {}});
    await c.reconcileInput(descriptor(1).attempt_id, input, {
        outcome: "applied",
        receipt_id: "00000000-0000-4000-8000-000000000023",
    });
    assert.equal(j.get(`input:${descriptor(1).attempt_id}:${input.id}`)!.state, "done");
    j.close();
});
test("stop fences a delayed claim before launch", async () => {
    const h = await harness();
    await h.c.stopActive();
    const request = h.t.request.bind(h.t);
    let release!: () => void;
    const wait = new Promise<void>((r) => (release = r));
    h.t.request = async (r, p, a) => {
        if (r === "/runner/claims") await wait;
        return request(r, p, a);
    };
    const pending = h.c.claim();
    await new Promise((r) => setImmediate(r));
    await h.c.stopActive();
    release();
    await pending;
    assert.equal(h.channels.length, 1);
    h.j.close();
});

test("lost input reconciliation response reuses its request after stop changes the version", async () => {
    const h = await harness();
    const input = {
        ...fixtures.valid.find((c: any) => c.schema === "input").payload,
        delivery_state: "delivered",
    };
    await assert.rejects(() => h.c.applyInput(descriptor(1), input));
    const original = h.t.request.bind(h.t),
        requests: any[] = [];
    h.t.request = async (route, data, anonymous) => {
        if (route !== "/runner/inputs/reconcile") return original(route, data, anonymous);
        requests.push(structuredClone(data));
        if (requests.length === 1) throw new TransportError("transient");
        return {input: {...input, delivery_state: "applied"}};
    };
    const receipt = {
        outcome: "applied" as const,
        receipt_id: "00000000-0000-4000-8000-000000000025",
    };
    await assert.rejects(() => h.c.reconcileInput(descriptor(1).attempt_id, input, receipt));
    await h.c.stopActive();
    await h.c.reconcileInput(descriptor(1).attempt_id, input, receipt);
    assert.deepEqual(requests[0], requests[1]);
    assert.equal(h.j.get(`input:${descriptor(1).attempt_id}:${input.id}`)!.state, "done");
    h.j.close();
});

test("active polling preserves the attempt heartbeat identity", async () => {
    const h = await harness(),
        d = descriptor(1),
        heartbeats: any[] = [];
    h.t.request = async (route, data) => {
        if (route === "/runner/controls")
            return {
                controls: [
                    {
                        attempt_id: d.attempt_id,
                        lease_epoch: d.lease_epoch,
                        control: "continue",
                        job_version: 1,
                    },
                ],
            };
        if (route === "/runner/heartbeat") {
            heartbeats.push(data);
            return {
                leases: [
                    {
                        attempt_id: d.attempt_id,
                        lease_epoch: d.lease_epoch,
                        control: "continue",
                        job_version: 1,
                        lease_expires_at: d.lease_expires_at,
                    },
                ],
            };
        }
        if (route === "/runner/inputs") return {inputs: []};
        if (route === "/runner/stop-evidence") return {receipt: {job_version: 2}};
        throw Error(route);
    };
    await h.c.tick();
    assert.deepEqual(heartbeats, [
        {
            schema_version: 1,
            leases: [{job_id: d.job_id, attempt_id: d.attempt_id, lease_epoch: d.lease_epoch}],
        },
    ]);
    assert.equal(h.channels[0].lease().attempt_id, d.attempt_id);
    await h.c.stopActive();
    h.j.close();
});

for (const confirmed of [true, false]) {
    test(`refresh failure requires confirmed containment before backoff: ${confirmed}`, async () => {
        const {runService} = await import("../dist/supervisor.js");
        const store = new PrivateStore(root()),
            d = descriptor(1);
        store.write("connection.json", {
            origin: "http://localhost",
            state: "connected",
            runner_id: d.runner_id,
            token: "access",
            refresh_token: "refresh",
            expires_at: new Date(Date.now() + 300000).toISOString(),
            refresh_expires_at: "2099-01-01T00:00:00Z",
        });
        const journal = new Journal(store.root),
            transport = new Transport(
                "http://localhost",
                journal,
                () => store.read("connection.json")!.token,
            );
        const failure = new TransportError("transient");
        let alive = false,
            stops = 0;
        transport.request = async (route) => {
            if (route === "/runner/leases" || route === "/runner/heartbeat") return {leases: []};
            if (route === "/runner/claims") return {attempt: d, job_version: 1};
            if (route === "/runner/token/refresh") throw failure;
            throw Error(route);
        };
        const coordinator = new Coordinator(
            journal,
            transport,
            {
                inspect: async () => [],
                canExecute: () => true,
                start: async () => {
                    alive = true;
                },
                stop: async () => {
                    stops++;
                    if (confirmed) alive = false;
                    return {confirmed};
                },
            } as any,
            d.runner_id,
            {assertRuntime: () => {}},
        );
        transport.poll = async (callback) => {
            await callback();
            store.write("connection.json", {
                ...store.read("connection.json"),
                expires_at: "2000-01-01T00:00:00Z",
            });
            try {
                await callback();
                assert.fail("Expected refresh failure");
            } catch (error) {
                // This is the boundary where the real poller classifies errors for backoff.
                assert.equal(stops, 1);
                assert.equal(alive, !confirmed);
                assert.equal(store.read("connection.json")!.state, "rotation_uncertain");
                if (confirmed) assert.equal(error, failure);
                else {
                    assert(!(error instanceof TransportError));
                    assert.match((error as Error).message, /Cannot confirm process stop/);
                }
                throw error;
            }
        };
        await assert.rejects(
            () =>
                runService(
                    coordinator,
                    new Connection(store, transport),
                    transport,
                    new AbortController().signal,
                ),
            confirmed ? TransportError : /Cannot confirm process stop/,
        );
        journal.close();
    });
}

for (const delayed of ["/runner/controls", "/runner/heartbeat"]) {
    test(`delayed ${delayed} cannot lower a version confirmed by an event`, async () => {
        const h = await harness(),
            d = descriptor(1);
        let release!: () => void, entered!: () => void;
        const wait = new Promise<void>((resolve) => {
                release = resolve;
            }),
            reached = new Promise<void>((resolve) => {
                entered = resolve;
            });
        h.t.request = async (route, data) => {
            if (route === delayed) {
                entered();
                await wait;
            }
            if (route === "/runner/controls")
                return {
                    controls: [
                        {
                            attempt_id: d.attempt_id,
                            lease_epoch: d.lease_epoch,
                            control: "continue",
                            job_version: 1,
                        },
                    ],
                };
            if (route === "/runner/heartbeat")
                return {
                    leases: [
                        {
                            attempt_id: d.attempt_id,
                            lease_epoch: d.lease_epoch,
                            control: "continue",
                            job_version: 1,
                            lease_expires_at: d.lease_expires_at,
                        },
                    ],
                };
            if (route === "/runner/events") return {receipts: [{job_version: 2}]};
            if (route === "/runner/inputs") {
                assert.equal(data!.job_version, 2);
                return {inputs: []};
            }
            if (route === "/runner/stop-evidence") return {receipt: {job_version: 3}};
            throw Error(route);
        };
        const tick = h.c.tick();
        await reached;
        await h.channels[0].event("attempt.started", payload);
        release();
        await tick;
        assert.equal(h.channels[0].lease().job_version, 2);
        await h.c.stopActive();
        h.j.close();
    });
}

for (const kind of ["local", "remote"] as const) {
    test(`${kind} operation recovery survives stop, restart, and lost receipt response`, async () => {
        const h = await harness(),
            d = descriptor(1),
            lease = h.channels[0].lease();
        const operationId = "00000000-0000-4000-8000-000000000031",
            operationHash = "a".repeat(64);
        h.j.prepare("consume", `consume:${operationId}`, "/runner/operations/consume", {
            ...lease,
            operation_id: operationId,
            operation_hash: operationHash,
            expected_version: 1,
        });
        h.j.uncertain(`consume:${operationId}`);
        h.j.prepare("effect", `effect:${operationId}`, "local", {
            operation: {operation_id: operationId},
        });
        h.j.uncertain(`effect:${operationId}`);
        await h.c.stopActive();
        await assert.rejects(() => h.channels[0].operations.reconcile(lease), /retired/);
        h.j.close();
        const journal = new Journal(h.dir),
            transport = new Transport("http://localhost", journal, () => "access");
        const requests: any[] = [];
        const result = {
            operation_id: operationId,
            operation_hash: operationHash,
            status: kind === "local" ? "cancelled" : "succeeded",
        };
        transport.request = async (route, data) => {
            assert.equal(data!.attempt_id, d.attempt_id);
            assert.equal(data!.lease_epoch, d.lease_epoch);
            if (route === "/runner/operations") return {operations: [result]};
            assert.equal(
                route,
                kind === "local"
                    ? "/runner/operations/reconcile-local"
                    : "/runner/operations/reconcile",
            );
            requests.push(structuredClone(data));
            if (requests.length === 1) throw new TransportError("transient");
            return {operation: result};
        };
        const coordinator = new Coordinator(journal, transport, h.s, d.runner_id, {
            assertRuntime: () => {},
        });
        const recovery = (coordinator as any).operationRecovery(d.attempt_id);
        assert.equal(recovery.propose, undefined);
        assert.equal(recovery.consume, undefined);
        assert.equal(recovery.beginEffect, undefined);
        assert.deepEqual((await recovery.list()).operations, [result]);
        const receipt =
            kind === "local"
                ? {
                      operation_id: operationId,
                      argument_digest: operationHash,
                      base_commit: null,
                      tree_hash: null,
                      outcome: "no_effect",
                      observed_at: new Date().toISOString(),
                  }
                : {
                      operation_id: operationId,
                      remote: "https://example.com/repo",
                      branch: "grow-agent/result",
                      commit: "b".repeat(40),
                      pull_request_id: null,
                      pull_request_url: null,
                      observed_at: new Date().toISOString(),
                  };
        const submit = () =>
            kind === "local" ? recovery.localReceipt(receipt) : recovery.remoteReceipt(receipt);
        await assert.rejects(submit, TransportError);
        await submit();
        await submit();
        assert.equal(requests.length, 2);
        assert.deepEqual(requests[0], requests[1]);
        assert.equal(journal.get(`effect:${operationId}`)!.state, "done");
        await assert.rejects(
            () =>
                kind === "local"
                    ? recovery.localReceipt({...receipt, outcome: "failed"})
                    : recovery.remoteReceipt({...receipt, commit: "c".repeat(40)}),
            /conflict/i,
        );
        assert.equal(journal.get(`consume:${operationId}`)!.state, "uncertain");
        transport.currentScope = () => "runner:replacement";
        await assert.rejects(() => recovery.list(), /retired runner scope/);
        assert.throws(() =>
            (coordinator as any).operationRecovery("00000000-0000-4000-8000-000000000099"),
        );
        journal.close();
    });
}
