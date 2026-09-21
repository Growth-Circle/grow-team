import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, mkdirSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {execFileSync} from "node:child_process";
import {randomUUID} from "node:crypto";
import {ToolBroker, ArtifactStore} from "../dist/tool-broker.js";
import {hashFinalTree} from "../dist/workspace.js";
import {OperationBoundary} from "../dist/supervisor.js";
import {Journal} from "../dist/journal.js";
import {digest, parse} from "../dist/protocol.js";

test("read-only shell publishes unchanged server tree before another consumed read", async () => {
    const root = mkdtempSync(join(tmpdir(), "grow-readonly-shell-"));
    mkdirSync(join(root, "tree"));
    writeFileSync(join(root, "tree/a.txt"), "approved");
    execFileSync("/usr/bin/git", ["init", "--quiet", "--bare", "--template=", join(root, "git")]);
    const d = {
        attempt_id: randomUUID(),
        job_id: randomUUID(),
        job_kind: "code",
        lease_epoch: 1,
        repository: {id: randomUUID()},
        policy: {
            actions: ["repository.read", "shell.run"],
            network: {
                targets: [],
                public_https_only: true,
                block_metadata: true,
                cross_origin_authorization: false,
                project_network: false,
            },
        },
        budget: {tool_rounds: 10, shell_timeout_seconds: 10},
    };
    const guard = {
        lease: () => ({attempt_id: d.attempt_id, job_id: d.job_id, lease_epoch: 1}),
        deadline: Date.now() + 60000,
        signal: new AbortController().signal,
    };
    const workspace = {
        root,
        checkout: join(root, "tree"),
        gitDir: join(root, "git"),
        attemptId: d.attempt_id,
        leaseEpoch: 1,
        record: {},
        limit: 33554432,
    };
    const initial = await hashFinalTree(workspace);
    let serverTree = initial,
        checkpoints = 0,
        effects = 0;
    const proposals = new Map(),
        consumed = new Set(),
        published = new Set();
    const j = new Journal(join(root, "journal"));
    const transport = {
        async mutate(kind, id, route, body) {
            if (kind === "proposal") {
                parse("operation_arguments", body.arguments);
                assert.equal(body.tree_hash, serverTree, "Operation workspace is unavailable.");
                const operation = {
                    operation_id: body.operation_id,
                    version: 1,
                    operation_hash: digest(body),
                    status: "authorized",
                };
                proposals.set(body.operation_id, operation);
                return {operation};
            }
            assert.equal(kind, "consume");
            consumed.add(body.operation_id);
            return {
                operation: {...proposals.get(body.operation_id), status: "started", version: 2},
            };
        },
    };
    const channel = {
        lease: guard.lease,
        operations: new OperationBoundary(j, transport as any, guard.lease),
        async event(type, payload) {
            assert(consumed.has(payload.operation_id));
            assert.equal(
                payload.argument_digest,
                proposals.get(payload.operation_id).operation_hash,
            );
            if (
                type === "tool.finished" &&
                ["repository.edit", "shell.run", "dependencies.install"].includes(
                    payload.tool_class,
                )
            )
                serverTree = "";
        },
    };
    const sandbox = {
        async runSandboxedTool(d, g, w, argv, options) {
            effects++;
            assert.equal(options.write, false, "narrowed policy must retain read-only mount");
            return {
                exitCode: 0,
                output: Buffer.from("approved"),
                containerId: "b".repeat(64),
                stopConfirmed: true,
                overflow: false,
                timedOut: false,
            };
        },
    };
    const persistence = {
        async upload(a) {
            const id = randomUUID();
            assert.notEqual(id, a.record.id);
            published.add(id);
            return {artifact_id: id, checksum: a.record.checksum, size: a.record.size};
        },
        async publishTree(w, tree, ids) {
            assert.equal(serverTree, "");
            assert.equal(tree, initial);
            assert(ids.every((id) => published.has(id)));
            checkpoints++;
            serverTree = tree;
        },
    };
    const broker = new ToolBroker(
        d,
        guard,
        workspace,
        sandbox as any,
        channel,
        j,
        new ArtifactStore(join(root, "artifacts"), 65536, 1048576),
        persistence,
    );
    try {
        await broker.runSandboxedTool(randomUUID(), {
            kind: "shell",
            argv: ["node", "--version"],
            cwd: ".",
        });
        assert.equal(checkpoints, 1);
        assert.equal(serverTree, initial);
        await broker.runSandboxedTool(randomUUID(), {kind: "read", path: "a.txt"});
        assert.equal(effects, 2);
        assert.equal(consumed.size, 2);
        assert.equal(checkpoints, 1);
    } finally {
        j.close();
    }
});
