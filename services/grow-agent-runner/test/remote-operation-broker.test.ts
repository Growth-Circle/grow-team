import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, mkdirSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {execFileSync} from "node:child_process";
import {createServer} from "node:http";
import {
    GitHubDraftProvider,
    RemoteGitBroker,
    RemoteOperationBroker,
} from "../dist/remote-operation-broker.js";
import {createCandidateCommit} from "../dist/workspace.js";
import {Journal} from "../dist/journal.js";

function git(cwd: string, ...args: string[]): string {
    return execFileSync("/usr/bin/git", args, {
        cwd,
        encoding: "utf8",
        env: {
            PATH: "/usr/bin:/bin",
            HOME: "/nonexistent",
            GIT_CONFIG_GLOBAL: "/dev/null",
            GIT_CONFIG_NOSYSTEM: "1",
        },
    }).trim();
}

function commit(source: string, value: string): string {
    writeFileSync(join(source, "file.txt"), value);
    git(source, "add", "file.txt");
    git(
        source,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        `fixture\n\nCo-Authored-By: CADIS <agent@cadis.digital>`,
    );
    return git(source, "rev-parse", "HEAD");
}

test("remote Git broker rejects a stale expected head before it can replace a ref", async () => {
    const root = mkdtempSync(join(tmpdir(), "grow-remote-broker-"));
    const remote = join(root, "remote.git"),
        source = join(root, "source");
    mkdirSync(source);
    git(root, "init", "--bare", "remote.git");
    git(source, "init", "-q");
    git(source, "remote", "add", "origin", remote);
    const first = commit(source, "one\n");
    git(source, "push", "origin", `${first}:refs/heads/grow-agent/job/attempt`);
    const second = commit(source, "two\n");
    const broker = new RemoteGitBroker();
    await broker.push({
        remote,
        branch: "grow-agent/job/attempt",
        commit: second,
        expected_remote_head: first,
        credential: null,
        git_dir: join(source, ".git"),
    });
    const third = commit(source, "three\n");
    await assert.rejects(
        () =>
            broker.push({
                remote,
                branch: "grow-agent/job/attempt",
                commit: third,
                expected_remote_head: first,
                credential: null,
                git_dir: join(source, ".git"),
            }),
        /expected remote head/i,
    );
    assert.equal(
        git(root, "--git-dir=remote.git", "rev-parse", "refs/heads/grow-agent/job/attempt"),
        second,
    );
    const privateGit = join(root, "private.git");
    git(root, "init", "--bare", "private.git");
    git(root, `--git-dir=${privateGit}`, "fetch", "--no-tags", source, `${first}:refs/heads/base`);
    await broker.importExpectedHead({
        remote,
        branch: "grow-agent/job/attempt",
        expected_remote_head: second,
        credential: null,
        git_dir: privateGit,
        base_commit: first,
    });
    const candidate = createCandidateCommit(
        privateGit,
        second,
        git(root, `--git-dir=${privateGit}`, "rev-parse", `${first}^{tree}`),
        "job-1",
    );
    await broker.push({
        remote,
        branch: "grow-agent/job/attempt",
        commit: candidate,
        expected_remote_head: second,
        credential: null,
        git_dir: privateGit,
    });
    assert.equal(git(root, `--git-dir=${privateGit}`, "rev-parse", `${candidate}^`), second);
    assert.equal(
        git(root, "--git-dir=remote.git", "rev-parse", "refs/heads/grow-agent/job/attempt"),
        candidate,
    );
});

test("candidate commit binds the verified tree and carries the CADIS trailer", () => {
    const root = mkdtempSync(join(tmpdir(), "grow-candidate-"));
    git(root, "init", "-q");
    const base = commit(root, "before\n");
    writeFileSync(join(root, "file.txt"), "after\n");
    git(root, "add", "file.txt");
    const tree = git(root, "write-tree");
    const candidate = createCandidateCommit(join(root, ".git"), base, tree, "job-1");
    assert.equal(git(root, "rev-parse", `${candidate}^{tree}`), tree);
    assert.equal(git(root, "rev-parse", `${candidate}^`), base);
    assert.match(
        git(root, "show", "-s", "--format=%B", candidate),
        /Co-Authored-By: CADIS <agent@cadis\.digital>/,
    );
});

