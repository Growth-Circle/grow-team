import {test} from "node:test";
import assert from "node:assert/strict";
import {
    mkdtempSync,
    writeFileSync,
    readFileSync,
    mkdirSync,
    existsSync,
    symlinkSync,
} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {execFileSync} from "node:child_process";
const module = await import("../dist/workspace.js").catch(() => null);
const git = (cwd: string, ...args: string[]) =>
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
function fixture() {
    const root = mkdtempSync(join(tmpdir(), "grow-workspace-test-"));
    const source = join(root, "source");
    mkdirSync(source);
    git(source, "init", "-q");
    git(source, "config", "user.email", "fixture@invalid");
    git(source, "config", "user.name", "Fixture");
    writeFileSync(join(source, "a.txt"), "approved\n");
    git(source, "add", ".");
    git(source, "commit", "-qm", "base\n\nCo-Authored-By: CADIS <agent@cadis.digital>");
    const base = git(source, "rev-parse", "HEAD");
    git(source, "remote", "add", "origin", "https://example.invalid/owner/repo.git");
    const d = {
        attempt_id: "a",
        lease_epoch: 1,
        base_ref: "HEAD",
        repository: {
            id: "repo",
            canonical_origin: "https://example.invalid/owner/repo.git",
            allowed_refs: ["HEAD"],
        },
    };
    return {root, source, d, base};
}
test("independent checkout excludes dirty WIP and ignores poisoned hooks and filters", async () => {
    assert(module, "workspace implementation is required");
    const {root, source, d, base} = fixture();
    writeFileSync(join(source, "a.txt"), "dirty WIP");
    writeFileSync(join(source, "untracked"), "private");
    git(source, "config", "core.hooksPath", "/host-poison");
    git(source, "config", "filter.evil.smudge", "touch /host-poison");
    const w = await module.prepareWorkspace(d, () => ({attempt_id: "a", lease_epoch: 1}), {
        root: join(root, "retained"),
        source,
        approvedCommit: base,
    });
    assert.equal(readFileSync(join(w.checkout, "a.txt"), "utf8"), "approved\n");
    assert.equal(existsSync(join(w.checkout, "untracked")), false);
    assert.equal(readFileSync(join(source, "a.txt"), "utf8"), "dirty WIP");
    assert.equal(w.record.base_commit, base);
    assert.equal(w.record.user_worktree_dirty, true);
    assert(!existsSync(join(w.checkout, ".git")));
    assert(!readFileSync(join(w.gitDir, "config"), "utf8").includes("evil"));
    assert.equal(await module.hashFinalTree(w), w.record.tree_hash);
});
test("base resolution, credentials, sensitive files and symlinks fail closed", async () => {
    assert(module);
    const {root, source, d, base} = fixture();
    const opts = {root: join(root, "retained"), source, approvedCommit: base};
    await assert.rejects(
        () =>
            module.prepareWorkspace(
                {...d, base_ref: "unapproved"},
                () => ({attempt_id: "a", lease_epoch: 1}),
                opts,
            ),
        /ref/i,
    );
    git(source, "remote", "set-url", "origin", "https://credential@example.invalid/owner/repo.git");
    await assert.rejects(
        () => module.prepareWorkspace(d, () => ({attempt_id: "a", lease_epoch: 1}), opts),
        /origin/i,
    );
    git(source, "remote", "set-url", "origin", d.repository.canonical_origin);
    symlinkSync("/etc/passwd", join(source, "escape"));
    git(source, "add", ".");
    git(source, "commit", "-qm", "symlink\n\nCo-Authored-By: CADIS <agent@cadis.digital>");
    await assert.rejects(
        () =>
            module.prepareWorkspace(d, () => ({attempt_id: "a", lease_epoch: 1}), {
                ...opts,
                approvedCommit: git(source, "rev-parse", "HEAD"),
            }),
        /symlink/i,
    );
});
test("tar parser rejects links, traversal, oversized entries and preserves binary regular files", async () => {
    assert(module, "workspace implementation is required");
    const good = Buffer.alloc(2048);
    good.write("./ok");
    good.write("0000660\0", 100);
    good.write("00000000003\0", 124);
    good.write("0", 156);
    good.write("abc", 512);
    const files = module.parseSnapshotTar(good, 1024);
    assert.equal(files[0].bytes.toString(), "abc");
    const bad = Buffer.from(good);
    bad.write("../bad\0", 0);
    assert.throws(() => module.parseSnapshotTar(bad, 1024), /path/i);
    bad.fill(0, 0, 100);
    bad.write("link");
    bad.write("2", 156);
    assert.throws(() => module.parseSnapshotTar(bad, 1024), /type|link/i);
    assert.throws(() => module.parseSnapshotTar(good, 2), /limit/i);
});
test("matching clean and process filters never execute during dirty WIP inspection", async () => {
    assert(module);
    const {root, source, d} = fixture();
    writeFileSync(join(source, ".gitattributes"), "*.txt filter=poison\n");
    git(source, "add", ".gitattributes");
    git(source, "commit", "-qm", "attributes\n\nCo-Authored-By: CADIS <agent@cadis.digital>");
    const base = git(source, "rev-parse", "HEAD");
    const marker = join(root, "FILTER_EXECUTED");
    git(source, "config", "filter.poison.clean", `tee ${marker}`);
    writeFileSync(join(source, "a.txt"), "modified\n");
    const w = await module.prepareWorkspace(d, () => ({attempt_id: "a", lease_epoch: 1}), {
        root: join(root, "retained"),
        source,
        approvedCommit: base,
    });
    assert(w.record.user_worktree_dirty);
    assert.equal(existsSync(marker), false, "untrusted clean filter ran in the host broker");
    assert.equal(readFileSync(join(source, "a.txt"), "utf8"), "modified\n");
});
test("preparation and final-tree traversal recheck current authority between files", async () => {
    assert(module);
    const {root, source, d, base} = fixture();
    let calls = 0;
    await assert.rejects(
        () =>
            module.prepareWorkspace(
                d,
                () => {
                    if (++calls >= 3) throw Error("lease revoked");
                    return {attempt_id: "a", lease_epoch: 1};
                },
                {root: join(root, "retained"), source, approvedCommit: base},
            ),
        /lease revoked/,
    );
    const w = await module.prepareWorkspace(d, () => ({attempt_id: "a", lease_epoch: 1}), {
        root: join(root, "retained2"),
        source,
        approvedCommit: base,
    });
    await assert.rejects(
        () =>
            module.hashFinalTree(w, () => {
                throw Error("cancelled");
            }),
        /cancelled/,
    );
});
