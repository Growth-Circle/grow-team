import {SecretFilter} from "./redaction.js";
import {setTimeout as sleep} from "node:timers/promises";
import {createHash, randomUUID} from "node:crypto";
import {openSync, writeFileSync, fsyncSync, closeSync, readdirSync} from "node:fs";
import {PrivateStore} from "./config.js";
import {type Data, canonical, digest, parse} from "./protocol.js";
import type {JournalLog} from "./journal.js";
import type {AttemptChannel} from "./supervisor.js";
import {RootlessSandbox, type ExecutionGuard, type SandboxResult} from "./sandbox.js";
import {safePath, hashFinalTree, finalDiff, assertLease, type Workspace} from "./workspace.js";
export type ToolRequest =
    | {kind: "read"; path: string}
    | {kind: "search"; path: string; query: string}
    | {kind: "edit"; path: string; content: string}
    | {kind: "shell"; argv: string[]; cwd: string};
export function validateTool(value: unknown): ToolRequest {
    if (!value || typeof value !== "object") throw new Error("Invalid tool");
    const t = value as Data;
    const keys: Record<string, string[]> = {
        read: ["kind", "path"],
        search: ["kind", "path", "query"],
        edit: ["kind", "path", "content"],
        shell: ["kind", "argv", "cwd"],
    };
    if (!keys[t.kind] || Object.keys(t).some((k) => !keys[t.kind]!.includes(k)))
        throw new Error("Unknown tool or field");
    if (t.kind === "shell") {
        if (
            !Array.isArray(t.argv) ||
            !t.argv.length ||
            t.argv.length > 64 ||
            t.argv.some(
                (a: unknown) => typeof a !== "string" || !a || a.length > 4096 || a.includes("\0"),
            )
        )
            throw new Error("Invalid argv");
        if (t.cwd !== ".") safePath(t.cwd);
    } else {
        if (t.kind !== "search" || t.path !== ".") safePath(t.path);
        if (
            t.kind === "search" &&
            (typeof t.query !== "string" || !t.query || t.query.length > 1000)
        )
            throw new Error("Invalid search");
        if (
            t.kind === "edit" &&
            (typeof t.content !== "string" || Buffer.byteLength(t.content) > 51200)
        )
            throw new Error("Edit size limit");
    }
    return structuredClone(t) as ToolRequest;
}
export interface Artifact {
    record: Data;
    path: string;
}
export class ArtifactStore {
    private store: PrivateStore;
    constructor(
        root: string,
        private perFile: number,
        private perJob: number,
        private filter = new SecretFilter(),
    ) {
        this.store = new PrivateStore(root);
    }
    bind(artifact: Artifact, receipt: ArtifactUploadReceipt): UploadedArtifact {
        if (
            !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
                receipt.artifact_id,
            ) ||
            receipt.checksum !== artifact.record.checksum ||
            receipt.size !== artifact.record.size
        )
            throw new Error("Invalid artifact upload receipt");
        this.store.write(`upload-${artifact.record.id}.json`, {
            local_artifact_id: artifact.record.id,
            ...receipt,
        });
        return {...artifact, serverId: receipt.artifact_id};
    }
    retain(jobId: string, attemptId: string, kind: string, bytes: Buffer): Artifact {
        bytes = this.filter.bytes(bytes);
        const total = readdirSync(this.store.root)
            .filter((x) => x.endsWith(".json"))
            .reduce((sum, name) => {
                const a = this.store.read(name);
                return sum + (a?.job_id === jobId ? a.size : 0);
            }, 0);
        if (bytes.length > this.perFile || bytes.length + total > this.perJob)
            throw new Error("Artifact size limit");
        const id = randomUUID(),
            name = `${id}.bin`,
            fd = openSync(this.store.path(name), "wx", 0o600);
        try {
            writeFileSync(fd, bytes);
            fsyncSync(fd);
        } finally {
            closeSync(fd);
        }
        const record = {
            id,
            attempt_id: attemptId,
            kind,
            checksum: createHash("sha256").update(bytes).digest("hex"),
            size: bytes.length,
            media_type: kind === "diff" ? "text/x-diff" : "text/plain",
            filename: `${kind}-${id}.txt`,
            expires_at: new Date(Date.now() + 7 * 86400000).toISOString(),
        };
        this.store.write(`${id}.json`, {...record, job_id: jobId});
        return {record, path: this.store.path(name)};
    }
}
export interface ArtifactUploadReceipt {
    artifact_id: string;
    checksum: string;
    size: number;
}
export interface UploadedArtifact extends Artifact {
    serverId: string;
}
export interface BrokerPersistence {
    upload(artifact: Artifact): Promise<ArtifactUploadReceipt>;
    // Save a server checkpoint with this tree before subsequent operation proposals.
    publishTree(workspace: Workspace, tree: string, artifactIds: string[]): Promise<void>;
}
export class ToolBroker {
    private d: Data;
    private busy = false;
    private verifiedTree: string | null = null;
    constructor(
        descriptor: Data,
        private guard: ExecutionGuard,
        private workspace: Workspace,
        private sandbox: RootlessSandbox,
        private channel: AttemptChannel,
        private journal: JournalLog,
        private artifacts: ArtifactStore,
        private persistence: BrokerPersistence,
        private filter = new SecretFilter(),
    ) {
        this.d = structuredClone(descriptor);
    }
    private current(): Data {
        if (this.guard.signal.aborted || Date.now() >= this.guard.deadline)
            throw new Error("Tool authority expired or cancelled");
        return assertLease(this.d, this.guard.lease);
    }
    private async artifact(kind: string, bytes: Buffer): Promise<UploadedArtifact> {
        const a = this.artifacts.retain(this.d.job_id, this.d.attempt_id, kind, bytes);
        this.current();
        const receipt = await this.persistence.upload(a);
        const uploaded = this.artifacts.bind(a, receipt);
        this.current();
        return uploaded;
    }
    private async authority(id: string, args: Data, tree: string): Promise<Data> {
        const intentId = `tool-intent:${this.d.attempt_id}:${id}`;
        if (this.journal.get(intentId))
            throw new Error("Tool ID already used; reconcile prior receipt");
        if (
            this.journal
                .list("tool-intent")
                .filter((e) => e.request.attempt_id === this.d.attempt_id).length >=
            this.d.budget.tool_rounds
        )
            throw new Error("Tool round budget exhausted");
        this.journal.prepare("tool-intent", intentId, "local", {
            attempt_id: this.d.attempt_id,
            epoch: this.d.lease_epoch,
            operation_id: id,
            argument_digest: digest(args),
            tree_hash: tree,
            arguments: args,
        });
        let proposal = await this.channel.operations.propose(this.current(), id, args, {
            tree_hash: tree,
        });
        while (proposal.status === "proposed") {
            this.current();
            await sleep(500, undefined, {signal: this.guard.signal});
            const response = await this.channel.operations.reconcile(this.current());
            const updated = response.operations.find((op: Data) => op.operation_id === id);
            if (!updated || updated.operation_hash !== proposal.operation_hash)
                throw new Error("Approval identity changed");
            proposal = updated;
            // An approved proposal remains proposed. Consume uses its current nonce and version.
            if (!this.channel.request) throw new Error("Approval control channel unavailable");
            const controls = await this.channel.request("/runner/controls");
            const control = controls.controls.find(
                (c: Data) =>
                    c.attempt_id === this.d.attempt_id && c.lease_epoch === this.d.lease_epoch,
            );
            const approval = control?.approvals.find(
                (a: Data) =>
                    a.operation_id === id &&
                    a.id === proposal.approval_id &&
                    a.nonce === proposal.nonce,
            );
            if (approval?.decision === "approved") break;
            if (approval && approval.decision !== "pending")
                throw new Error("Operation approval rejected");
        }
        if (!["authorized", "proposed"].includes(proposal.status))
            throw new Error("Operation is not authorized");
        const operation = await this.channel.operations.consume(this.current(), proposal);
        this.current();
        this.channel.operations.beginEffect(id);
        await this.channel.event("tool.started", {
            operation_id: id,
            tool_class: args.action,
            argument_digest: operation.operation_hash,
            status: "started",
            artifact_id: null,
            exit_code: null,
            summary: "",
        });
        this.current();
        return operation;
    }
    async runSandboxedTool(
        id: string,
        request: unknown,
    ): Promise<{result: SandboxResult; artifact: Data; tree: string}> {
        if (!/^[0-9a-f-]{36}$/.test(id)) throw new Error("Durable UUID tool ID required");
        if (this.busy) throw new Error("Concurrent tools denied");
        this.busy = true;
        try {
            this.filter.assertArguments(request);
            const tool = validateTool(request),
                tree = await hashFinalTree(this.workspace, () => this.current());
            this.current();
            let args: Data,
                argv: string[],
                write = false;
            if (tool.kind === "shell") {
                args = {
                    action: "shell.run",
                    repository_id: this.d.repository.id,
                    argv: tool.argv,
                    cwd: tool.cwd,
                    network: this.d.policy.network,
                };
                argv = tool.argv;
                write = this.d.policy.actions.includes("repository.edit");
            } else if (tool.kind === "edit") {
                const patch = await this.artifact("file", Buffer.from(canonical(tool)));
                args = {
                    action: "repository.edit",
                    repository_id: this.d.repository.id,
                    patch_artifact_id: patch.serverId,
                    patch_checksum: patch.record.checksum,
                    expected_tree: tree,
                };
                argv = [
                    "node",
                    "/grow/worker.mjs",
                    "edit",
                    Buffer.from(
                        JSON.stringify({
                            path: tool.path,
                            content: Buffer.from(tool.content).toString("base64"),
                        }),
                    ).toString("base64"),
                ];
                write = true;
            } else {
                args = {
                    action: "repository.read",
                    repository_id: this.d.repository.id,
                    paths: [tool.path],
                };
                argv = [
                    "node",
                    "/grow/worker.mjs",
                    tool.kind,
                    Buffer.from(JSON.stringify(tool)).toString("base64"),
                ];
            }
            if (
                !this.d.policy.actions.includes(args.action) ||
                (this.d.job_kind === "answer" && args.action !== "repository.read")
            )
                throw new Error("Tool exceeds attempt policy");
            const operation = await this.authority(id, args, tree);
            this.verifiedTree = null;
            const result = await this.sandbox.runSandboxedTool(
                this.d,
                this.guard,
                this.workspace,
                argv,
                {
                    write,
                    timeoutMs: this.d.budget.shell_timeout_seconds * 1000,
                    cwd: tool.kind === "shell" ? tool.cwd : ".",
                },
            );
            result.output = this.filter.bytes(result.output);
            const after = await hashFinalTree(this.workspace, () => this.current()),
                artifact = await this.artifact("log", result.output);
            const receipt = {
                operation_id: id,
                argument_digest: operation.operation_hash,
                tree_hash: after,
                container_id: result.containerId,
                exit_code: result.exitCode,
                artifact_id: artifact.serverId,
                stop_confirmed: result.stopConfirmed,
            };
            this.channel.operations.finishEffect(id, receipt);
            this.current();
            await this.channel.event("tool.finished", {
                operation_id: id,
                tool_class: args.action,
                argument_digest: operation.operation_hash,
                status:
                    result.exitCode === 0 && !result.timedOut && !result.overflow
                        ? "succeeded"
                        : "failed",
                artifact_id: artifact.serverId,
                exit_code: result.exitCode,
                summary: result.overflow
                    ? "Output limit exceeded"
                    : result.timedOut
                      ? "Tool timed out"
                      : "",
            });
            if (["repository.edit", "shell.run", "dependencies.install"].includes(args.action))
                await this.persistence.publishTree(this.workspace, after, [artifact.serverId]);
            this.current();
            return {result, artifact: {...artifact.record, id: artifact.serverId}, tree: after};
        } finally {
            this.busy = false;
        }
    }
    async verifyFinalTree(): Promise<{tree: string; passed: boolean; records: Data[]; diff: Data}> {
        if (this.busy) throw new Error("Concurrent verification denied");
        this.busy = true;
        try {
            this.current();
            if (!this.d.policy.actions.includes("checks.run")) throw new Error("Checks denied");
            const tree = await hashFinalTree(this.workspace, () => this.current()),
                diff = await this.artifact("diff", finalDiff(this.workspace, tree));
            await this.persistence.publishTree(this.workspace, tree, [diff.serverId]);
            this.current();
            const records: Data[] = [];
            let allChecksPassed = true;
            for (const check of this.d.repository.required_checks) {
                const id = randomUUID(),
                    args = {
                        action: "checks.run",
                        repository_id: this.d.repository.id,
                        check_ids: [check.id],
                        tree_hash: tree,
                    };
                const operation = await this.authority(id, args, tree);
                const start = new Date().toISOString();
                const result = await this.sandbox.runSandboxedTool(
                    this.d,
                    this.guard,
                    this.workspace,
                    check.argv,
                    {write: false, timeoutMs: check.timeout_seconds * 1000, cwd: check.cwd},
                );
                const artifact = await this.artifact("verification", result.output);
                if ((await hashFinalTree(this.workspace, () => this.current())) !== tree)
                    throw new Error("Final tree changed during verification");
                const record = parse("verification", {
                    operation_id: id,
                    check_id: check.id,
                    command: check.argv,
                    cwd: check.cwd,
                    exit_code: result.exitCode,
                    started_at: start,
                    finished_at: new Date().toISOString(),
                    tree_hash: tree,
                    artifact_id: artifact.serverId,
                    timed_out: result.timedOut,
                });
                this.channel.operations.finishEffect(id, {
                    ...record,
                    container_id: result.containerId,
                    stop_confirmed: result.stopConfirmed,
                });
                this.current();
                await this.channel.event("verification.finished", record);
                await this.channel.event("tool.finished", {
                    operation_id: id,
                    tool_class: "checks.run",
                    argument_digest: operation.operation_hash,
                    status: result.exitCode === 0 && !result.overflow ? "succeeded" : "failed",
                    artifact_id: artifact.serverId,
                    exit_code: result.exitCode,
                    summary: result.overflow ? "Output limit exceeded" : "",
                });
                allChecksPassed =
                    allChecksPassed &&
                    result.exitCode === 0 &&
                    !result.timedOut &&
                    !result.overflow;
                records.push(record);
            }
            this.current();
            if ((await hashFinalTree(this.workspace, () => this.current())) !== tree)
                throw new Error("Final tree changed");
            const passed =
                allChecksPassed && records.every((r) => r.exit_code === 0 && !r.timed_out);
            this.verifiedTree = passed ? tree : null;
            return {tree, passed, records, diff: {...diff.record, id: diff.serverId}};
        } finally {
            this.busy = false;
        }
    }
    async assertVerified(): Promise<string> {
        this.current();
        if (
            !this.verifiedTree ||
            (await hashFinalTree(this.workspace, () => this.current())) !== this.verifiedTree
        )
            throw new Error("Verification invalidated by a later edit");
        return this.verifiedTree;
    }
}