test("draft provider reconciles a lost create acknowledgement without a duplicate post", async () => {
    const requests: any[] = [];
    const expected = {
        base: "main",
        head: "grow-agent/job/attempt",
        commit: "a".repeat(40),
        title: "Approved title",
        body: "Approved body\n\n<!-- grow-agent-operation:op -->",
        draft: true,
    };
    const item = {
        id: 42,
        html_url: "https://github.example/org/repo/pull/42",
        ...expected,
        base: {ref: expected.base},
        head: {ref: expected.head, sha: expected.commit},
    };
    const server = createServer(async (request, response) => {
        let text = "";
        for await (const chunk of request) text += chunk;
        requests.push({
            method: request.method,
            url: request.url,
            headers: request.headers,
            body: text,
        });
        assert.equal(request.headers["x-github-api-version"], "2026-03-10");
        if (request.method === "POST") {
            assert.deepEqual(JSON.parse(text), {
                title: expected.title,
                body: expected.body,
                head: expected.head,
                base: expected.base,
                draft: true,
            });
            response.destroy();
            return;
        }
        response.writeHead(200, {"Content-Type": "application/json"}).end(JSON.stringify([item]));
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
        const provider = new GitHubDraftProvider({
            api_base: `http://127.0.0.1:${(server.address() as any).port}`,
            credential: "fixture-token",
        });
        assert.deepEqual(await provider.create("https://github.example/org/repo.git", expected), {
            id: "42",
            url: "https://github.example/org/repo/pull/42",
        });
        assert.equal(requests.filter((request) => request.method === "POST").length, 1);
        assert.equal(requests.filter((request) => request.method === "GET").length, 1);
    } finally {
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});

test("publication creates exact approved push and draft operations before external effects", async () => {
    const operations: any[] = [],
        receipts: any[] = [],
        effects: string[] = [];
    const server = createServer(async (request, response) => {
        let text = "";
        for await (const chunk of request) text += chunk;
        const body = JSON.parse(text);
        response.writeHead(201, {"Content-Type": "application/json"}).end(
            JSON.stringify({
                id: 44,
                html_url: "https://github.example/org/repo/pull/44",
                title: body.title,
                body: body.body,
                draft: true,
                base: {ref: body.base},
                head: {ref: body.head, sha: "b".repeat(40)},
            }),
        );
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
        const git: any = {
            head: async () => null,
            push: async (request: any) => effects.push(`push:${request.commit}`),
        };
        const publisher = new RemoteOperationBroker(
            () => ({
                remote: "https://github.example/org/repo.git",
                git_credential: null,
                github: {
                    api_base: `http://127.0.0.1:${(server.address() as any).port}`,
                    credential: "token",
                },
            }),
            git,
        );
        const channel: any = {
            lease: () => ({job_id: "job", attempt_id: "attempt", lease_epoch: 1}),
            event: async () => {},
            request: async () => ({
                controls: [
                    {
                        attempt_id: "attempt",
                        lease_epoch: 1,
                        approvals: operations.map((operation) => ({
                            operation_id: operation.operation_id,
                            nonce: operation.nonce,
                            decision: "approved",
                        })),
                    },
                ],
            }),
            operations: {
                propose: async (_lease: any, operation_id: string, args: any, extras: any) => {
                    const operation = {
                        operation_id,
                        operation_hash: `hash-${operations.length}`,
                        version: 1,
                        nonce: `nonce-${operations.length}`,
                        status: "proposed",
                        ...args,
                        extras,
                    };
                    operations.push(operation);
                    return operation;
                },
                consume: async (_lease: any, operation: any) => ({
                    ...operation,
                    status: "started",
                    version: 2,
                    tool_class: operation.action,
                }),
                beginEffect: (id: string) => effects.push(`begin:${id}`),
                remoteReceipt: async (_lease: any, receipt: any) => receipts.push(receipt),
                finishEffect: (id: string) => effects.push(`finish:${id}`),
                reconcile: async () => ({operations}),
            },
        };
        await publisher.publish(
            {
                delivery_target: "draft_pr",
                job_id: "job",
                attempt_id: "attempt",
                request: "Implement the approved change",
                base_ref: "main",
                repository: {id: "repo", canonical_origin: "https://github.example/org/repo.git"},
            },
            {
                candidateCommit: async () => ({
                    tree: "a".repeat(40),
                    commit: "b".repeat(40),
                    git_dir: "/private/git",
                    base_commit: "c".repeat(40),
                }),
            },
            {verification: {passed: true, diff: {id: "diff-id"}}},
            channel,
        );
        assert.deepEqual(
            operations.map((operation) => operation.action),
            ["git.push", "git.draft_pr"],
        );
        assert.equal(operations[0].commit, "b".repeat(40));
        assert.equal(operations[0].extras.diff_artifact_id, "diff-id");
        assert.equal(receipts.length, 2);
        assert.equal(receipts[1].pull_request_id, "44");
        assert.equal(effects.filter((item) => item.startsWith("begin:")).length, 2);
    } finally {
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});

test("accepted input before a remote approval effect leaves the stale candidate unpublished", async () => {
    const operations: any[] = [],
        effects: string[] = [];
    let polls = 0,
        pending = false;
    const git: any = {
        head: async () => null,
        push: async () => effects.push("push"),
    };
    const publisher = new RemoteOperationBroker(
        () => ({
            remote: "https://github.example/org/repo.git",
            git_credential: null,
            github: {api_base: "http://127.0.0.1:1", credential: "token"},
        }),
        git,
    );
    const channel: any = {
        lease: () => ({job_id: "job", attempt_id: "attempt", lease_epoch: 1}),
        event: async () => {},
        pollInputs: async () => {
            polls++;
            if (polls >= 5) pending = true;
        },
        hasPendingInput: () => pending,
        request: async () => ({
            controls: [
                {
                    attempt_id: "attempt",
                    lease_epoch: 1,
                    approvals: operations.map((operation) => ({
                        operation_id: operation.operation_id,
                        nonce: operation.nonce,
                        decision: "approved",
                    })),
                },
            ],
        }),
        operations: {
            propose: async (_lease: any, operation_id: string, args: any, extras: any) => {
                const operation = {
                    operation_id,
                    operation_hash: "hash",
                    version: 1,
                    nonce: "nonce",
                    status: "proposed",
                    ...args,
                    extras,
                };
                operations.push(operation);
                return operation;
            },
            consume: async () => {
                effects.push("consume");
                return {};
            },
            beginEffect: () => effects.push("begin"),
            remoteReceipt: async () => {},
            finishEffect: () => {},
            reconcile: async () => ({operations}),
        },
    };
    assert.equal(
        await publisher.publish(
            {
                delivery_target: "draft_pr",
                job_id: "job",
                attempt_id: "attempt",
                request: "Apply accepted input",
                base_ref: "main",
                repository: {id: "repo", canonical_origin: "https://github.example/org/repo.git"},
            },
            {
                candidateCommit: async () => ({
                    tree: "a".repeat(40),
                    commit: "b".repeat(40),
                    git_dir: "/private/git",
                    base_commit: "c".repeat(40),
                }),
            },
            {verification: {passed: true, diff: {id: "diff-id"}}},
            channel,
        ),
        false,
    );
    assert.equal(operations.length, 1);
    assert.deepEqual(effects, []);
});

test("a later candidate extends a reconciled push after input defers draft PR approval", async () => {
    const server = createServer(async (request, response) => {
        let text = "";
        for await (const chunk of request) text += chunk;
        const body = JSON.parse(text);
        response.writeHead(201, {"Content-Type": "application/json"}).end(
            JSON.stringify({
                id: 9,
                html_url: "https://github.example/org/repo/pull/9",
                title: body.title,
                body: body.body,
                draft: true,
                base: {ref: body.base},
                head: {ref: body.head, sha: "c".repeat(40)},
            }),
        );
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
        const first = "b".repeat(40),
            second = "c".repeat(40),
            base = "a".repeat(40);
        let remoteHead: string | null = null,
            deferDraft = true;
        const pushed: any[] = [],
            parents: Array<string | null> = [],
            imported: any[] = [],
            operations: any[] = [];
        const git: any = {
            head: async () => remoteHead,
            importExpectedHead: async (value: any) => imported.push(value),
            push: async (value: any) => {
                pushed.push(value);
                remoteHead = value.commit;
            },
        };
        const publisher = new RemoteOperationBroker(
            () => ({
                remote: "https://github.example/org/repo.git",
                git_credential: null,
                github: {
                    api_base: `http://127.0.0.1:${(server.address() as any).port}`,
                    credential: "token",
                },
            }),
            git,
        );
        const channel: any = {
            lease: () => ({job_id: "job", attempt_id: "attempt", lease_epoch: 1}),
            event: async () => {},
            pollInputs: async () => {},
            hasPendingInput: () => deferDraft && remoteHead === first,
            request: async () => ({
                controls: [
                    {
                        attempt_id: "attempt",
                        lease_epoch: 1,
                        approvals: operations.map((operation) => ({
                            operation_id: operation.operation_id,
                            nonce: operation.nonce,
                            decision: "approved",
                        })),
                    },
                ],
            }),
            operations: {
                propose: async (_lease: any, operation_id: string, args: any, extras: any) => {
                    const operation = {
                        operation_id,
                        operation_hash: `hash-${operations.length}`,
                        nonce: `nonce-${operations.length}`,
                        status: "proposed",
                        ...args,
                        extras,
                    };
                    operations.push(operation);
                    return operation;
                },
                consume: async (_lease: any, operation: any) => ({
                    ...operation,
                    tool_class: operation.action,
                }),
                beginEffect: () => {},
                remoteReceipt: async () => {},
                finishEffect: () => {},
                reconcile: async () => ({operations}),
            },
        };
        const broker = {
            candidateCommit: async (parent?: string) => {
                parents.push(parent ?? null);
                return {
                    tree: parent ? "d".repeat(40) : "e".repeat(40),
                    commit: parent ? second : first,
                    git_dir: "/private/git",
                    base_commit: base,
                };
            },
        };
        const descriptor = {
            delivery_target: "draft_pr",
            job_id: "job",
            attempt_id: "attempt",
            request: "Apply the accepted input",
            base_ref: "main",
            repository: {id: "repo", canonical_origin: "https://github.example/org/repo.git"},
        };
        assert.equal(
            await publisher.publish(
                descriptor,
                broker,
                {verification: {passed: true, diff: {id: "first-diff"}}},
                channel,
            ),
            false,
        );
        assert.deepEqual(
            pushed.map((value) => value.commit),
            [first],
        );
        deferDraft = false;
        assert.equal(
            await publisher.publish(
                descriptor,
                broker,
                {verification: {passed: true, diff: {id: "second-diff"}}},
                channel,
            ),
            true,
        );
        assert.deepEqual(imported, [
            {
                remote: "https://github.example/org/repo.git",
                branch: "grow-agent/job/attempt",
                expected_remote_head: first,
                credential: null,
                git_dir: "/private/git",
                base_commit: base,
            },
        ]);
        assert.deepEqual(parents, [null, null, first]);
        assert.deepEqual(
            pushed.map((value) => ({commit: value.commit, expected: value.expected_remote_head})),
            [
                {commit: first, expected: null},
                {commit: second, expected: first},
            ],
        );
    } finally {
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});

test("restart recovery observes the remote branch before it submits the retained receipt", async () => {
    const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-remote-recovery-"))).partition(
        "runner:fixture",
    );
    const operation = {
        operation_id: "op-1",
        tool_class: "git.push",
        arguments: {
            remote: "https://github.example/org/repo.git",
            branch: "grow-agent/job/attempt",
            commit: "c".repeat(40),
        },
    };
    journal.prepare("authority", "authority:op-1", "local", {
        lease: {attempt_id: "attempt-1"},
        operation,
    });
    journal.prepare("effect", "effect:op-1", "local", {operation});
    journal.uncertain("effect:op-1");
    const receipts: any[] = [];
    const publisher = new RemoteOperationBroker(
        () => null,
        {head: async () => "c".repeat(40)} as any,
        journal,
        () => ({remote: "https://github.example/org/repo.git", git_credential: null, github: null}),
    );
    await publisher.recover((attemptId) => ({
        remoteReceipt: async (receipt) => {
            receipts.push({attemptId, receipt});
            return {};
        },
    }));
    assert.equal(receipts[0].attemptId, "attempt-1");
    assert.equal(receipts[0].receipt.commit, "c".repeat(40));
});
