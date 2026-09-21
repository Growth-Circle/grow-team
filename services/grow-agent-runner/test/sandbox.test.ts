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
