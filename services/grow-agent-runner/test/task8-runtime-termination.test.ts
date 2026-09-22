import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore} from "../dist/config.js";
import {Journal} from "../dist/journal.js";
import {effectiveConfiguration, digest} from "../dist/protocol.js";
import {RuntimeSupervisor} from "../dist/runtime-supervisor.js";
import {ContainedEndpointRuntime} from "../dist/contained-endpoint.js";
import {Coordinator} from "../dist/supervisor.js";

const fixtures = JSON.parse(
    readFileSync(
        new URL("../../../zerver/tests/fixtures/agents/protocol-v1.json", import.meta.url),
        "utf8",
    ),
);

function descriptor() {
    const item = structuredClone(fixtures.valid[0].payload);
    item.adapter.mode = "endpoint";
    item.adapter.version = "0.1.0";
    item.repository = null;
    item.job_kind = "answer";
    item.delivery_target = "answer";
    item.context_refs = [];
    item.policy.actions = ["context.read"];
    item.provider.credential_ref = null;
    item.provider.data_scope = ["selected_chat"];
    item.lease_expires_at = new Date(Date.now() + 60000).toISOString();
    item.tested_configuration = effectiveConfiguration(item);
    item.configuration_digest = digest(item.tested_configuration);
    item.descriptor_digest = digest(
        Object.fromEntries(Object.entries(item).filter(([key]) => key !== "descriptor_digest")),
    );
    return item;
}

test("confirmed Task8 extension failure retires the coordinator and reports a stopped attempt", async () => {
    const d = descriptor();
    const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-task8-termination-")));
    store.write("connection.json", {
        state: "connected",
        origin: "https://control.example",
        runner_id: d.runner_id,
    });
    const journal = new Journal(store.root);
    const saved = {
        startSession: ContainedEndpointRuntime.prototype.startSession,
        close: ContainedEndpointRuntime.prototype.close,
    };
    let stops = 0,
        activeHeartbeats = 0;
    ContainedEndpointRuntime.prototype.startSession = async () => {};
    ContainedEndpointRuntime.prototype.close = async () => {};
    const runtime = new (RuntimeSupervisor as any)(
        store,
        journal,
        {
            assertRuntime: () => {},
            read: () => ({
                catalog_reported: true,
                catalog: {adapters: [{auth_state: "ready", capabilities: {chat_ready: true}}]},
            }),
        },
        {
            inspect: async () => [],
            stopScope: async () => {
                stops++;
                if (stops > 1) throw new Error("second scope stop must not run");
                return {confirmed: true};
            },
        },
        {owner_approved: true, model_image: `sha256:${"a".repeat(64)}`},
        {context: async () => Promise.reject(new Error("fixture context failure"))},
    );
    const events: any[] = [],
        stopsReported: any[] = [];
    let loseStopAcknowledgement = true;
    let version = 1;
    const transport: any = {
        currentScope: () => `runner:${d.runner_id}`,
        request: async (route: string, body?: any) => {
            if (route === "/runner/leases") return {leases: []};
            if (route === "/runner/controls")
                return {
                    controls: [
                        {
                            attempt_id: d.attempt_id,
                            lease_epoch: d.lease_epoch,
                            job_version: version,
                            control: "continue",
                            approvals: [],
                        },
                    ],
                };
            if (route === "/runner/authority") return body;
            if (route === "/runner/inputs") return {inputs: []};
            if (route === "/runner/heartbeat") {
                if (body?.leases?.length) activeHeartbeats++;
                return {leases: []};
            }
            throw new Error(`Unexpected request ${route}`);
        },
        mutate: async (kind: string, _id: string, route: string, body: any) => {
            if (kind === "claim") return {attempt: d, job_version: version};
            if (route === "/runner/events") {
                events.push(body.events[0]);
                return {receipts: [{job_version: ++version}]};
            }
            if (route === "/runner/stop-evidence") {
                stopsReported.push(body.event);
                if (loseStopAcknowledgement) {
                    loseStopAcknowledgement = false;
                    journal
                        .partition(`runner:${d.runner_id}`)
                        .uncertain(`stop:${d.attempt_id}:${d.lease_epoch}`);
                    throw new Error("fixture stop acknowledgement lost");
                }
                return {receipt: {job_version: ++version}};
            }
            throw new Error(`Unexpected mutation ${route}`);
        },
        send: async (entry: any) => {
            journal.partition(`runner:${d.runner_id}`).complete(entry.id, {
                receipt: {job_version: ++version},
            });
            return {receipts: [{job_version: version}]};
        },
    };
    const coordinator = new Coordinator(journal, transport, runtime, d.runner_id, {
        assertRuntime: () => {},
    });
    try {
        await coordinator.recover();
        await coordinator.claim();
        await (runtime as any).active.get(d.attempt_id).task;
        await new Promise<void>((resolve) => setImmediate(resolve));
        assert.equal(stops, 1);
        assert.equal((coordinator as any).active, null);
        assert.equal(activeHeartbeats, 0);
        assert.equal(stopsReported.length, 1);
        assert.equal(stopsReported[0].type, "attempt.stopped");
        assert.equal(stopsReported[0].payload.stop_confirmed, true);
        assert.equal(
            events.some((event) => event.type === "attempt.interrupted"),
            false,
        );
        await assert.rejects(() => coordinator.claim(), /owner recovery/);
        assert.equal(
            journal.partition(`runner:${d.runner_id}`).list("stop")[0]?.state,
            "uncertain",
        );
        await coordinator.recover();
        assert.equal(journal.partition(`runner:${d.runner_id}`).list("stop")[0]?.state, "done");
    } finally {
        Object.assign(ContainedEndpointRuntime.prototype, saved);
        journal.close();
    }
});
