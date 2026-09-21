import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Journal} from "../dist/journal.js";
import {Coordinator} from "../dist/supervisor.js";
import {digest} from "../dist/protocol.js";
const fixture = JSON.parse(
    readFileSync(
        new URL("../../../zerver/tests/fixtures/agents/protocol-v1.json", import.meta.url),
        "utf8",
    ),
).valid[0].payload;
function descriptor() {
    const d = structuredClone(fixture);
    d.lease_expires_at = new Date(Date.now() + 60000).toISOString();
    d.configuration_digest = digest(d.tested_configuration);
    d.descriptor_digest = digest(
        Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
    );
    return d;
}
test("claim response lost before restart cannot launch the stopped server attempt", async () => {
    const dir = mkdtempSync(join(tmpdir(), "grow-recovery-test-"));
    let j = new Journal(dir);
    j.prepare("claim", "claim:old", "/runner/claims", {claim_key: "old"});
    j.uncertain("claim:old");
    j.close();
    j = new Journal(dir);
    const d = descriptor(),
        calls: string[] = [],
        transport: any = {
            send: async (e: any) => {
                calls.push("claim-replay");
                j.complete(e.id, {attempt: d, job_version: 1});
                return {attempt: d, job_version: 1};
            },
            request: async () => ({leases: [{descriptor: d, event_cursor: 7}]}),
            mutate: async (k: any, id: any, route: any, p: any) => {
                calls.push(route);
                if (k === "stop") {
                    assert.equal(p.event.sequence, 8);
                    assert.equal(p.event.payload.summary, "");
                } else assert.notEqual(p.claim_key, "old");
                return {attempt: null};
            },
        };
    const supervisor: any = {
        inspect: async () => [],
        stop: async () => ({confirmed: true}),
        canExecute: () => true,
    };
    const c = new Coordinator(j, transport, supervisor, d.runner_id, {assertRuntime: () => {}});
    await c.recover();
    await c.claim();
    assert.equal(calls[0], "claim-replay");
    assert(calls.includes("/runner/stop-evidence"));
    j.close();
});
test("schema snapshot is identical to server export", () => {
    const shipped = readFileSync(
        new URL("../protocol/protocol-v1.schema.json", import.meta.url),
        "utf8",
    );
    const source = readFileSync(
        new URL("../../../zerver/tests/fixtures/agents/protocol-v1.schema.json", import.meta.url),
        "utf8",
    );
    assert.equal(shipped, source);
});
