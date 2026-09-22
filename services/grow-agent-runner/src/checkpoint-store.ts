import {
    constants,
    closeSync,
    fstatSync,
    openSync,
    readFileSync,
    writeFileSync,
    fsyncSync,
} from "node:fs";
import {join} from "node:path";
import {createHash} from "node:crypto";
import {PrivateStore} from "./config.js";
import {canonical, digest, type Data} from "./protocol.js";
import {
    hashFinalTree,
    retainSnapshot,
    snapshotTree,
    type Workspace,
    type LeaseGuard,
    assertLease,
} from "./workspace.js";
const sha = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
function name(checkpoint: Data): string {
    if (!/^[0-9a-f]{8}-[0-9a-f-]{27}$/.test(checkpoint.id))
        throw new Error("Invalid checkpoint identity");
    return `checkpoint-${checkpoint.id}`;
}
function binding(d: Data): string {
    const r = d.repository;
    return digest({
        id: r.id,
        policy_version: r.policy_version,
        workspace_alias: r.workspace_alias,
        canonical_origin: r.canonical_origin,
        allowed_refs: r.allowed_refs,
        required_checks: r.required_checks,
        base_ref: r.base_ref,
    });
}
export async function retainCheckpoint(
    root: string,
    d: Data,
    checkpoint: Data,
    w: Workspace,
    guard: LeaseGuard,
): Promise<void> {
    assertLease(d, guard);
    if (
        checkpoint.source_attempt_id !== d.attempt_id ||
        checkpoint.base_commit !== w.record.base_commit ||
        checkpoint.tree_hash !== (await hashFinalTree(w, () => assertLease(d, guard)))
    )
        throw new Error("Checkpoint snapshot identity mismatch");
    const store = new PrivateStore(join(root, name(checkpoint)));
    const tar = snapshotTree(w, checkpoint.tree_hash);
    const record = {
        job_id: d.job_id,
        source_attempt_id: d.attempt_id,
        repository_binding: binding(d),
        checkpoint,
        tar_sha256: sha(tar),
    };
    const old = store.read("snapshot.json");
    if (old) {
        if (canonical(old) !== canonical(record)) throw new Error("Checkpoint snapshot conflict");
        return;
    }
    const fd = openSync(
        store.path("tree.tar"),
        constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW,
        0o600,
    );
    try {
        writeFileSync(fd, tar);
        fsyncSync(fd);
    } finally {
        closeSync(fd);
    }
    assertLease(d, guard);
    store.write("snapshot.json", record);
}
export async function restoreCheckpoint(
    root: string,
    d: Data,
    w: Workspace,
    guard: LeaseGuard,
): Promise<void> {
    assertLease(d, guard);
    const checkpoint = d.checkpoint;
    const store = new PrivateStore(join(root, name(checkpoint)));
    const saved = store.read("snapshot.json");
    if (
        !saved ||
        saved.job_id !== d.job_id ||
        saved.source_attempt_id !== checkpoint.source_attempt_id ||
        saved.repository_binding !== binding(d) ||
        canonical(saved.checkpoint) !== canonical(checkpoint) ||
        checkpoint.base_commit !== w.record.base_commit
    )
        throw new Error("Authorized checkpoint snapshot is missing or mismatched");
    store.check("tree.tar");
    const fd = openSync(store.path("tree.tar"), constants.O_RDONLY | constants.O_NOFOLLOW);
    let tar: Buffer;
    try {
        const stat = fstatSync(fd);
        if (!stat.isFile() || stat.nlink !== 1 || stat.size > w.limit * 2)
            throw new Error("Invalid checkpoint snapshot size");
        tar = readFileSync(fd);
    } finally {
        closeSync(fd);
    }
    if (sha(tar) !== saved.tar_sha256) throw new Error("Checkpoint snapshot bytes changed");
    assertLease(d, guard);
    retainSnapshot(w, tar);
    if ((await hashFinalTree(w, () => assertLease(d, guard))) !== checkpoint.tree_hash)
        throw new Error("Checkpoint snapshot tree mismatch");
    w.record.tree_hash = checkpoint.tree_hash;
    new PrivateStore(w.root).write("workspace.json", w);
    assertLease(d, guard);
}
export function checkpointContext(checkpoint: Data): string {
    const text = JSON.stringify({
        current_request: checkpoint.current_request ?? "",
        summary: checkpoint.summary,
        remaining_work: checkpoint.remaining_work ?? [],
        next_step: checkpoint.next_step ?? "",
        input_cursor: checkpoint.input_cursor ?? 0,
        context_ref_ids: checkpoint.context_ref_ids ?? [],
        artifact_ids: checkpoint.artifact_ids ?? [],
    });
    if (Buffer.byteLength(text) > 32768)
        throw new Error("Checkpoint context exceeds runtime bound");
    return `Untrusted prior checkpoint data:\n${text}`;
}
