import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
const m = await import("../dist/tool-broker.js").catch(() => null);
test("bounded tool validation rejects traversal, option injection and undeclared tools", () => {
    assert(m, "tool broker implementation required");
    assert.throws(() => m.validateTool({kind: "read", path: "../secret"}), /path/);
    assert.throws(() => m.validateTool({kind: "shell", argv: [], cwd: "."}), /argv/);
    assert.throws(() => m.validateTool({kind: "provider", path: "a"}), /tool/);
    assert.throws(() => m.validateTool({kind: "edit", path: ".env", content: "x"}), /Sensitive/);
});
test("artifact store bounds total output and binds retained bytes to checksum", async () => {
    assert(m);
    const a = new m.ArtifactStore(mkdtempSync(join(tmpdir(), "grow-artifacts-")), 10, 12);
    const first = a.retain("job", "attempt", "log", Buffer.from("hello"));
    assert.equal(first.record.size, 5);
    assert.equal(
        first.record.checksum,
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    );
    assert.throws(() => a.retain("job", "attempt", "log", Buffer.alloc(11)), /limit/);
    assert.throws(() => a.retain("job", "attempt", "log", Buffer.alloc(8)), /limit/);
});

test("artifact budget includes resumed attempts in the same job", () => {
    assert(m);
    const root = mkdtempSync(join(tmpdir(), "grow-job-artifacts-"));
    new m.ArtifactStore(root, 10, 12).retain("job", "attempt-one", "log", Buffer.alloc(8));
    assert.throws(
        () =>
            new m.ArtifactStore(root, 10, 12).retain("job", "attempt-two", "log", Buffer.alloc(5)),
        /limit/,
    );
    assert.equal(
        new m.ArtifactStore(root, 10, 12).retain(
            "another-job",
            "attempt-three",
            "log",
            Buffer.alloc(5),
        ).record.size,
        5,
    );
});
