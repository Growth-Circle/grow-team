import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Journal} from "../dist/journal.js";
import {Coordinator, OperationBoundary} from "../dist/supervisor.js";
import {digest, effectiveConfiguration} from "../dist/protocol.js";
const root = () => mkdtempSync(join(tmpdir(), "grow-supervisor-test-"));
const fixtures = JSON.parse(
    readFileSync(
        new URL("../../../zerver/tests/fixtures/agents/protocol-v1.json", import.meta.url),
        "utf8",
    ),
);
function descriptor() {
    const d = structuredClone(fixtures.valid[0].payload);
    d.lease_expires_at = new Date(Date.now() + 60000).toISOString();
    d.configuration_digest = digest(d.tested_configuration);
    d.descriptor_digest = digest(
        Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
    );
    return d;
}
function probeDescriptor() {
    const d = structuredClone(
        fixtures.valid.find((c: any) => c.schema === "probe_descriptor").payload,
    );
    d.grant.expires_at = new Date(Date.now() + 60000).toISOString();
    d.configuration_digest = digest(effectiveConfiguration(d));
    d.descriptor_digest = digest(
        Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
    );
    return d;
}
test("startup stops old processes and reconciles leases before claims", async () => {
    const log: string[] = [],
        d = descriptor(),
        j = new Journal(root());
    const transport: any = {
        journal: j,
        request: async (route: string) => {
            log.push(route);
            if (route === "/runner/leases")
                return {
                    leases: [
                        {
                            descriptor: d,
                            job_version: 3,
                            lease_expires_at: d.lease_expires_at,
                            process_state: "active",
                        },
                    ],
                };
            return {controls: []};
        },
        mutate: async (kind: string, id: string, route: string, body: any) => {
            log.push(route);
            return {attempt: null, receipt: {job_version: 4}};
        },
        flush: async () => {
            log.push("flush");
        },
    };
    const supervisor: any = {
        inspect: async () => {
            log.push("inspect");
            return [{attempt_id: d.attempt_id}];
        },
        stop: async () => {
            log.push("stop");
            return {confirmed: true};
        },
        canExecute: () => true,
    };
    const c = new Coordinator(j, transport, supervisor, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    assert(log.indexOf("stop") < log.indexOf("/runner/claims"));
    assert(log.indexOf("/runner/leases") < log.indexOf("/runner/claims"));
    assert(log.includes("/runner/stop-evidence"));
    j.close();
});
test("unconfirmed process stop blocks new claims", async () => {
    const j = new Journal(root());
    const c = new Coordinator(
        j,
        {journal: j, request: async () => ({leases: []})} as any,
        {
            inspect: async () => [{attempt_id: "unknown"}],
            stop: async () => ({confirmed: false}),
            canExecute: () => true,
        } as any,
        "runner",
        {assertRuntime: () => {}},
    );
    await assert.rejects(() => c.recover(), /stop/);
    await assert.rejects(() => c.claim(), /reconcile/);
    j.close();
});
test("operation receipt and effect fence survive restart", async () => {
    const dir = root();
    let j = new Journal(dir);
    const transport: any = {
        mutate: async () => ({
            operation: {operation_id: "op", operation_hash: "h", version: 2, status: "started"},
        }),
    };
    let b = new OperationBoundary(j, transport, () => ({
        job_id: "j",
        attempt_id: "a",
        lease_epoch: 1,
    }));
    await b.consume(
        {job_id: "j", attempt_id: "a", lease_epoch: 1, job_version: 1},
        {operation_id: "op", operation_hash: "h", version: 1, nonce: "n"},
    );
    b.beginEffect("op");
    j.close();
    j = new Journal(dir);
    b = new OperationBoundary(j, transport, () => ({job_id: "j", attempt_id: "a", lease_epoch: 1}));
    assert.throws(() => b.beginEffect("op"), /uncertain|started/);
    j.close();
});
test("execute sends the consume-shaped identity to the new route and returns its operation", async () => {
    const requests: any[] = [];
    const transport: any = {
        mutate: async (_kind: string, id: string, route: string, request: any) => {
            requests.push({id, route, request});
            return {
                operation: {
                    operation_id: request.operation_id,
                    status: "succeeded",
                    server_receipt: {tool: "team.find", outcome: "succeeded", summary: "", objects: {}, error: null},
                },
            };
        },
    };
    const b = new OperationBoundary(new Journal(root()), transport, () => ({
        job_id: "j",
        attempt_id: "a",
        lease_epoch: 1,
        job_version: 3,
    }));
    const operation = await b.execute(
        {job_id: "j", attempt_id: "a", lease_epoch: 1, job_version: 3},
        {operation_id: "op1", operation_hash: "h1", version: 1, nonce: "n1"},
    );
    assert.equal(requests[0].route, "/runner/operations/execute");
    assert.equal(requests[0].id, "execute:op1");
    assert.deepEqual(requests[0].request, {
        job_id: "j",
        attempt_id: "a",
        lease_epoch: 1,
        job_version: 3,
        operation_id: "op1",
        expected_version: 1,
        operation_hash: "h1",
        nonce: "n1",
    });
    assert.equal(operation.status, "succeeded");
    assert.equal(operation.server_receipt.tool, "team.find");
});
test("input effect is fenced before application and never replayed after uncertainty", async () => {
    const j = new Journal(root()),
        d = descriptor();
    let calls = 0;
    const supervisor: any = {
        inspect: async () => [],
        canExecute: () => true,
        start: async () => {},
        stop: async () => ({confirmed: true}),
        applyInput: async () => {
            calls++;
            throw new Error("lost runtime reply");
        },
    };
    const c = new Coordinator(
        j,
        {
            request: async () => ({leases: []}),
            mutate: async (k: string) =>
                k === "claim" ? {attempt: d, job_version: 1} : {receipt: {job_version: 2}},
        } as any,
        supervisor,
        d.runner_id,
        {assertRuntime: () => {}},
    );
    const input = {
        ...fixtures.valid.find((c: any) => c.schema === "input").payload,
        delivery_state: "delivered",
    };
    await c.recover();
    await c.claim();
    await assert.rejects(() => c.applyInput(d, input));
    await assert.rejects(() => c.applyInput(d, input), /uncertain/);
    assert.equal(calls, 1);
    await c.stopActive();
    j.close();
});
test("owner runtime approval is required before launch", async () => {
    const d = descriptor(),
        j = new Journal(root());
    let starts = 0;
    const t: any = {
        request: async () => ({leases: []}),
        mutate: async () => ({attempt: d, job_version: 1}),
    };
    const s: any = {
        inspect: async () => [],
        canExecute: () => true,
        start: async () => {
            starts++;
        },
    };
    const c = new Coordinator(j, t, s, d.runner_id, {
        assertRuntime: () => {
            throw new Error("Unapproved workspace");
        },
    });
    await c.recover();
    await assert.rejects(() => c.claim(), /Unapproved/);
    assert.equal(starts, 0);
    j.close();
});
test("expired lease stops a process without waiting for the next poll", async () => {
    const d = descriptor();
    d.lease_expires_at = new Date(Date.now() + 90).toISOString();
    d.descriptor_digest = digest(
        Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
    );
    const j = new Journal(root());
    let stops = 0;
    const t: any = {
        request: async () => ({leases: []}),
        mutate: async (k: any) =>
            k === "claim" ? {attempt: d, job_version: 1} : {receipt: {job_version: 2}},
    };
    const s: any = {
        inspect: async () => [],
        canExecute: () => true,
        start: async () => {},
        stop: async () => {
            stops++;
            return {confirmed: true};
        },
    };
    const c = new Coordinator(j, t, s, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    await new Promise((r) => setTimeout(r, 130));
    assert.equal(stops, 1);
    j.close();
});
test("setup() reaches probe for an unapproved runtime instead of failing before it runs", async () => {
    const d = probeDescriptor(),
        j = new Journal(root());
    const claimed = {
        descriptor: d,
        claim_key: "claim-key",
        lease_epoch: 1,
        lease_expires_at: new Date(Date.now() + 60000).toISOString(),
    };
    const posted: any[] = [];
    let assertRuntimeCalls = 0;
    const t: any = {
        request: async (route: string, body: any) => {
            if (route === "/runner/leases") return {leases: []};
            if (route === "/runner/setup-authority")
                return {...body, grant_id: d.grant.id, expires_at: d.grant.expires_at};
            return {};
        },
        mutate: async (kind: string, _id: string, _route: string, body: any) => {
            if (kind === "setup_claim") return claimed;
            if (kind === "setup_result") posted.push(body);
            return {};
        },
    };
    const s: any = {
        inspect: async () => [],
        canExecute: () => true,
        probe: async () => ({
            state: "needs_action",
            capabilities: {},
            requirements: [
                {code: "auth_required", surface: "adapter", action: "login_vendor", diagnostic_id: null},
            ],
        }),
    };
    const c = new Coordinator(j, t, s, d.runner_id, {
        assertRuntime: () => {
            assertRuntimeCalls++;
            throw new Error("Unapproved workspace");
        },
    });
    await c.recover();
    await c.setup("setup-1");
    // The requirement never reached probe() in the old code: Coordinator.setup()
    // threw registry.assertRuntime's error first, and nothing was ever posted.
    assert.equal(assertRuntimeCalls, 0);
    assert.equal(posted.length, 1);
    assert.equal(posted[0].state, "needs_action");
    assert.equal(posted[0].requirements[0].code, "auth_required");
    j.close();
});
test("setup() reports a thrown probe error as a failed result instead of dropping it", async () => {
    const d = probeDescriptor(),
        j = new Journal(root());
    const claimed = {
        descriptor: d,
        claim_key: "claim-key",
        lease_epoch: 1,
        lease_expires_at: new Date(Date.now() + 60000).toISOString(),
    };
    const posted: any[] = [];
    const t: any = {
        request: async (route: string, body: any) => {
            if (route === "/runner/leases") return {leases: []};
            if (route === "/runner/setup-authority")
                return {...body, grant_id: d.grant.id, expires_at: d.grant.expires_at};
            return {};
        },
        mutate: async (kind: string, _id: string, _route: string, body: any) => {
            if (kind === "setup_claim") return claimed;
            if (kind === "setup_result") posted.push(body);
            return {};
        },
    };
    const s: any = {
        inspect: async () => [],
        canExecute: () => true,
        probe: async () => {
            throw new Error("boom");
        },
    };
    const c = new Coordinator(j, t, s, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.setup("setup-1");
    assert.equal(posted.length, 1);
    assert.equal(posted[0].state, "failed");
    j.close();
});
