import {test, after} from "node:test";
import assert from "node:assert/strict";
import {
    mkdtempSync,
    mkdirSync,
    writeFileSync,
    readFileSync,
    readdirSync,
    existsSync,
} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawn, execFileSync} from "node:child_process";
import {randomUUID} from "node:crypto";
import {RootlessSandbox} from "../dist/sandbox.js";
import {
    processIdentity,
    sameProcess,
    docker,
    inspect,
    command,
    owned,
    stopContainer,
} from "../dist/containment.js";
import {PrivateStore} from "../dist/config.js";
import {prepareWorkspace, hashFinalTree} from "../dist/workspace.js";
const endpoint = `unix:///run/user/${process.getuid!()}/docker.sock`,
    binary =
        process.env.GROW_TEST_DOCKER ??
        execFileSync("/usr/bin/which", ["docker"], {encoding: "utf8"}).trim();
const image = execFileSync(
    binary,
    ["--host", endpoint, "image", "inspect", "grow-task6-tools:20260922", "--format", "{{.Id}}"],
    {encoding: "utf8"},
).trim();
const root = mkdtempSync(join(tmpdir(), "grow-task6-hostile-"));
console.log("RETAINED_EVIDENCE", root);
const policy = {
    image_digest: image,
    cpu_millicores: 250,
    memory_bytes: 134217728,
    pids_limit: 32,
    temporary_bytes: 1048576,
};
const sandbox = await RootlessSandbox.open({
    root: join(root, "containment"),
    endpoint,
    docker: binary,
    images: [image],
});
function fixture() {
    const id = randomUUID(),
        source = join(root, `source-${id}`);
    mkdirSync(source);
    const git = (...a: string[]) =>
        execFileSync("/usr/bin/git", a, {
            cwd: source,
            encoding: "utf8",
            env: {
                PATH: "/usr/bin:/bin",
                HOME: "/nonexistent",
                GIT_CONFIG_GLOBAL: "/dev/null",
                GIT_CONFIG_NOSYSTEM: "1",
            },
        }).trim();
    git("init", "-q");
    git("config", "user.email", "test@invalid");
    git("config", "user.name", "Fixture");
    writeFileSync(join(source, "a.txt"), "base\n");
    git("add", ".");
    git("commit", "-qm", "base");
    git("remote", "add", "origin", "https://example.invalid/owner/repo.git");
    const d = {
        attempt_id: id,
        lease_epoch: 1,
        base_ref: "HEAD",
        repository: {
            id: "repo",
            canonical_origin: "https://example.invalid/owner/repo.git",
            allowed_refs: ["HEAD"],
        },
        policy: {sandbox: policy},
    };
    const abort = new AbortController();
    const guard = {
        lease: () => ({attempt_id: id, lease_epoch: 1}),
        deadline: Date.now() + 60000,
        signal: abort.signal,
    };
    return {d, guard, abort, source, git};
}
async function workspace(f: ReturnType<typeof fixture>) {
    return prepareWorkspace(f.d, f.guard.lease, {
        root: join(root, "workspaces"),
        source: f.source,
        approvedCommit: f.git("rev-parse", "HEAD"),
    });
}
const node = (script: string) => ["node", "-e", script];
test("real read-only, secrets, direct IPv4 IPv6 metadata bridge DNS and host paths are denied", async () => {
    const f = fixture(),
        w = await workspace(f);
    process.env.GROW_SYNTHETIC_SECRET = "host-canary-" + randomUUID();
    const script = `const fs=require('fs'),net=require('net'),dns=require('dns').promises; (async()=>{let denied=0;for(const p of ['/etc/escape','/workspace/a.txt'])try{fs.writeFileSync(p,'bad');throw Error('write escaped')}catch(e){if(!['EROFS','EACCES'].includes(e.code))throw e;denied++;}for(const p of ['/home/ramaaditya','/root/.ssh','/var/run/docker.sock','/run/user/1000/docker.sock','/seed','/workspace/.git'])if(fs.existsSync(p))throw Error('host path exposed');if(process.env.GROW_SYNTHETIC_SECRET)throw Error('secret env inherited');for(const ip of ['1.1.1.1','169.254.169.254','172.17.0.1','::1','2606:4700:4700::1111','127.0.0.1'])await new Promise((resolve,reject)=>{const s=net.connect({host:ip,port:80});s.setTimeout(500);s.on('connect',()=>reject(Error('network escaped')));s.on('error',()=>resolve());s.on('timeout',()=>{s.destroy();resolve()})});try{await dns.resolve4('example.com');throw Error('DNS escaped')}catch(e){if(e.message==='DNS escaped')throw e;}console.log(JSON.stringify({denied,uid:process.getuid(),status:fs.readFileSync('/proc/self/status','utf8').split('\\n').filter(x=>/CapEff|NoNewPrivs|Seccomp/.test(x))}));})().catch(e=>{console.error(e);process.exit(1)});`;
    const r = await sandbox.runSandboxedTool(f.d, f.guard, w, node(script), {
        write: false,
        timeoutMs: 15000,
    });
    assert.equal(r.exitCode, 0, r.output.toString());
    assert(r.stopConfirmed);
    assert.match(r.output.toString(), /65532/);
    assert.equal(readFileSync(join(f.source, "a.txt"), "utf8"), "base\n");
});
test("bounded writable tmpfs exports actual files and preserves independent WIP", async () => {
    const f = fixture();
    writeFileSync(join(f.source, "a.txt"), "dirty WIP");
    const w = await workspace(f);
    const r = await sandbox.runSandboxedTool(
        f.d,
        f.guard,
        w,
        node(
            `require('fs').writeFileSync('/workspace/a.txt','changed');require('fs').writeFileSync('/workspace/new.bin',Buffer.from([0,1,255]));`,
        ),
        {write: true, timeoutMs: 10000},
    );
    assert.equal(r.exitCode, 0, r.output.toString());
    assert.equal(readFileSync(join(w.checkout, "a.txt"), "utf8"), "changed");
    assert.deepEqual(readFileSync(join(w.checkout, "new.bin")), Buffer.from([0, 1, 255]));
    assert.equal(readFileSync(join(f.source, "a.txt"), "utf8"), "dirty WIP");
    assert.notEqual(await hashFinalTree(w), w.record.tree_hash);
});
test("real disk output process and memory limits constrain hostile processes", async () => {
    const f = fixture(),
        w = await workspace(f);
    const disk = await sandbox.runSandboxedTool(
        f.d,
        f.guard,
        w,
        node(
            `const fs=require('fs');let code;try{fs.writeFileSync('/tmp/flood',Buffer.alloc(2*1024*1024))}catch(e){code=e.code}if(code!=='ENOSPC')process.exit(1);console.log(code);`,
        ),
        {write: false, timeoutMs: 10000},
    );
    assert.equal(disk.exitCode, 0, disk.output.toString());
    const output = await sandbox.runSandboxedTool(
        f.d,
        f.guard,
        w,
        node(`for(;;)process.stdout.write('x'.repeat(65536))`),
        {write: false, timeoutMs: 5000},
    );
    assert(output.overflow);
    assert(output.output.length <= 51200);
    assert(output.stopConfirmed);
    const pids = await sandbox.runSandboxedTool(
        f.d,
        f.guard,
        w,
        node(
            `const cp=require('child_process');let denied=false;for(let i=0;i<100;i++){let p=cp.spawn('/bin/sleep',['30']);p.on('error',e=>{if(e.code==='EAGAIN')denied=true});}setTimeout(()=>{console.log(denied?'PIDS_DENIED':'FAIL');process.exit(denied?0:1)},1000)`,
        ),
        {write: false, timeoutMs: 10000},
    );
    assert.equal(pids.exitCode, 0, pids.output.toString());
    const memory = await sandbox.runSandboxedTool(
        f.d,
        f.guard,
        w,
        node(`const blocks=[];setInterval(()=>{blocks.push(Buffer.alloc(16*1024*1024,1))},10)`),
        {write: false, timeoutMs: 10000},
    );
    assert.notEqual(memory.exitCode, 0);
    assert(memory.stopConfirmed);
});
test("cancel kills setsid double-fork descendants that ignore TERM", async () => {
    const f = fixture(),
        w = await workspace(f);
    const grandchild =
        "process.title='grow-doublefork-grandchild';process.on('SIGTERM',()=>{});setInterval(()=>{},1000)";
    const launcher = `require('child_process').spawn('node',['-e',${JSON.stringify(grandchild)}],{detached:true,stdio:'ignore'}).unref()`;
    const stubborn =
        "process.title='grow-stubborn-child';process.on('SIGTERM',()=>{});setInterval(()=>{},1000)";
    const script = `const cp=require('child_process');process.on('SIGTERM',()=>{});cp.spawn('node',['-e',${JSON.stringify(stubborn)}],{detached:true,stdio:'ignore'}).unref();cp.spawn('node',['-e',${JSON.stringify(launcher)}],{detached:true,stdio:'ignore'}).unref();setInterval(()=>{},1000)`;
    const run = sandbox.runSandboxedTool(f.d, f.guard, w, node(script), {
        write: false,
        timeoutMs: 20000,
    });
    let id = "";
    for (let n = 0; n < 50; n++) {
        const handles = await sandbox.inspect();
        id = handles.find((x) => x.attempt_id === f.d.attempt_id)?.container_id;
        if (id && (await inspect(sandbox.installation, id)).State.Running) break;
        await new Promise((r) => setTimeout(r, 100));
    }
    await new Promise((r) => setTimeout(r, 1000));
    const top = await docker(sandbox.installation, ["top", id, "-eo", "pid,args"]);
    const processes = top.stdout
        .toString()
        .split("\n")
        .slice(1)
        .map((x) => Number(x.trim().split(/\s+/)[0]))
        .filter(Boolean)
        .map(processIdentity);
    assert(processes.length >= 5, top.stdout.toString());
    assert.match(top.stdout.toString(), /grow-doublefork-grandchild/);
    assert.match(top.stdout.toString(), /grow-stubborn-child/);
    f.abort.abort();
    await run;
    assert(processes.every((p) => !sameProcess(p)));
    assert.equal((await inspect(sandbox.installation, id)).State.Running, false);
});
test("foreign installation and reused process identities cannot grant stop authority", async () => {
    const foreign = await RootlessSandbox.open({
        root: join(root, "foreign"),
        endpoint,
        docker: binary,
        images: [image],
    });
    const f = fixture(),
        w = await workspace(f);
    const pending = foreign.runSandboxedTool(f.d, f.guard, w, node("setInterval(()=>{},1000)"), {
        write: false,
        timeoutMs: 15000,
    });
    let id = "";
    for (let n = 0; n < 50; n++) {
        id = (await foreign.inspect())[0]?.container_id;
        if (id) break;
        await new Promise((r) => setTimeout(r, 100));
    }
    await assert.rejects(() => inspect(sandbox.installation, id), /Foreign/);
    await sandbox.stopScope(f.d.attempt_id, "attempt");
    assert.equal((await inspect(foreign.installation, id)).State.Running, true);
    f.abort.abort();
    await pending;
});
test("independent user watchdog kills real containers after supervisor SIGKILL", async () => {
    const f = fixture(),
        w = await workspace(f),
        childRoot = join(root, "crashed-supervisor");
    const config = {d: f.d, w, root: childRoot, endpoint, docker: binary, images: [image]};
    const configPath = join(root, "crash-config.json");
    writeFileSync(configPath, JSON.stringify(config), {mode: 0o600});
    const code = `import fs from 'node:fs';import {RootlessSandbox} from ${JSON.stringify(new URL("../dist/sandbox.js", import.meta.url).href)};const c=JSON.parse(fs.readFileSync(process.argv[1]));const s=await RootlessSandbox.open(c);await s.runSandboxedTool(c.d,{lease:()=>({attempt_id:c.d.attempt_id,lease_epoch:1}),deadline:Date.now()+30000,signal:new AbortController().signal},c.w,['node','-e',"require('child_process').spawn('node',['-e','process.on(\\\"SIGTERM\\\",()=>{});setInterval(()=>{},1000)'],{detached:true,stdio:'ignore'}).unref();setInterval(()=>{},1000)"],{write:false,timeoutMs:25000});`;
    const child = spawn(process.execPath, ["--input-type=module", "-e", code, configPath], {
        stdio: ["ignore", "pipe", "pipe"],
    });
    let errors = "";
    child.stderr.on("data", (x) => (errors += x));
    const exit = new Promise((r) => child.once("exit", r));
    let record: any;
    for (let n = 0; n < 100; n++) {
        if (existsSync(childRoot)) {
            const name = readdirSync(childRoot).find((x) => x.startsWith("container-"));
            if (name) {
                record = JSON.parse(readFileSync(join(childRoot, name), "utf8"));
                if (record.state === "running") break;
            }
        }
        await new Promise((r) => setTimeout(r, 100));
    }
    assert.equal(record?.state, "running", errors);
    await new Promise((r) => setTimeout(r, 500));
    const installation = JSON.parse(readFileSync(join(childRoot, "installation.json"), "utf8"));
    const top = await docker(installation, ["top", record.id, "-eo", "pid"]);
    const processes = top.stdout
        .toString()
        .split("\n")
        .slice(1)
        .map((x) => Number(x.trim()))
        .filter(Boolean)
        .map(processIdentity);
    assert(processes.length >= 3);
    child.kill("SIGKILL");
    await exit;
    for (let n = 0; n < 60; n++) {
        if (
            !(await inspect(installation, record.id)).State.Running &&
            existsSync(join(childRoot, `watchdog-stop-${record.id}.json`))
        )
            break;
        await new Promise((r) => setTimeout(r, 100));
    }
    assert.equal((await inspect(installation, record.id)).State.Running, false);
    assert(processes.every((p) => !sameProcess(p)));
    assert.equal(
        JSON.parse(readFileSync(join(childRoot, `watchdog-stop-${record.id}.json`), "utf8")).state,
        "stopped",
    );
});
test("expiry, current lease loss and concurrent launch fence stop all effects", async () => {
    for (const cause of ["expiry", "lease"]) {
        const f = fixture(),
            w = await workspace(f);
        let valid = true;
        f.guard.lease = () => {
            if (!valid) throw Error("Lost lease");
            return {attempt_id: f.d.attempt_id, lease_epoch: 1};
        };
        if (cause === "expiry") f.guard.deadline = Date.now() + 900;
        const run = sandbox.runSandboxedTool(f.d, f.guard, w, node("setInterval(()=>{},1000)"), {
            write: false,
            timeoutMs: 10000,
        });
        await assert.rejects(
            () =>
                sandbox.runSandboxedTool(f.d, f.guard, w, node("process.exit(0)"), {
                    write: false,
                    timeoutMs: 10000,
                }),
            /Concurrent/,
        );
        if (cause === "lease") setTimeout(() => (valid = false), 900);
        const result = await run;
        assert(result.timedOut);
        assert(result.stopConfirmed);
        await assert.rejects(
            () =>
                sandbox.runSandboxedTool(f.d, f.guard, w, node("process.exit(0)"), {
                    write: false,
                    timeoutMs: 1000,
                }),
            /revoked|lease/,
        );
    }
});
test("tmpfs workspace disk bound and hostile symlinks cannot change retained tree", async () => {
    const f = fixture(),
        w = await workspace(f);
    const before = await hashFinalTree(w);
    const result = await sandbox.runSandboxedTool(
        f.d,
        f.guard,
        w,
        node(
            `const fs=require('fs');let denied=false;for(let i=0;i<40;i++){try{fs.writeFileSync('/workspace/f'+i,Buffer.alloc(1024*1024))}catch(e){if(e.code==='ENOSPC')denied=true;break}}if(!denied)process.exit(1);console.log('WORKSPACE_ENOSPC')`,
        ),
        {write: true, timeoutMs: 10000},
    );
    assert.equal(result.exitCode, 0, result.output.toString());
    assert.match(result.output.toString(), /WORKSPACE_ENOSPC/);
    const f2 = fixture(),
        w2 = await workspace(f2),
        before2 = await hashFinalTree(w2);
    await assert.rejects(
        () =>
            sandbox.runSandboxedTool(
                f2.d,
                f2.guard,
                w2,
                node(`require('fs').symlinkSync('/etc/passwd','/workspace/escape')`),
                {write: true, timeoutMs: 10000},
            ),
        /link|type/,
    );
    assert.equal(await hashFinalTree(w2), before2);
    assert.equal(readFileSync(join(f2.source, "a.txt"), "utf8"), "base\n");
});
test("actual cgroup CPU throttle, memory PID settings and namespace controls are observed", async () => {
    const f = fixture(),
        w = await workspace(f);
    const script = `const fs=require('fs');const start=Date.now();while(Date.now()-start<1200){}console.log(JSON.stringify(Object.fromEntries(['memory.max','pids.max','cpu.max','cpu.stat'].map(p=>[p,fs.readFileSync('/sys/fs/cgroup/'+p,'utf8')]))));`;
    const result = await sandbox.runSandboxedTool(f.d, f.guard, w, node(script), {
        write: false,
        timeoutMs: 10000,
    });
    assert.equal(result.exitCode, 0);
    const actual = JSON.parse(result.output.toString());
    assert.equal(actual["memory.max"].trim(), "134217728");
    assert.equal(actual["pids.max"].trim(), "32");
    assert.equal(actual["cpu.max"].trim(), "25000 100000");
    assert(Number(/nr_throttled (\d+)/.exec(actual["cpu.stat"])![1]) > 0);
    const item = await inspect(sandbox.installation, result.containerId);
    assert.equal(item.HostConfig.PidMode, "");
    assert.equal(item.HostConfig.NetworkMode, "none");
    assert.equal(item.HostConfig.ReadonlyRootfs, true);
    assert.deepEqual(item.HostConfig.CapDrop, ["ALL"]);
    assert(item.HostConfig.SecurityOpt.includes("no-new-privileges"));
});
import {Journal} from "../dist/journal.js";
import {OperationBoundary} from "../dist/supervisor.js";
import {ToolBroker, ArtifactStore} from "../dist/tool-broker.js";
import {digest, parse} from "../dist/protocol.js";
async function brokerFixture(
    actions = ["repository.read", "repository.edit", "checks.run", "shell.run"],
) {
    const f = fixture();
    f.d.repository.id = randomUUID();
    const w = await workspace(f);
    Object.assign(f.d, {
        job_id: randomUUID(),
        job_kind: "code",
        budget: {tool_rounds: 20, shell_timeout_seconds: 10, tool_output_bytes: 51200},
        repository: {
            ...f.d.repository,
            required_checks: [
                {
                    id: "actual-content",
                    argv: [
                        "node",
                        "-e",
                        "if(require('fs').readFileSync('a.txt','utf8')!=='verified')process.exit(1)",
                    ],
                    cwd: ".",
                    timeout_seconds: 10,
                },
            ],
        },
        policy: {
            ...f.d.policy,
            actions,
            network: {
                targets: [],
                public_https_only: true,
                block_metadata: true,
                cross_origin_authorization: false,
                project_network: false,
            },
        },
    });
    f.guard.lease = () => ({
        job_id: f.d.job_id,
        attempt_id: f.d.attempt_id,
        lease_epoch: 1,
        job_version: 1,
    });
    const j = new Journal(join(root, `journal-${f.d.attempt_id}`));
    let serverTree = w.record.tree_hash;
    let loseConsume = false;
    const events: any[] = [];
    const published = new Set<string>();
    const proposals = new Map<string, any>();
    const transport: any = {
        mutate: async (kind: string, id: string, route: string, body: any) => {
            if (kind === "proposal") {
                assert.equal(body.tree_hash, serverTree);
                parse("operation_arguments", body.arguments);
                const operation = {
                    operation_id: body.operation_id,
                    version: 1,
                    operation_hash: digest(body),
                    status: "authorized",
                    arguments: body.arguments,
                };
                proposals.set(body.operation_id, operation);
                return {operation};
            }
            if (kind === "consume") {
                if (loseConsume) throw Error("Lost consume response");
                return {
                    operation: {...proposals.get(body.operation_id), status: "started", version: 2},
                };
            }
            throw Error("Unexpected route");
        },
    };
    const channel: any = {
        lease: f.guard.lease,
        operations: new OperationBoundary(j, transport, f.guard.lease),
        event: async (type: string, payload: any) => {
            events.push({type, payload});
            if (type.startsWith("tool."))
                assert.equal(
                    payload.argument_digest,
                    proposals.get(payload.operation_id).operation_hash,
                );
            if (
                type === "tool.finished" &&
                ["repository.edit", "shell.run"].includes(payload.tool_class)
            )
                serverTree = "";
            if (type === "verification.finished") {
                assert.equal(payload.tree_hash, serverTree);
                assert(published.has(payload.artifact_id));
                const op = proposals.get(payload.operation_id);
                assert.equal(op.arguments.tree_hash, serverTree);
            }
        },
    };
    const broker = new ToolBroker(
        f.d,
        f.guard,
        w,
        sandbox,
        channel,
        j,
        new ArtifactStore(join(root, `artifacts-${f.d.attempt_id}`), 10485760, 52428800),
        {
            upload: async (a) => {
                const receipt = {
                    artifact_id: randomUUID(),
                    checksum: a.record.checksum,
                    size: a.record.size,
                };
                assert.notEqual(receipt.artifact_id, a.record.id);
                published.add(receipt.artifact_id);
                return receipt;
            },
            publishTree: async (workspace, tree, ids) => {
                assert.equal(await hashFinalTree(workspace), tree);
                assert(ids.every((id) => published.has(id)));
                serverTree = tree;
            },
        },
    );
    return {f, w, j, broker, events, lose: () => (loseConsume = true)};
}
test("actual file tools, consumed operations and owner checks bind to the final tree", async () => {
    const {f, w, j, broker, events} = await brokerFixture();
    try {
        const edited = await broker.runSandboxedTool(randomUUID(), {
            kind: "edit",
            path: "a.txt",
            content: "verified",
        });
        assert.equal(edited.result.exitCode, 0);
        const read = await broker.runSandboxedTool(randomUUID(), {kind: "read", path: "a.txt"});
        assert.equal(read.result.output.toString(), "verified");
        const searched = await broker.runSandboxedTool(randomUUID(), {
            kind: "search",
            path: ".",
            query: "verified",
        });
        assert.match(searched.result.output.toString(), /a.txt/);
        const evidence = await broker.verifyFinalTree();
        assert(evidence.passed);
        assert.equal(evidence.records.length, 1);
        assert.equal(await broker.assertVerified(), evidence.tree);
        assert(events.some((x) => x.type === "verification.finished"));
        await broker.runSandboxedTool(randomUUID(), {
            kind: "edit",
            path: "a.txt",
            content: "later edit",
        });
        await assert.rejects(() => broker.assertVerified(), /invalidated/);
        assert.equal(readFileSync(join(f.source, "a.txt"), "utf8"), "base\n");
    } finally {
        j.close();
    }
});
test("lost consume authority does not execute or repeat an edit", async () => {
    const {w, j, broker, lose} = await brokerFixture();
    try {
        const before = await hashFinalTree(w),
            id = randomUUID();
        lose();
        await assert.rejects(
            () =>
                broker.runSandboxedTool(id, {kind: "edit", path: "a.txt", content: "unauthorized"}),
            /Lost consume/,
        );
        assert.equal(await hashFinalTree(w), before);
        await assert.rejects(
            () => broker.runSandboxedTool(id, {kind: "edit", path: "a.txt", content: "retry"}),
            /already used/,
        );
        assert.equal(await hashFinalTree(w), before);
    } finally {
        j.close();
    }
});
test("separate probe grant cancellation contains synthetic probes and exposes no project mount", async () => {
    const abort = new AbortController(),
        setup = randomUUID();
    const d = {setup_operation_id: setup, policy: {sandbox: policy}};
    let current = true;
    const authority = {
        setup_operation_id: setup,
        deadline: Date.now() + 15000,
        signal: abort.signal,
        assertCurrent: () => {
            if (!current) throw Error("Probe grant revoked");
        },
    };
    const pending = sandbox.runProbe(
        d,
        authority,
        node(
            `const fs=require('fs');if(fs.existsSync('/seed')||fs.existsSync('/workspace/a.txt'))process.exit(4);process.on('SIGTERM',()=>{});setInterval(()=>{},1000)`,
        ),
        10000,
    );
    let handle: any;
    for (let n = 0; n < 50; n++) {
        handle = (await sandbox.inspect()).find((x) => x.attempt_id === `probe-${setup}`);
        if (handle) break;
        await new Promise((r) => setTimeout(r, 100));
    }
    assert.equal(handle.kind, "probe");
    await new Promise((r) => setTimeout(r, 500));
    abort.abort();
    const r = await pending;
    assert(r.stopConfirmed);
    await assert.rejects(
        () => sandbox.runProbe(d, authority, node("process.exit(0)"), 1000),
        /revoked/,
    );
});
test("unknown required executables fail checks and output uses the descriptor byte budget", async () => {
    const {f, w, j, broker} = await brokerFixture();
    try {
        const shell = await broker.runSandboxedTool(randomUUID(), {
            kind: "shell",
            argv: ["grow-owner-tool-not-installed"],
            cwd: ".",
        });
        assert.notEqual(shell.result.exitCode, 0);
        const failed = await broker.verifyFinalTree();
        assert.equal(failed.passed, false);
        await assert.rejects(() => broker.assertVerified(), /invalidated/);
    } finally {
        j.close();
    }
    const f2 = fixture(),
        w2 = await workspace(f2);
    Object.assign(f2.d, {budget: {tool_output_bytes: 1024}});
    const output = await sandbox.runSandboxedTool(
        f2.d,
        f2.guard,
        w2,
        node("process.stdout.write('x'.repeat(8192))"),
        {write: false, timeoutMs: 10000},
    );
    assert(output.overflow);
    assert.equal(output.output.length, 1024);
});
test("restart discovery contains owned orphans and installation identity survives reopen", async () => {
    const installation = sandbox.installation;
    const reloaded = await RootlessSandbox.open({
        root: join(root, "containment"),
        endpoint,
        docker: binary,
        images: [image],
    });
    assert.equal(reloaded.installation.id, installation.id);
    const scope = randomUUID();
    const created = await docker(installation, [
        "create",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=65532:0",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=67108864",
        "--pids-limit=16",
        "--cpus=0.1",
        `--label=digital.cadis.grow.installation=${installation.id}`,
        `--label=digital.cadis.grow.scope=${scope}`,
        "--label=digital.cadis.grow.kind=attempt",
        "--entrypoint=node",
        image,
        "-e",
        "setInterval(()=>{},1000)",
    ]);
    assert.equal(created.code, 0);
    const id = created.stdout.toString().trim();
    await docker(installation, ["start", id]);
    for (let n = 0; n < 60; n++) {
        if (
            !(await inspect(installation, id)).State.Running &&
            existsSync(join(root, "containment", `watchdog-stop-${id}.json`))
        )
            break;
        await new Promise((r) => setTimeout(r, 100));
    }
    assert.equal((await inspect(installation, id)).State.Running, false);
    assert(existsSync(join(root, "containment", `watchdog-stop-${id}.json`)));
});

