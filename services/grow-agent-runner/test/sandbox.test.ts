import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
const m = await import("../dist/sandbox.js").catch(() => null);
test("sandbox rejects inherited daemon selection and unpinned image choices", async () => {
    assert(m, "containment implementation required");
    await assert.rejects(
        () =>
            m.RootlessSandbox.open({
                root: mkdtempSync(join(tmpdir(), "grow-sandbox-policy-")),
                endpoint: "tcp://localhost:2375",
                docker: "/home/ramaaditya/bin/docker",
                images: [],
            }),
        /endpoint/i,
    );
});
test("process identity includes start time and rejects reused PID", () => {
    assert(m);
    const identity = m.processIdentity(process.pid);
    assert(m.sameProcess(identity));
    assert(!m.sameProcess({...identity, start: "0"}));
});
test("EX-15 a tag without digest is rejected", () => {
    assert(m, "containment implementation required");
    assert.throws(() => m.assertPinnedImages(["codex:latest"]), /Pinned image ID required/);
    assert.throws(() => m.assertPinnedImages(["sha256:" + "g".repeat(64)]), /Pinned image ID required/);
    assert.doesNotThrow(() => m.assertPinnedImages(["sha256:" + "a".repeat(64)]));
});
test("EX-15 a non-rootless engine is rejected", () => {
    assert(m, "containment implementation required");
    const ready = {SecurityOptions: ["name=rootless", "name=seccomp,profile=default"], CgroupVersion: "2"};
    assert.doesNotThrow(() => m.assertRootlessEngine(ready));
    assert.throws(
        () => m.assertRootlessEngine({...ready, SecurityOptions: ["name=seccomp,profile=default"]}),
        /Rootless seccomp cgroup v2 required/,
    );
    assert.throws(
        () => m.assertRootlessEngine({...ready, SecurityOptions: ["name=rootless"]}),
        /Rootless seccomp cgroup v2 required/,
    );
    assert.throws(
        () => m.assertRootlessEngine({...ready, CgroupVersion: "1"}),
        /Rootless seccomp cgroup v2 required/,
    );
});
