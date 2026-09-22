import {
    constants,
    closeSync,
    fstatSync,
    ftruncateSync,
    mkdirSync,
    openSync,
    readFileSync,
    readdirSync,
    lstatSync,
    writeFileSync,
    chmodSync,
    realpathSync,
} from "node:fs";
import {join, dirname} from "node:path";
import {execFileSync} from "node:child_process";
import {setImmediate as yieldLoop} from "node:timers/promises";
import {randomUUID} from "node:crypto";
import {PrivateStore} from "./config.js";
import type {Data} from "./protocol.js";
export type LeaseGuard = () => Data;
export interface Workspace {
    attemptId: string;
    leaseEpoch: number;
    root: string;
    checkout: string;
    gitDir: string;
    record: Data;
    limit: number;
}
export const MAX_TREE_BYTES = 32 * 1024 * 1024;
const env = {
    PATH: "/usr/bin:/bin",
    HOME: "/nonexistent",
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_CONFIG_GLOBAL: "/dev/null",
    GIT_TERMINAL_PROMPT: "0",
    GIT_OPTIONAL_LOCKS: "0",
    GIT_NO_REPLACE_OBJECTS: "1",
    GIT_NO_LAZY_FETCH: "1",
};
export function safePath(path: string): string {
    if (
        typeof path !== "string" ||
        !path ||
        path.length > 4096 ||
        path.startsWith("/") ||
        /[\\\x00-\x1f:]/.test(path) ||
        path.split("/").some((p) => !p || p === ".." || p === "." || /^\.git$/i.test(p))
    )
        throw new Error("Unsafe repository path");
    if (
        path
            .split("/")
            .some((p) =>
                /^(\.env($|\.)|\.ssh$|\.aws$|\.gnupg$|\.npmrc$|\.netrc$|\.codex$|\.claude$|credentials(\.json)?$|id_(rsa|ed25519)$)/i.test(
                    p,
                ),
            )
    )
        throw new Error("Sensitive repository path");
    return path;
}
export function assertLease(d: Data, guard: LeaseGuard): Data {
    const current = guard();
    for (const k of ["attempt_id", "lease_epoch"])
        if (current[k] !== d[k]) throw new Error("Stale workspace lease");
    return current;
}
function git(cwd: string, args: string[], input?: Buffer | string): Buffer {
    return execFileSync(
        "/usr/bin/git",
        [
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.allow=never",
            ...args,
        ],
        {cwd, env, input, timeout: 15000, maxBuffer: MAX_TREE_BYTES * 2},
    );
}
function ownGit(w: Workspace, args: string[], input?: Buffer | string): Buffer {
    return git(w.root, [`--git-dir=${w.gitDir}`, `--work-tree=${w.checkout}`, ...args], input);
}
function origin(value: string | null): string | null {
    if (value === null) return null;
    if (/^git@[a-zA-Z0-9.-]+:[a-zA-Z0-9_./-]+$/.test(value)) return value;
    try {
        const u = new URL(value);
        if (u.protocol !== "https:" || u.username || u.password || u.search || u.hash) throw 0;
        return value;
    } catch {
        throw new Error("Unsafe origin");
    }
}
export async function prepareWorkspace(
    d: Data,
    guard: LeaseGuard,
    options: {root: string; source: string; approvedCommit: string; limit?: number},
): Promise<Workspace> {
    const started = Date.now();
    const current = () => {
        assertLease(d, guard);
        if (Date.now() - started > 120000) throw new Error("Workspace preparation time limit");
    };
    current();
    const r = d.repository;
    if (
        !r ||
        !r.allowed_refs.includes(d.base_ref) ||
        !/^[a-zA-Z0-9_./-]+$/.test(d.base_ref) ||
        d.base_ref.startsWith("-")
    )
        throw new Error("Unapproved base ref");
    if (!/^[0-9a-f]{40}$/.test(options.approvedCommit)) throw new Error("Unapproved base commit");
    const source = realpathSync(options.source);
    const actualOrigin = git(source, [
        "config",
        "--local",
        "--no-includes",
        "--get",
        "remote.origin.url",
    ])
        .toString()
        .trim();
    if (origin(actualOrigin) !== origin(r.canonical_origin))
        throw new Error("Origin differs from owner approval");
    const base = git(source, ["rev-parse", "--verify", `${d.base_ref}^{commit}`])
        .toString()
        .trim();
    if (base !== options.approvedCommit) throw new Error("Base ref changed after approval");
    const store = new PrivateStore(options.root),
        ref = `workspace-${randomUUID()}`,
        root = join(store.root, ref);
    mkdirSync(root, {mode: 0o700});
    const checkout = join(root, "tree-0"),
        gitDir = join(root, "git");
    mkdirSync(checkout, {mode: 0o2770});
    chmodSync(checkout, 0o2770);
    if (
        options.limit !== undefined &&
        (!Number.isSafeInteger(options.limit) ||
            options.limit < 1 ||
            options.limit > MAX_TREE_BYTES)
    )
        throw new Error("Invalid workspace byte limit");
    const w: Workspace = {
        attemptId: d.attempt_id,
        leaseEpoch: d.lease_epoch,
        root,
        checkout,
        gitDir,
        record: {},
        limit: options.limit ?? MAX_TREE_BYTES,
    };
    git(root, ["init", "--quiet", "--bare", "--template=", gitDir]);
    const tree = git(source, ["rev-parse", `${base}^{tree}`])
        .toString()
        .trim();
    const entries = git(source, ["ls-tree", "-rz", "--full-tree", base])
        .toString()
        .split("\0")
        .filter(Boolean);
    let total = 0;

    for (const entry of entries) {
        current();
        await yieldLoop();
        current();
        const match = /^(\d+) (blob|commit) ([0-9a-f]{40})\t([\s\S]+)$/.exec(entry);
        if (!match) throw new Error("Invalid tree entry");
        const [, mode, type, sha, path] = match as unknown as [
            string,
            string,
            string,
            string,
            string,
        ];
        safePath(path);
        if (mode === "120000") throw new Error("Repository symlink denied");
        if (type !== "blob" || !["100644", "100755"].includes(mode))
            throw new Error("Submodule or special file denied");
        const bytes = git(source, ["cat-file", "blob", sha]);
        total += bytes.length;
        if (total > w.limit || entries.length > 20000) throw new Error("Workspace size limit");
        mkdirSync(dirname(join(checkout, path)), {recursive: true, mode: 0o2770});
        writeFileSync(join(checkout, path), bytes, {
            mode: mode === "100755" ? 0o770 : 0o660,
            flag: "wx",
        });
        ownGit(w, ["hash-object", "-w", "--stdin"], bytes);
    }
    // Copy tree and commit objects through plumbing. Never execute source hooks, filters, or upload-pack.
    async function copyTree(id: string): Promise<void> {
        current();
        await yieldLoop();
        current();
        const bytes = git(source, ["cat-file", "tree", id]);
        ownGit(w, ["hash-object", "-w", "-t", "tree", "--stdin"], bytes);
        for (const e of git(source, ["ls-tree", "-z", id]).toString().split("\0")) {
            const m = /^040000 tree ([0-9a-f]{40})\t/.exec(e);
            if (m) await copyTree(m[1]!);
        }
    }
    await copyTree(tree);
    ownGit(
        w,
        ["hash-object", "-w", "-t", "commit", "--stdin"],
        git(source, ["cat-file", "commit", base]),
    );
    writeFileSync(join(gitDir, "shallow"), base + "\n", {mode: 0o600});
    ownGit(w, ["update-ref", "refs/heads/attempt", base]);
    ownGit(w, ["symbolic-ref", "HEAD", "refs/heads/attempt"]);
    ownGit(w, ["read-tree", base]);
    const dirty =
        git(source, [
            `--git-dir=${gitDir}`,
            `--work-tree=${source}`,
            "status",
            "--porcelain=v1",
            "--untracked-files=normal",
            "--ignore-submodules=all",
        ]).length > 0 ||
        git(source, [
            "diff-index",
            "--cached",
            "--raw",
            "--no-ext-diff",
            "--no-textconv",
            base,
            "--",
        ]).length > 0;
    w.record = {
        repository_id: r.id,
        workspace_reference: ref,
        base_ref: d.base_ref,
        base_commit: base,
        tree_hash: tree,
        user_worktree_dirty: dirty,
    };
    assertLease(d, guard);
    new PrivateStore(root).write("workspace.json", {
        ...w,
        attempt_id: d.attempt_id,
        lease_epoch: d.lease_epoch,
    });
    return w;
}
export interface SnapshotFile {
    path: string;
    mode: number;
    bytes: Buffer;
}
export function parseSnapshotTar(tar: Buffer, limit: number): SnapshotFile[] {
    const files: SnapshotFile[] = [];
    const seen = new Set<string>();
    let total = 0;
    for (let at = 0; at + 512 <= tar.length; ) {
        const h = tar.subarray(at, at + 512);
        at += 512;
        if (h.every((v) => v === 0)) break;
        const str = (a: number, b: number) => h.subarray(a, b).toString().split("\0")[0]!;
        let path = [str(345, 500), str(0, 100)]
            .filter(Boolean)
            .join("/")
            .replace(/^\.\//, "")
            .replace(/\/$/, "");
        const type = str(156, 157);
        const size = parseInt(str(124, 136).trim() || "0", 8),
            mode = parseInt(str(100, 108).trim() || "0", 8);
        if (!Number.isSafeInteger(size) || size < 0 || at + size > tar.length)
            throw new Error("Invalid tar size");
        if (!["0", "", "5"].includes(type)) throw new Error("Snapshot link or special type denied");
        if (path && path !== ".") {
            safePath(path);
            if (seen.has(path)) throw new Error("Duplicate snapshot path");
            seen.add(path);
        }
        if (type !== "5") {
            if (!path || path === ".") throw new Error("Unsafe snapshot path");
            total += size;
            if (total > limit || files.length >= 20000) throw new Error("Snapshot size limit");
            files.push({
                path,
                mode: mode & 0o111 ? 0o770 : 0o660,
                bytes: Buffer.from(tar.subarray(at, at + size)),
            });
        }
        at += Math.ceil(size / 512) * 512;
    }
    return files;
}
export function retainSnapshot(w: Workspace, tar: Buffer): void {
    const files = parseSnapshotTar(tar, w.limit),
        next = join(w.root, `tree-${randomUUID()}`);
    mkdirSync(next, {mode: 0o2770});
    chmodSync(next, 0o2770);
    for (const f of files) {
        const path = join(next, f.path);
        mkdirSync(dirname(path), {recursive: true, mode: 0o2770});
        writeFileSync(path, f.bytes, {mode: f.mode, flag: "wx"});
    }
    w.checkout = next;
    new PrivateStore(w.root).write("workspace.json", w);
}
export async function hashFinalTree(
    w: Workspace,
    guard: () => unknown = () => {},
): Promise<string> {
    const started = Date.now();
    const current = () => {
        guard();
        if (Date.now() - started > 120000) throw new Error("Final tree inspection time limit");
    };
    current();
    const rows: string[] = [];
    let total = 0;
    async function walk(dir: string, prefix = ""): Promise<void> {
        for (const entry of readdirSync(dir, {withFileTypes: true})) {
            current();
            await yieldLoop();
            current();
            const path = prefix + entry.name;
            safePath(path);
            const full = join(dir, entry.name);
            const s = lstatSync(full);
            if (s.isSymbolicLink() || (!s.isDirectory() && !s.isFile()))
                throw new Error("Unsafe final tree type");
            if (s.isDirectory()) {
                await walk(full, path + "/");
                continue;
            }
            if (s.nlink !== 1) throw new Error("Hardlink denied");
            const fd = openSync(full, constants.O_RDONLY | constants.O_NOFOLLOW);
            let bytes: Buffer;
            try {
                const stat = fstatSync(fd);
                total += stat.size;
                if (total > w.limit || rows.length >= 20000)
                    throw new Error("Final tree size limit");
                bytes = readFileSync(fd);
            } finally {
                closeSync(fd);
            }
            const hash = ownGit(w, ["hash-object", "-w", "--stdin"], bytes).toString().trim();
            rows.push(`${s.mode & 0o111 ? "100755" : "100644"} ${hash}\t${path}\0`);
        }
    }
    await walk(w.checkout);
    current();
    ownGit(w, ["read-tree", "--empty"]);
    ownGit(w, ["update-index", "-z", "--index-info"], rows.sort().join(""));
    return ownGit(w, ["write-tree"]).toString().trim();
}
export function finalDiff(w: Workspace, tree: string): Buffer {
    return ownGit(w, [
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        w.record.base_commit,
        tree,
        "--",
    ]);
}

export function createCandidateCommit(
    gitDir: string,
    baseCommit: string,
    tree: string,
    jobId: string,
): string {
    if (
        !/^[0-9a-f]{40}$/.test(baseCommit) ||
        !/^[0-9a-f]{40}$/.test(tree) ||
        !/^[a-zA-Z0-9_-]{1,80}$/.test(jobId)
    )
        throw new Error("Invalid candidate commit input");
    const message = `Grow Agent candidate for ${jobId}\n\nCo-Authored-By: CADIS <agent@cadis.digital>\n`;
    const candidate = execFileSync(
        "/usr/bin/git",
        [
            `--git-dir=${gitDir}`,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "credential.helper=",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.attributesFile=/dev/null",
            "commit-tree",
            tree,
            "-p",
            baseCommit,
        ],
        {
            env: {
                ...env,
                GIT_AUTHOR_NAME: "Grow Agent",
                GIT_AUTHOR_EMAIL: "grow-agent@localhost",
                GIT_COMMITTER_NAME: "Grow Agent",
                GIT_COMMITTER_EMAIL: "grow-agent@localhost",
            },
            input: message,
            encoding: "utf8",
            timeout: 10000,
        },
    ).trim();
    if (!/^[0-9a-f]{40}$/.test(candidate)) throw new Error("Invalid candidate commit");
    return candidate;
}
export function snapshotTree(w: Workspace, tree: string): Buffer {
    if (!/^[0-9a-f]{40}$/.test(tree)) throw new Error("Invalid checkpoint tree");
    // Private Git metadata overrides tracked attributes at every directory depth.
    const info = new PrivateStore(join(w.gitDir, "info"));
    const fd = openSync(
        info.path("attributes"),
        constants.O_WRONLY | constants.O_CREAT | constants.O_NOFOLLOW,
        0o600,
    );
    try {
        const stat = fstatSync(fd);
        if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.getuid!())
            throw new Error("Unsafe archive attributes");
        ftruncateSync(fd, 0);
        writeFileSync(fd, "* -export-ignore -export-subst\n");
    } finally {
        closeSync(fd);
    }
    const tar = ownGit(w, ["archive", "--format=tar", tree]);
    // Reconstruct the archive tree before retaining bytes.
    const rows = parseSnapshotTar(tar, w.limit).map((file) => {
        const hash = ownGit(w, ["hash-object", "-w", "--stdin"], file.bytes).toString().trim();
        return `${file.mode & 0o111 ? "100755" : "100644"} ${hash}\t${file.path}\0`;
    });
    ownGit(w, ["read-tree", "--empty"]);
    ownGit(w, ["update-index", "-z", "--index-info"], rows.sort().join(""));
    if (ownGit(w, ["write-tree"]).toString().trim() !== tree)
        throw new Error("Checkpoint archive tree mismatch");
    return tar;
}