after(async () => {
    for (const entry of readdirSync(root, {withFileTypes: true})) {
        if (!entry.isDirectory()) continue;
        const state = join(root, entry.name);
        if (!existsSync(join(state, "installation.json"))) continue;
        const store = new PrivateStore(state),
            installation = store.read<any>("installation.json");
        for (const id of await owned(installation)) {
            const item = await inspect(installation, id);
            const record = store.read<any>(`container-${id}.json`) ?? {
                id,
                installation: installation.id,
                scope: item.Config.Labels["digital.cadis.grow.scope"],
                kind: item.Config.Labels["digital.cadis.grow.kind"],
                owner: {pid: 0, start: "0"},
                deadline: 0,
                heartbeat: 0,
                processes: [],
                cgroups: [],
                state: "orphan",
            };
            assert(await stopContainer(installation, record));
        }
        await command("/usr/bin/systemctl", ["--user", "stop", `grow-watchdog-${installation.id}`]);
    }
});

test("watchdog service restarts after SIGKILL and keeps containment authority", async () => {
    const f = fixture(),
        w = await workspace(f),
        pending = sandbox.runSandboxedTool(f.d, f.guard, w, node("setInterval(()=>{},1000)"), {
            write: false,
            timeoutMs: 15000,
        });
    await new Promise((r) => setTimeout(r, 800));
    const store = new PrivateStore(join(root, "containment")),
        previous = store.read<any>("watchdog-ready.json");
    assert.equal(
        (
            await command("/usr/bin/systemctl", [
                "--user",
                "kill",
                "--kill-whom=main",
                "--signal=SIGKILL",
                `grow-watchdog-${sandbox.installation.id}`,
            ])
        ).code,
        0,
    );
    for (let n = 0; n < 40; n++) {
        const current = store.read<any>("watchdog-ready.json");
        if (current.pid !== previous.pid) break;
        await new Promise((r) => setTimeout(r, 100));
    }
    assert.notEqual(store.read<any>("watchdog-ready.json").pid, previous.pid);
    f.abort.abort();
    assert((await pending).stopConfirmed);
});

