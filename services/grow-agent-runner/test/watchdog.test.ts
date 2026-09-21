import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, writeFileSync, existsSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore} from "../dist/config.js";
import {processIdentity, monotonic} from "../dist/containment.js";
import {watchdogTick} from "../dist/watchdog.js";

const evidenceRoot = mkdtempSync(join(tmpdir(), "grow-watchdog-fix1-"));
console.log("WATCHDOG_RACE_EVIDENCE", evidenceRoot);
function fixture(initial: string | null, publish: string | null, point: string) {
    const root = mkdtempSync(join(evidenceRoot, "case-")),
        store = new PrivateStore(join(root, "state"));
    const id = "a".repeat(64),
        installation = "isolated-watchdog-fixture";
    const valid = {
        id,
        installation,
        scope: "authorized-attempt",
        kind: "attempt",
        owner: processIdentity(process.pid),
        deadline: monotonic() + 60000,
        heartbeat: monotonic() + 60000,
        processes: [],
        cgroups: [],
        state: "running",
    };
    const variants = {
        valid,
        expired: {...valid, deadline: 0},
        stale: {...valid, heartbeat: 0},
        revoked: {...valid, state: "revoked"},
        dead: {...valid, owner: {pid: 0, start: "0"}},
        foreign: {...valid, scope: "other-attempt"},
    };
    if (initial) store.write(`container-${id}.json`, variants[initial]);
    if (publish) writeFileSync(join(root, "publish.json"), JSON.stringify(variants[publish]));
    const binary = join(root, "fake-docker.mjs");
    writeFileSync(
        binary,
        `#!${process.execPath}
import fs from 'node:fs';
const root=${JSON.stringify(root)},id=${JSON.stringify(id)},installation=${JSON.stringify(installation)},point=${JSON.stringify(point)};
const [command,...args]=process.argv.slice(4);fs.appendFileSync(root+'/calls.jsonl',JSON.stringify({command,args})+'\\n');
let count=fs.existsSync(root+'/inspect-count')?Number(fs.readFileSync(root+'/inspect-count')):0;
if(command==='inspect'){count++;fs.writeFileSync(root+'/inspect-count',String(count));}
if((command==='ps'&&point==='ps')||(command==='inspect'&&point==='inspect-'+count))fs.writeFileSync(root+'/state/container-'+id+'.json',fs.readFileSync(root+'/publish.json'),{mode:0o600});
if(command==='ps')console.log(id);
else if(command==='inspect')console.log(JSON.stringify([{Id:id,Config:{Labels:{'digital.cadis.grow.installation':installation,'digital.cadis.grow.scope':'authorized-attempt','digital.cadis.grow.kind':'attempt'}},State:{Running:!fs.existsSync(root+'/killed'),Paused:false,Pid:0}}]));
else if(command==='top')console.log('PID');
else if(command==='kill')fs.writeFileSync(root+'/killed','issued');
else throw Error(command);
`,
        {mode: 0o700},
    );
    return {
        root,
        store,
        id,
        valid,
        installation: {
            id: installation,
            endpoint: "unix:///isolated-fixture",
            docker: binary,
            images: [],
        },
    };
}
for (const [name, initial, point] of [
    ["launch during discovery", null, "ps"],
    ["renewal during first inspection", "stale", "inspect-1"],
    ["renewal during stop inspection", "stale", "inspect-3"],
] as const) {
    test(`watchdog preserves valid ${name}`, async () => {
        const f = fixture(initial, "valid", point);
        await watchdogTick(f.store, f.installation);
        assert.equal(
            existsSync(join(f.root, "killed")),
            false,
            "valid current authority must not receive a kill",
        );
        assert.deepEqual(f.store.read(`container-${f.id}.json`), f.valid);
    });
}
for (const state of ["revoked", "expired"]) {
    test(`watchdog observes current ${state} authority after inspection`, async () => {
        const f = fixture("valid", state, "inspect-1");
        await watchdogTick(f.store, f.installation);
        assert(existsSync(join(f.root, "killed")));
        const authority = f.store.read(`container-${f.id}.json`);
        assert.equal(authority.state, state === "revoked" ? "revoked" : "running");
        assert.equal(f.store.read(`watchdog-stop-${f.id}.json`).state, "stopped");
    });
}
for (const state of [null, "dead"]) {
    test(`watchdog stops ${state ?? "unjournaled orphan"} without overwriting supervisor records`, async () => {
        const f = fixture(state, null, "");
        const before = f.store.read(`container-${f.id}.json`);
        await watchdogTick(f.store, f.installation);
        assert(existsSync(join(f.root, "killed")));
        assert.deepEqual(f.store.read(`container-${f.id}.json`), before);
        const receipt = f.store.read(`watchdog-stop-${f.id}.json`);
        assert.equal(receipt.state, "stopped");
        assert.equal(receipt.id, f.id);
    });
}
test("watchdog rejects a re-read record with another immutable scope", async () => {
    const f = fixture("valid", "foreign", "inspect-1");
    await assert.rejects(() => watchdogTick(f.store, f.installation), /identity|scope/);
    assert.equal(existsSync(join(f.root, "killed")), false);
});
