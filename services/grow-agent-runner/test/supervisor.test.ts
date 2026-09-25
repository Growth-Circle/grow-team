import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, mkdirSync, writeFileSync, readFileSync} from "node:fs";
import {execFileSync} from "node:child_process";
import {randomUUID} from "node:crypto";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Journal} from "../dist/journal.js";
import {Coordinator, OperationBoundary} from "../dist/supervisor.js";
import {TransportError, Transport} from "../dist/transport.js";
import {digest, effectiveConfiguration} from "../dist/protocol.js";
import {PrivateStore} from "../dist/config.js";
import {RuntimeSupervisor} from "../dist/runtime-supervisor.js";
import {AnthropicRuntime} from "../dist/anthropic-runtime.js";
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
                    server_receipt: {
                        tool: "team.find",
                        outcome: "succeeded",
                        summary: "",
                        objects: {},
                        error: null,
                    },
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
                {
                    code: "auth_required",
                    surface: "adapter",
                    action: "login_vendor",
                    diagnostic_id: null,
                },
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
test("newEvent only reads event and stop kinds, so a same-shaped entry of another kind cannot inflate the sequence", async () => {
    const d = descriptor(),
        j = new Journal(root());
    // A "claim" entry that happens to carry an event-shaped field with a high
    // sequence number. Scanning every kind (the old newEvent()) would pick
    // this up too; scanning only "event" and "stop" (internals/docs/spec/
    // 2026-09-24-agent-fast-lane.md §2.3) must not.
    j.prepare("claim", "claim:bogus", "local", {
        event: {attempt_id: d.attempt_id, lease_epoch: d.lease_epoch, sequence: 999},
    });
    j.complete("claim:bogus", {}); // Done, so recover()'s own claim replay ignores it.
    let eventBody: any;
    const t: any = {
        request: async (route: string) =>
            route === "/runner/controls"
                ? {
                      controls: [
                          {
                              attempt_id: d.attempt_id,
                              lease_epoch: d.lease_epoch,
                              control: "continue",
                              job_version: 1,
                          },
                      ],
                  }
                : {leases: []},
        mutate: async (kind: string, _id: string, _route: string, body: any) => {
            if (kind === "claim") return {attempt: d, job_version: 1};
            if (kind === "event") {
                eventBody = body;
                return {receipts: [{job_version: 1}]};
            }
            return {receipt: {job_version: 1}};
        },
    };
    const s: any = {inspect: async () => [], canExecute: () => true, start: async () => {}};
    const c = new Coordinator(j, t, s, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    await c.event("attempt.started", {
        process_state: "active",
        adapter_session_ref: null,
        stop_confirmed: false,
        summary: "",
    });
    assert.equal(eventBody.events[0].sequence, 1);
    j.close();
});
test("a claim left open after a crash is retried with its own key, not a fresh one", async () => {
    const d = descriptor(),
        j = new Journal(root());
    const seeded = j.prepare("claim", "claim:retry-me", "local", {
        schema_version: 1,
        claim_key: "retry-me",
        capacity: 1,
        runner_version: "0.1.0",
    });
    assert.equal(seeded.state, "prepared");
    const seenIds: string[] = [];
    const t: any = {
        request: async () => ({leases: []}),
        // recover() replays any still-open claim before claim() runs; a no-op here
        // leaves the seeded entry "prepared" so claimOnce() is the one under test.
        send: async () => ({}),
        mutate: async (kind: string, id: string, _route: string, body: any) => {
            if (kind === "claim") {
                seenIds.push(id);
                assert.equal(body.claim_key, "retry-me");
                return {attempt: null};
            }
            return {receipt: {job_version: 1}};
        },
    };
    const s: any = {inspect: async () => [], canExecute: () => true, start: async () => {}};
    const c = new Coordinator(j, t, s, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    assert.deepEqual(seenIds, ["claim:retry-me"]);
    j.close();
});
test("recover() resends a claim left open from before a restart", async () => {
    const j = new Journal(root());
    j.prepare("claim", "claim:leftover", "local", {
        schema_version: 1,
        claim_key: "leftover",
        capacity: 1,
        runner_version: "0.1.0",
    });
    const sent: string[] = [];
    const t: any = {
        request: async () => ({leases: []}),
        send: async (entry: any) => {
            sent.push(entry.id);
            return {receipts: []};
        },
    };
    const s: any = {
        inspect: async () => [],
        stop: async () => ({confirmed: true}),
        canExecute: () => true,
    };
    const c = new Coordinator(j, t, s, "runner", {assertRuntime: () => {}});
    await c.recover();
    assert.deepEqual(sent, ["claim:leftover"]);
    j.close();
});
test("requests to /runner/drafts and /runner/authority skip the extra controls check", async () => {
    const d = descriptor(),
        j = new Journal(root());
    let controlsCalls = 0;
    let channel: any;
    const t: any = {
        request: async (route: string) => {
            if (route === "/runner/controls") {
                controlsCalls++;
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
            }
            if (route === "/runner/leases") return {leases: []};
            if (route === "/runner/drafts") return {ok: true};
            if (route === "/runner/authority")
                return {job_id: d.job_id, attempt_id: d.attempt_id, lease_epoch: d.lease_epoch};
            return {};
        },
        mutate: async (kind: string) =>
            kind === "claim" ? {attempt: d, job_version: 1} : {receipt: {job_version: 1}},
    };
    const s: any = {
        inspect: async () => [],
        canExecute: () => true,
        start: async (_d: any, ch: any) => {
            channel = ch;
        },
    };
    const c = new Coordinator(j, t, s, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    await channel.request("/runner/drafts", {text: "hi"});
    await channel.request("/runner/authority");
    assert.equal(controlsCalls, 0);
    await channel.request("/runner/context", {reference_ids: []});
    assert.equal(controlsCalls, 1);
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
test("a stale-version rejection on /runner/authority retries once after a controls refresh", async () => {
    const d = descriptor(),
        j = new Journal(root());
    let controlsCalls = 0;
    let authorityCalls = 0;
    let channel: any;
    const t: any = {
        request: async (route: string, body: any) => {
            if (route === "/runner/controls") {
                controlsCalls++;
                return {
                    controls: [
                        {
                            attempt_id: d.attempt_id,
                            lease_epoch: d.lease_epoch,
                            control: "continue",
                            job_version: 9,
                        },
                    ],
                };
            }
            if (route === "/runner/leases") return {leases: []};
            if (route === "/runner/authority") {
                authorityCalls++;
                // The first call finds the server has already moved the job
                // version ahead (a later add_input, a waiting_for_approval
                // transition, ...) - exactly what the old code never refreshed
                // for. Only the retry, after refreshVersion(), succeeds.
                if (authorityCalls === 1) throw new TransportError("policy");
                return {...body};
            }
            return {};
        },
        mutate: async (kind: string) =>
            kind === "claim" ? {attempt: d, job_version: 1} : {receipt: {job_version: 1}},
    };
    const s: any = {
        inspect: async () => [],
        canExecute: () => true,
        start: async (_d: any, ch: any) => {
            channel = ch;
        },
    };
    const c = new Coordinator(j, t, s, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    const response = await channel.request("/runner/authority");
    assert.equal(response.attempt_id, d.attempt_id);
    assert.equal(authorityCalls, 2);
    // Exactly one refresh, not one per authority request: the retry reuses
    // the version the refresh just fetched instead of polling again.
    assert.equal(controlsCalls, 1);
    j.close();
});
// A non-policy failure (e.g. a transient network error) must propagate as-is;
// only a stale-version rejection on /runner/authority gets the retry above.
test("a non-policy /runner/authority failure is not retried", async () => {
    const d = descriptor(),
        j = new Journal(root());
    let authorityCalls = 0;
    let channel: any;
    const t: any = {
        request: async (route: string) => {
            if (route === "/runner/leases") return {leases: []};
            if (route === "/runner/authority") {
                authorityCalls++;
                throw new TransportError("transient");
            }
            return {controls: []};
        },
        mutate: async (kind: string) =>
            kind === "claim" ? {attempt: d, job_version: 1} : {receipt: {job_version: 1}},
    };
    const s: any = {
        inspect: async () => [],
        canExecute: () => true,
        start: async (_d: any, ch: any) => {
            channel = ch;
        },
    };
    const c = new Coordinator(j, t, s, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    await assert.rejects(() => channel.request("/runner/authority"), TransportError);
    assert.equal(authorityCalls, 1);
    j.close();
});
const gitFixture = (cwd: string, ...args: string[]) =>
    execFileSync("/usr/bin/git", args, {
        cwd,
        encoding: "utf8",
        env: {
            PATH: "/usr/bin:/bin",
            HOME: "/nonexistent",
            GIT_CONFIG_NOSYSTEM: "1",
            GIT_CONFIG_GLOBAL: "/dev/null",
        },
    }).trim();
for (const withRepository of [false, true]) {
    const git = gitFixture;
    test(`fast-lane stopScope, repository=${withRepository}`, async () => {
        const d = descriptor();
        d.adapter = {...d.adapter, mode: "endpoint", version: "0.1.0"};
        d.job_kind = "answer";
        d.delivery_target = "answer";
        d.context_refs = [];
        d.checkpoint = null;
        // An answer job's policy allows only these two actions (protocol.ts
        // "Answer mutation"); fixture #0 also has repository.edit/checks.run.
        d.policy = {...d.policy, actions: ["context.read", "repository.read"]};
        d.provider = {...d.provider, credential_ref: null};
        d.budget = {...d.budget, active_seconds: 3600};
        let source = "";
        if (withRepository) {
            const gitRoot = mkdtempSync(join(tmpdir(), "grow-fastlane-repo-"));
            source = join(gitRoot, "source");
            mkdirSync(source);
            git(source, "init", "-q", "-b", "main");
            git(source, "config", "user.email", "fixture@invalid");
            git(source, "config", "user.name", "Fixture");
            writeFileSync(join(source, "a.txt"), "hello\n");
            git(source, "add", ".");
            git(source, "commit", "-qm", "base");
            const base = git(source, "rev-parse", "HEAD");
            d.repository = {...d.repository, base_ref: "main", base_commit: base};
            d.provider.data_scope = ["selected_chat", "selected_repository"];
        } else {
            d.repository = null;
            d.provider.data_scope = ["selected_chat"];
        }
        // narrow() (protocol.ts) requires tested_configuration to match the
        // top-level fields changed above (adapter, provider, policy, ...);
        // rebuild it from them, the same way a real claim's own tested_
        // configuration would have been captured, then re-hash both digests.
        d.tested_configuration = effectiveConfiguration(d);
        d.configuration_digest = digest(d.tested_configuration);
        d.descriptor_digest = digest(
            Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
        );
        const saved = {
            startSession: AnthropicRuntime.prototype.startSession,
            sendTurn: AnthropicRuntime.prototype.sendTurn,
            cancel: AnthropicRuntime.prototype.cancel,
            close: AnthropicRuntime.prototype.close,
            resume: AnthropicRuntime.prototype.resume,
        };
        AnthropicRuntime.prototype.startSession = async () => {};
        AnthropicRuntime.prototype.sendTurn = async () => "done";
        AnthropicRuntime.prototype.cancel = async () => {};
        AnthropicRuntime.prototype.close = async () => {};
        AnthropicRuntime.prototype.resume = async () => false;
        let stops = 0;
        const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-fastlane-store-")));
        const journal = new Journal(join(store.root, "journal"));
        const registry: any = {
            assertRuntime: () => {},
            assertWorkspace: () => source,
            read: () => ({
                catalog_reported: true,
                catalog: {adapters: [{auth_state: "ready", capabilities: {chat_ready: true}}]},
            }),
        };
        const sandbox: any = {
            inspect: async () => [],
            stopScope: async () => {
                stops++;
                return {confirmed: true};
            },
        };
        const supervisor = new (RuntimeSupervisor as any)(store, journal, registry, sandbox, {
            owner_approved: true,
            fast_lane: true,
        });
        let version = 1;
        const transport = new Transport("http://localhost", journal, () => "synthetic-fixture");
        transport.request = (async (route: string, body: any) => {
            if (route === "/runner/leases") return {leases: []};
            if (route === "/runner/claims") return {attempt: d, job_version: version};
            if (route === "/runner/controls")
                return {
                    controls: [
                        {
                            attempt_id: d.attempt_id,
                            lease_epoch: d.lease_epoch,
                            control: "continue",
                            job_version: version,
                        },
                    ],
                };
            if (route === "/runner/inputs") return {inputs: []};
            if (route === "/runner/authority") return {...body};
            if (route === "/runner/heartbeat")
                return {
                    leases: [
                        {
                            attempt_id: d.attempt_id,
                            lease_epoch: d.lease_epoch,
                            control: "continue",
                            job_version: version,
                            lease_expires_at: d.lease_expires_at,
                        },
                    ],
                };
            if (route === "/runner/events") return {receipts: [{job_version: ++version}]};
            if (route === "/runner/stop-evidence") return {receipt: {job_version: ++version}};
            throw new Error(`Unexpected route ${route}`);
        }) as any;
        transport.binary = (async (_route: string, body: any, bytes?: Buffer) => ({
            artifact_id: randomUUID(),
            checksum: body.checksum,
            size: bytes!.length,
        })) as any;
        const coordinator = new Coordinator(journal, transport, supervisor, d.runner_id, registry);
        try {
            await coordinator.recover();
            await coordinator.claim();
            // FL-20 (runtime-supervisor.ts): the attempt stops itself right
            // after result.prepared, without waiting for a controls tick this
            // test never sends - so its own Active entry disappears on its own.
            for (let i = 0; i < 200 && (supervisor as any).active.size > 0; i++)
                await new Promise((resolve) => setTimeout(resolve, 10));
            assert.equal(
                (supervisor as any).active.size,
                0,
                "Active entry leaked after the attempt finished",
            );
            assert.equal(stops, withRepository ? 1 : 0);
        } finally {
            await coordinator.stopActive();
            Object.assign(AnthropicRuntime.prototype, saved);
            journal.close();
        }
    });
}
// §9 "fast_lane per provider": a present-but-malformed allowlist must fail
// closed at startup, not read as Array.isArray(...) === false and silently
// match every provider.
for (const field of ["fast_lane_providers", "fast_lane_bearer_providers"]) {
    test(`open() rejects a non-array ${field}`, async () => {
        const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-open-validate-")));
        store.write("runtime.json", {
            owner_approved: true,
            model_image: "sha256:" + "a".repeat(64),
            [field]: "not-an-array",
        });
        const journal = new Journal(join(store.root, "journal"));
        try {
            await assert.rejects(
                () => RuntimeSupervisor.open(store, journal, {} as any),
                new RegExp(field),
            );
        } finally {
            journal.close();
        }
    });
}