test("real read-only shell checkpoint permits the next repository read", async () => {
    const {broker, j} = await brokerFixture(["repository.read", "shell.run"]);
    try {
        const shell = await broker.runSandboxedTool(randomUUID(), {
            kind: "shell",
            argv: ["node", "--version"],
            cwd: ".",
        });
        assert.equal(shell.result.exitCode, 0);
        const container = await inspect(sandbox.installation, shell.result.containerId);
        assert.equal(container.Mounts.find((m) => m.Destination === "/workspace").RW, false);
        const next = await broker.runSandboxedTool(randomUUID(), {kind: "read", path: "a.txt"});
        assert.equal(next.result.output.toString(), "base\n");
        assert.equal(next.tree, shell.tree);
    } finally {
        j.close();
    }
});

test("implementation confirms direct stop of a real paused container", async () => {
    const f = fixture(),
        w = await workspace(f);
    const pending = sandbox.runSandboxedTool(
        f.d,
        f.guard,
        w,
        node("process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"),
        {write: false, timeoutMs: 15000},
    );
    let id = "";
    for (let n = 0; n < 50; n++) {
        const handle = (await sandbox.inspect()).find((x) => x.attempt_id === f.d.attempt_id);
        if (handle) {
            id = handle.container_id;
            if ((await inspect(sandbox.installation, id)).State.Running) break;
        }
        await new Promise((r) => setTimeout(r, 100));
    }
    await new Promise((r) => setTimeout(r, 400));
    assert.equal((await docker(sandbox.installation, ["pause", id])).code, 0);
    assert.equal((await inspect(sandbox.installation, id)).State.Paused, true);
    const record = new PrivateStore(join(root, "containment")).read<any>(`container-${id}.json`);
    assert(await stopContainer(sandbox.installation, record, () => true));
    const result = await pending;
    assert(result.stopConfirmed);
    const final = await inspect(sandbox.installation, id);
    assert.equal(final.State.Running, false);
    assert.equal(final.State.Paused, false);
    assert.equal(final.State.Pid, 0);
    assert.equal(final.State.ExitCode, 137);
});
