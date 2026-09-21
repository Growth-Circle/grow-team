import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, writeFileSync, readFileSync, existsSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore} from "../dist/config.js";
import {processIdentity, monotonic} from "../dist/containment.js";
import {watchdogTick} from "../dist/watchdog.js";
const evidenceRoot = mkdtempSync(join(tmpdir(), "grow-paused-stop-fix2-"));
console.log("PAUSED_STOP_EVIDENCE", evidenceRoot);
function fixture(renewBeforeStop: boolean, denyKill = false) {
    const root = mkdtempSync(join(evidenceRoot, "case-")),
        store = new PrivateStore(join(root, "state"));
    const id = "c".repeat(64),
        installation = "isolated-paused-stop";
    const valid = {
        id,
        installation,
        scope: "snapshot-export",
        kind: "attempt",
        owner: processIdentity(process.pid),
        deadline: monotonic() + 60000,
        heartbeat: monotonic() + 60000,
        processes: [],
        cgroups: [],
        state: "running",
    };
    store.write(`container-${id}.json`, {...valid, heartbeat: 0});
    writeFileSync(join(root, "renewed.json"), JSON.stringify(valid));
    writeFileSync(
        join(root, "engine-state.json"),
        JSON.stringify({Running: true, Paused: true, Pid: 12345}),
    );
    const binary = join(root, "fake-docker.mjs");
    writeFileSync(
        binary,
        `#!${process.execPath}
import fs from 'node:fs';
const root=${JSON.stringify(root)},id=${JSON.stringify(id)},installation=${JSON.stringify(installation)},renewBeforeStop=${renewBeforeStop},denyKill=${denyKill};
const [command,...args]=process.argv.slice(4);fs.appendFileSync(root+'/calls.jsonl',JSON.stringify({command,args})+'\\n');
const state=()=>JSON.parse(fs.readFileSync(root+'/engine-state.json'));
let count=fs.existsSync(root+'/inspect-count')?Number(fs.readFileSync(root+'/inspect-count')):0;
if(command==='inspect'){fs.writeFileSync(root+'/inspect-count',String(++count));if(renewBeforeStop&&count===3)fs.writeFileSync(root+'/state/container-'+id+'.json',fs.readFileSync(root+'/renewed.json'),{mode:0o600});}
if(command==='ps')console.log(id);
else if(command==='inspect')console.log(JSON.stringify([{Id:id,Config:{Labels:{'digital.cadis.grow.installation':installation,'digital.cadis.grow.scope':'snapshot-export','digital.cadis.grow.kind':'attempt'}},State:state()}]));
else if(command==='top')console.log('PID');
else if(command==='unpause'){fs.writeFileSync(root+'/engine-state.json',JSON.stringify({...state(),Paused:false}));fs.writeFileSync(root+'/state/container-'+id+'.json',fs.readFileSync(root+'/renewed.json'),{mode:0o600});}
else if(command==='kill'){if(denyKill)process.exit(1);fs.writeFileSync(root+'/engine-state.json',JSON.stringify({Running:false,Paused:false,Pid:0}));}
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
        state: () => JSON.parse(readFileSync(join(root, "engine-state.json"), "utf8")),
        calls: () => readFileSync(join(root, "calls.jsonl"), "utf8"),
    };
}
test("renewed authority preserves continuous pause before a conditional stop", async () => {
    const f = fixture(true);
    await watchdogTick(f.store, f.installation);
    assert.deepEqual(f.state(), {Running: true, Paused: true, Pid: 12345});
    assert(!f.calls().includes('"command":"unpause"'));
    assert(!f.calls().includes('"command":"kill"'));
    assert.deepEqual(f.store.read(`container-${f.id}.json`), f.valid);
    assert.equal(existsSync(join(f.store.root, `watchdog-stop-${f.id}.json`)), false);
});
test("expired paused authority is killed directly without an abortable unpause", async () => {
    const f = fixture(false);
    await watchdogTick(f.store, f.installation);
    assert(
        !f.calls().includes('"command":"unpause"'),
        "snapshot must never be explicitly unpaused",
    );
    assert(f.calls().includes('"command":"kill"'));
    assert.deepEqual(f.state(), {Running: false, Paused: false, Pid: 0});
    assert.equal(f.store.read(`watchdog-stop-${f.id}.json`).state, "stopped");
});
test("failed direct kill preserves pause and cannot confirm termination", async () => {
    const f = fixture(false, true);
    await watchdogTick(f.store, f.installation);
    assert(!f.calls().includes('"command":"unpause"'));
    assert.deepEqual(f.state(), {Running: true, Paused: true, Pid: 12345});
    assert.equal(f.store.read(`watchdog-stop-${f.id}.json`).state, "stop_unconfirmed");
});
