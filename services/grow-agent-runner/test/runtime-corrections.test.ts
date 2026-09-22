import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {randomUUID} from "node:crypto";
import {PrivateStore} from "../dist/config.js";
import {Journal} from "../dist/journal.js";
import {RuntimeSupervisor} from "../dist/runtime-supervisor.js";
import {ContainedEndpointRuntime} from "../dist/contained-endpoint.js";

for (const fault of ["close", "stop", "none", "certified"]) {
    test(`probe cleanup ${fault} always checks containment before readiness`, async () => {
        const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-fix1-stop-")));
        const journal = new Journal(join(store.root, "journal"));
        const saved = {
            startSession: ContainedEndpointRuntime.prototype.startSession,
            probe: ContainedEndpointRuntime.prototype.probe,
            sendTurn: ContainedEndpointRuntime.prototype.sendTurn,
            close: ContainedEndpointRuntime.prototype.close,
        };
        let stops = 0;
        ContainedEndpointRuntime.prototype.startSession = async () => {};
        ContainedEndpointRuntime.prototype.probe = async () => ({chat_ready: true});
        ContainedEndpointRuntime.prototype.sendTurn = async function () {
            const runtime = this as any;
            runtime.model.observed.streaming = true;
            runtime.model.observed.usage = true;
            return runtime.tools.call({
                id: randomUUID(),
                name: "grow_probe_echo",
                arguments: '{"value":"PROBE_OK"}',
            });
        };
        ContainedEndpointRuntime.prototype.close = async () => {
            if (fault === "close") throw Error("injected close failure");
        };
        const sandbox = {
            runProbe: async () => ({
                stopConfirmed: true,
                exitCode: 0,
                output: Buffer.from("GROW_SANDBOX_OK"),
            }),
            stopScope: async () => {
                stops++;
                return {confirmed: fault !== "stop"};
            },
            inspect: async () => [],
        };
        const supervisor = new (RuntimeSupervisor as any)(
            store,
            journal,
            {
                assertRuntime: () => {},
                read: () => ({
                    catalog_reported: true,
                    catalog: {adapters: [{auth_state: "ready", capabilities: {chat_ready: true}}]},
                }),
            },
            sandbox,
            {owner_approved: true, model_image: "sha256:" + "a".repeat(64)},
        );
        let d: any = {
            setup_operation_id: randomUUID(),
            adapter: {mode: "endpoint", version: "0.1.0"},
            provider: {
                base_url: "https://example.invalid/v1",
                model_id: "fixture",
                allowed_models: ["fixture"],
                api_mode: "responses",
                data_scope: ["synthetic"],
                config_version: 1,
            },
            policy: {},
            budget: {},
        };
        if (fault === "certified") {
            const fixture = await codingFixture();
            d = {...fixture.d, setup_operation_id: randomUUID()};
            store.write("coding-certification.json", fixture.evidence);
            supervisor.config.coding_evidence_sha256 = fixture.digest(fixture.evidence);
        }
        const authority = {
            validate: async () => {},
            assertCurrent: () => {},
            request: async () => ({}),
            signal: new AbortController().signal,
            deadline: Date.now() + 10000,
        };
        try {
            if (["none", "certified"].includes(fault)) {
                const result = await supervisor.probe(d, authority);
                assert.equal(result.state, "ready");
                assert.equal(result.capabilities.code_ready, fault === "certified");
            } else await assert.rejects(supervisor.probe(d, authority), /close|containment/i);
            assert.equal(stops, 1);
            if (fault === "stop") {
                assert.equal(supervisor.canExecute(), false);
                await assert.rejects(supervisor.probe(d, authority), /containment/i);
                assert.equal(stops, 1);
                await supervisor.inspect();
                assert.equal(supervisor.canExecute(), true);
            }
        } finally {
            Object.assign(ContainedEndpointRuntime.prototype, saved);
            journal.close();
        }
    });
}

test("broker sockets use the shared private runtime directory and reject unsafe parents", async () => {
    const {brokerSocket, validateBrokerDirectory} = await import("../dist/broker-socket.js");
    const {chmodSync, symlinkSync} = await import("node:fs");
    const root = mkdtempSync(join(tmpdir(), "grow-fix1-socket-"));
    const a = brokerSocket(root, "one"),
        b = brokerSocket(root, "two");
    assert(a.startsWith(`/run/user/${process.getuid!()}/grow-broker-`));
    assert.notEqual(a, b);
    assert(Buffer.byteLength(a) < 104);
    validateBrokerDirectory(root);
    chmodSync(root, 0o750);
    assert.throws(() => validateBrokerDirectory(root), /owner-private/);
    chmodSync(root, 0o700);
    symlinkSync(root, join(root, "alias"));
    assert.throws(() => validateBrokerDirectory(join(root, "alias")), /owner-private/);
});

test("broker system ownership accepts only verified owner namespace mappings", async () => {
    const {brokerSystemOwner} = await import("../dist/broker-socket.js");
    assert.equal(brokerSystemOwner("0 0 4294967295\n", "65534\n", 1000), 0);
    assert.equal(brokerSystemOwner("1000 1000 1\n", "65534\n", 1000), 65534);
    for (const [mapping, overflow, uid] of [
        ["1000 1000 1\n", "65534", 1001],
        ["1000 2000 1\n", "65534", 1000],
        ["1000 1000 2\n", "65534", 1000],
        ["1000 1000 1\n65534 65534 1\n", "65534", 1000],
        ["1000 1000 1\n", "1000", 1000],
        ["0 0 1\n", "65534", 0],
        ["invalid", "65534", 1000],
        ["1000 1000 1\n", "invalid", 1000],
    ] as const) {
        assert.throws(() => brokerSystemOwner(mapping, overflow, uid), /namespace/);
    }
});

test("queued inputs wait for a turn boundary and acknowledge once before the cursor advances", async () => {
    const {InputQueue} = await import("../dist/input-queue.js");
    const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-fix1-input-")));
    const abort = new AbortController();
    const d = {job_id: randomUUID(), attempt_id: randomUUID(), lease_epoch: 1};
    const queue = new InputQueue(journal, d, abort.signal, 0);
    let turns = 0,
        acknowledgements = 0;
    const input = {id: randomUUID(), sequence: 1, text: "Apply this once", input_type: "steering"};
    const pending = queue.submit(input);
    assert.equal(queue.submit(input), pending);
    assert.equal(turns, 0);
    try {
        const runtime = {
            sendTurn: async (text: string, id: string) => {
                assert.equal(text, input.text);
                assert.equal(id, input.id);
                turns++;
                return "done";
            },
        } as any;
        await queue.next(
            runtime,
            async () => {
                assert.equal(queue.cursor, 0);
                acknowledgements++;
            },
            (s) => s,
        );
        assert.equal((await pending).outcome, "applied");
        assert.equal(queue.cursor, 1);
        await queue.submit(input);
        assert.equal(turns, 1);
        assert.equal(acknowledgements, 1);
        const later = {...input, id: randomUUID(), sequence: 2, input_type: "replan"};
        const waiting = queue.wait();
        const applied = queue.submit(later);
        await waiting;
        await queue.next(
            {
                sendTurn: async () => {
                    turns++;
                    return "later";
                },
            } as any,
            async () => {},
            (s) => s,
        );
        await applied;
        assert.equal(queue.cursor, 2);
    } finally {
        abort.abort();
        journal.close();
    }
});

test("input receipt loss recovers the outcome without replaying a model turn", async () => {
    const {InputQueue} = await import("../dist/input-queue.js");
    const root = mkdtempSync(join(tmpdir(), "grow-fix1-receipt-"));
    let journal = new Journal(root);
    const d = {job_id: randomUUID(), attempt_id: randomUUID(), lease_epoch: 1};
    const input = {id: randomUUID(), sequence: 1, text: "once", input_type: "answer"};
    let turns = 0;
    let queue = new InputQueue(journal, d, new AbortController().signal, 0);
    const pending = assert.rejects(queue.submit(input), /lost/);
    await assert.rejects(
        queue.next(
            {
                sendTurn: async () => {
                    turns++;
                    return "done";
                },
            } as any,
            async () => {
                throw Error("receipt lost");
            },
            (s) => s,
        ),
        /lost/,
    );
    await pending;
    assert.equal(queue.cursor, 0);
    journal.close();
    journal = new Journal(root);
    queue = new InputQueue(journal, d, new AbortController().signal, 0);
    try {
        const receipt = queue.submit(input);
        await queue.next(
            {
                sendTurn: async () => {
                    throw Error("replayed");
                },
            } as any,
            async () => {},
            (s) => s,
        );
        assert.equal((await receipt).outcome, "applied");
        assert.equal(queue.cursor, 1);
        assert.equal(turns, 1);
    } finally {
        journal.close();
    }
});

test("cancelled input dispatch remains uncertain and cannot replay", async () => {
    const {InputQueue} = await import("../dist/input-queue.js");
    const root = mkdtempSync(join(tmpdir(), "grow-fix1-cancel-"));
    const journal = new Journal(root),
        abort = new AbortController();
    const d = {job_id: randomUUID(), attempt_id: randomUUID(), lease_epoch: 1};
    const input = {id: randomUUID(), sequence: 1, text: "once", input_type: "answer"};
    const queue = new InputQueue(journal, d, abort.signal, 0);
    const pending = assert.rejects(queue.submit(input), /cancelled/);
    try {
        await assert.rejects(
            queue.next(
                {
                    sendTurn: async () => {
                        abort.abort();
                        throw Error("cancelled turn");
                    },
                } as any,
                async () => {},
                (s) => s,
            ),
            /cancelled/,
        );
        await pending;
        const recovered = new InputQueue(journal, d, new AbortController().signal, 0);
        await assert.rejects(recovered.submit(input), /uncertain/);
        assert.equal(recovered.cursor, 0);
    } finally {
        journal.close();
    }
});

test("checkpoint restoration preserves edited tree and owner WIP and rejects altered or foreign snapshots", async () => {
    const {mkdirSync, writeFileSync, readFileSync} = await import("node:fs");
    const {execFileSync} = await import("node:child_process");
    const {prepareWorkspace, hashFinalTree} = await import("../dist/workspace.js");
    const {retainCheckpoint, restoreCheckpoint, checkpointContext} = await import(
        "../dist/checkpoint-store.js"
    );
    const root = mkdtempSync(join(tmpdir(), "grow-fix1-snapshot-")),
        source = join(root, "source");
    mkdirSync(source);
    const git = (...args: string[]) =>
        execFileSync("/usr/bin/git", args, {
            cwd: source,
            encoding: "utf8",
            env: {
                PATH: "/usr/bin:/bin",
                HOME: "/nonexistent",
                GIT_CONFIG_NOSYSTEM: "1",
                GIT_CONFIG_GLOBAL: "/dev/null",
            },
        }).trim();
    git("init", "-q");
    git("config", "user.name", "Fixture");
    git("config", "user.email", "fixture@invalid");
    writeFileSync(join(source, "a.txt"), "base\n");
    git("add", "a.txt");
    git("commit", "-qm", "fixture\n\nCo-Authored-By: CADIS <agent@cadis.digital>");
    git("remote", "add", "origin", "https://example.invalid/repo");
    const base = git("rev-parse", "HEAD");
    const d: any = {
        job_id: randomUUID(),
        attempt_id: randomUUID(),
        lease_epoch: 1,
        base_ref: "HEAD",
        repository: {
            id: randomUUID(),
            policy_version: 1,
            workspace_alias: "fixture",
            canonical_origin: "https://example.invalid/repo",
            allowed_refs: ["HEAD"],
            required_checks: [],
            base_ref: "HEAD",
        },
    };
    const options = {root: join(root, "workspaces"), source, approvedCommit: base};
    const w = await prepareWorkspace(d, () => d, options);
    writeFileSync(join(w.checkout, "a.txt"), "edited\n");
    const checkpoint = {
        id: randomUUID(),
        source_attempt_id: d.attempt_id,
        base_commit: base,
        tree_hash: await hashFinalTree(w),
        summary: "edited file",
        remaining_work: ["run check"],
        next_step: "verify",
        input_cursor: 1,
        context_ref_ids: [],
        artifact_ids: [],
    };
    await retainCheckpoint(join(root, "snapshots"), d, checkpoint, w, () => d);
    writeFileSync(join(source, "a.txt"), "owner WIP\n");
    const resumed = {...d, attempt_id: randomUUID(), lease_epoch: 2, checkpoint};
    const restored = await prepareWorkspace(resumed, () => resumed, options);
    await restoreCheckpoint(join(root, "snapshots"), resumed, restored, () => resumed);
    assert.equal(restored.record.tree_hash, checkpoint.tree_hash);
    assert.equal(readFileSync(join(restored.checkout, "a.txt"), "utf8"), "edited\n");
    assert.equal(readFileSync(join(source, "a.txt"), "utf8"), "owner WIP\n");
    assert.match(checkpointContext(checkpoint), /run check/);
    assert.match(checkpointContext(checkpoint), /verify/);
    const store = new PrivateStore(root),
        journal = new Journal(join(root, "runtime-journal"));
    const recovery: any = {
        ...resumed,
        repository: {...resumed.repository, base_commit: base},
        request: "Continue authorized task",
        context_refs: [],
        adapter: {mode: "endpoint", version: "0.1.0"},
        job_kind: "answer",
        provider: {
            base_url: "https://example.invalid/v1",
            api_mode: "responses",
            model_id: "fixture",
            allowed_models: ["fixture"],
            data_scope: ["selected_chat", "selected_repository"],
        },
        policy: {actions: ["repository.read"]},
        budget: {active_seconds: 10, artifact_bytes: 100000, job_artifact_bytes: 500000},
    };
    let prepared: any, resumedContext: any;
    let done!: () => void;
    const result = new Promise<void>((resolve) => {
        done = resolve;
    });
    const saved = {
        startSession: ContainedEndpointRuntime.prototype.startSession,
        sendTurn: ContainedEndpointRuntime.prototype.sendTurn,
        resume: ContainedEndpointRuntime.prototype.resume,
        close: ContainedEndpointRuntime.prototype.close,
        cancel: ContainedEndpointRuntime.prototype.cancel,
    };
    ContainedEndpointRuntime.prototype.startSession = async () => {
        assert.equal(prepared.tree_hash, checkpoint.tree_hash);
    };
    ContainedEndpointRuntime.prototype.sendTurn = async () => "Recovered tree verified";
    ContainedEndpointRuntime.prototype.resume = async (value: any) => {
        resumedContext = value;
        return false;
    };
    ContainedEndpointRuntime.prototype.close = async () => {};
    ContainedEndpointRuntime.prototype.cancel = async () => {};
    const supervisor = new (RuntimeSupervisor as any)(
        store,
        journal,
        {assertRuntime: () => {}, assertWorkspace: () => source},
        {stopScope: async () => ({confirmed: true})},
        {},
    );
    try {
        await supervisor.start(recovery, {
            lease: () => recovery,
            request: async () => recovery,
            download: async () => Buffer.alloc(0),
            upload: async (payload: any, bytes: Buffer) => ({
                artifact_id: randomUUID(),
                checksum: payload.checksum,
                size: bytes.length,
            }),
            event: async (type: string, payload: any) => {
                if (type === "workspace.prepared") {
                    prepared = payload;
                    assert.equal(payload.tree_hash, checkpoint.tree_hash);
                }
                if (type === "result.prepared") done();
            },
        });
        await result;
        assert.deepEqual(resumedContext.remaining_work, ["run check"]);
        assert.equal(resumedContext.next_step, "verify");
        assert.equal(resumedContext.current_request, recovery.request);
    } finally {
        await supervisor.stop({attempt_id: recovery.attempt_id});
        Object.assign(ContainedEndpointRuntime.prototype, saved);
        journal.close();
    }

    await assert.rejects(
        restoreCheckpoint(
            join(root, "snapshots"),
            {...resumed, job_id: randomUUID()},
            restored,
            () => resumed,
        ),
        /mismatched/,
    );
    await assert.rejects(
        restoreCheckpoint(
            join(root, "snapshots"),
            {...resumed, checkpoint: {...checkpoint, tree_hash: "a".repeat(40)}},
            restored,
            () => resumed,
        ),
        /mismatched/,
    );
    await assert.rejects(
        restoreCheckpoint(join(root, "snapshots"), resumed, restored, () => ({
            ...resumed,
            lease_epoch: 3,
        })),
        /Stale/,
    );
    const path = join(root, "snapshots", `checkpoint-${checkpoint.id}`, "tree.tar");
    writeFileSync(path, "tampered", {mode: 0o600});
    await assert.rejects(
        restoreCheckpoint(join(root, "snapshots"), resumed, restored, () => resumed),
        /bytes changed/,
    );
});

function codingFixture() {
    return import("../dist/protocol.js").then(async ({digest, effectiveConfiguration}) => {
        const {readFileSync} = await import("node:fs");
        const {toolCatalog} = await import("../dist/runtime.js");
        const {runtimePackageIdentity} = await import("../dist/certification.js");
        const fixtures = JSON.parse(
            readFileSync(
                new URL("../../../zerver/tests/fixtures/agents/protocol-v1.json", import.meta.url),
                "utf8",
            ),
        );
        const d = structuredClone(
            fixtures.valid.find((x: any) => x.schema === "attempt_descriptor").payload,
        );
        d.adapter.mode = "endpoint";
        d.adapter.version = "0.1.0";
        d.provider.api_mode = "responses";
        d.provider.credential_ref = null;
        d.provider.data_scope = ["synthetic", "selected_chat", "selected_repository"];
        d.policy.actions = d.policy.actions.filter(
            (action: string) => !["git.push", "git.draft_pr"].includes(action),
        );
        d.workspace_binding = d.tested_configuration.workspace_binding;
        const definitions = [{id: "check", argv: ["node", "check.js"], cwd: "."}];
        d.workspace_binding.checks_digest = digest(definitions);
        d.configuration_digest = digest(effectiveConfiguration(d));
        const identity = runtimePackageIdentity("sha256:" + "a".repeat(64));
        const hash = "b".repeat(64),
            after = "c".repeat(40);
        const evidence = {
            version: 1,
            issued_at: new Date(Date.now() - 1000).toISOString(),
            expires_at: new Date(Date.now() + 60000).toISOString(),
            configuration_digest: d.configuration_digest,
            configuration: effectiveConfiguration(d),
            package: identity,
            tools: toolCatalog({...d, job_kind: "code"}),
            cases: {
                provider: {real_provider: true, request_ids_sha256: hash},
                read: {passed: true, output_sha256: hash},
                edit: {
                    passed: true,
                    before_tree: "d".repeat(40),
                    after_tree: after,
                    diff_sha256: hash,
                },
                checks: {
                    definitions,
                    results: [
                        {
                            check_id: "check",
                            exit_code: 0,
                            tree_hash: after,
                            output_sha256: hash,
                            timed_out: false,
                        },
                    ],
                },
                publication: {
                    kind: "patch",
                    artifact_id: randomUUID(),
                    diff_artifact_sha256: hash,
                    delivery_receipt_sha256: hash,
                    passed: true,
                    tree_hash: after,
                    conflict_rejected: true,
                    replay_idempotent: true,
                    remote_receipt_sha256: hash,
                },
                containment: {
                    model_stopped: true,
                    tool_stopped: true,
                    cancellation_passed: true,
                    recovery_passed: true,
                    evidence_sha256: hash,
                },
            },
        };
        return {d, evidence, identity, digest};
    });
}

test("coding evidence accepts exact owner approval and rejects stale incomplete or mismatched results", async () => {
    const {validateCodingEvidence, codingReadiness} = await import("../dist/certification.js");
    const {d, evidence, identity, digest} = await codingFixture();
    validateCodingEvidence(d, evidence, digest(evidence), identity);
    for (const mutate of [
        (e: any) => {
            e.expires_at = new Date(0).toISOString();
        },
        (e: any) => {
            e.cases.checks.results[0].exit_code = 1;
        },
        (e: any) => {
            e.cases.edit.after_tree = e.cases.edit.before_tree;
        },
        (e: any) => {
            e.cases.publication.passed = false;
        },
        (e: any) => {
            e.package.model_image = "sha256:" + "f".repeat(64);
        },
        (e: any) => {
            e.configuration.provider.config_version++;
        },
        (e: any) => {
            e.cases.provider.real_provider = false;
        },
        (e: any) => {
            delete e.cases.read;
        },
    ]) {
        const bad = structuredClone(evidence);
        mutate(bad);
        assert.throws(() => validateCodingEvidence(d, bad, digest(bad), identity), /certification/);
    }
    assert.throws(
        () => validateCodingEvidence(d, evidence, "f".repeat(64), identity),
        /certification/,
    );
    const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-fix1-cert-")));
    store.write("coding-certification.json", evidence);
    assert.equal(
        codingReadiness(store, d, {
            model_image: identity.model_image,
            coding_evidence_sha256: digest(evidence),
        }),
        true,
    );
    assert.equal(codingReadiness(store, d, {model_image: identity.model_image}), false);
    assert(!d.policy.actions.includes("git.push"));
    const remote = structuredClone(d);
    remote.policy.actions.push("git.push");
    const {effectiveConfiguration} = await import("../dist/protocol.js");
    remote.configuration_digest = digest(effectiveConfiguration(remote));
    const remoteEvidence = {
        ...evidence,
        configuration: effectiveConfiguration(remote),
        configuration_digest: remote.configuration_digest,
    };
    assert.throws(
        () => validateCodingEvidence(remote, remoteEvidence, digest(remoteEvidence), identity),
        /certification/,
    );
    remoteEvidence.cases.publication.kind = "remote";
    validateCodingEvidence(remote, remoteEvidence, digest(remoteEvidence), identity);
});

for (const timing of ["during-turn", "completion-boundary"]) {
    test(
        `Coordinator queues ${timing} input while heartbeats continue and emits one applied event`,
        {timeout: 10000},
        async () => {
            const {readFileSync} = await import("node:fs");
            const {Transport} = await import("../dist/transport.js");
            const {Coordinator} = await import("../dist/supervisor.js");
            const {digest, effectiveConfiguration} = await import("../dist/protocol.js");
            const defer = () => {
                let resolve!: () => void;
                const promise = new Promise<void>((yes) => {
                    resolve = yes;
                });
                return {resolve, promise};
            };
            const firstStarted = defer(),
                firstDone = defer(),
                inputStarted = defer(),
                inputDone = defer(),
                resultReady = defer();
            const fixtures = JSON.parse(
                readFileSync(
                    new URL(
                        "../../../zerver/tests/fixtures/agents/protocol-v1.json",
                        import.meta.url,
                    ),
                    "utf8",
                ),
            );
            const d = structuredClone(fixtures.valid[0].payload);
            d.adapter.mode = "endpoint";
            d.adapter.version = "0.1.0";
            d.repository = null;
            d.job_kind = "answer";
            d.delivery_target = "answer";
            d.context_refs = [];
            d.policy.actions = ["context.read"];
            d.provider.credential_ref = null;
            d.provider.data_scope = ["selected_chat"];
            d.lease_expires_at = new Date(Date.now() + 60000).toISOString();
            d.tested_configuration = effectiveConfiguration(d);
            d.configuration_digest = digest(d.tested_configuration);
            d.descriptor_digest = digest(
                Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
            );
            const input = {
                ...fixtures.valid.find((x: any) => x.schema === "input").payload,
                id: randomUUID(),
                delivery_state: "delivered",
            };
            const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-fix1-boundary-"))),
                journal = new Journal(join(store.root, "journal"));
            const registry = {
                assertRuntime: () => {},
                read: () => ({
                    catalog_reported: true,
                    catalog: {adapters: [{auth_state: "ready", capabilities: {chat_ready: true}}]},
                }),
            };
            const sandbox = {inspect: async () => [], stopScope: async () => ({confirmed: true})};
            const supervisor = new (RuntimeSupervisor as any)(store, journal, registry, sandbox, {
                owner_approved: true,
            });
            const saved = {
                startSession: ContainedEndpointRuntime.prototype.startSession,
                sendTurn: ContainedEndpointRuntime.prototype.sendTurn,
                close: ContainedEndpointRuntime.prototype.close,
                cancel: ContainedEndpointRuntime.prototype.cancel,
            };
            const turns: string[] = [],
                events: any[] = [];
            ContainedEndpointRuntime.prototype.startSession = async () => {};
            ContainedEndpointRuntime.prototype.sendTurn = async (text: string) => {
                turns.push(text);
                if (turns.length === 1) {
                    firstStarted.resolve();
                    await firstDone.promise;
                } else {
                    inputStarted.resolve();
                    await inputDone.promise;
                }
                return `done ${turns.length}`;
            };
            ContainedEndpointRuntime.prototype.close = async () => {};
            ContainedEndpointRuntime.prototype.cancel = async () => {};
            let version = 1,
                heartbeats = 0,
                available = false,
                applied = false;
            const transport = new Transport("http://localhost", journal, () => "synthetic-fixture");
            transport.request = async (route: string, body: any) => {
                if (route === "/runner/leases") return {leases: []};
                if (route === "/runner/claims") return {attempt: d, job_version: version};
                if (route === "/runner/controls")
                    return {
                        controls: [
                            {
                                attempt_id: d.attempt_id,
                                lease_epoch: d.lease_epoch,
                                job_version: version,
                                control: "continue",
                            },
                        ],
                    };
                if (route === "/runner/heartbeat") {
                    heartbeats++;
                    return {
                        leases: [
                            {
                                attempt_id: d.attempt_id,
                                lease_epoch: d.lease_epoch,
                                job_version: version,
                                control: "continue",
                                lease_expires_at: d.lease_expires_at,
                            },
                        ],
                    };
                }
                if (route === "/runner/inputs")
                    return {inputs: available && !applied ? [input] : []};
                if (route === "/runner/authority") return {...body};
                if (route === "/runner/events") {
                    assert.equal(body.job_version, version);
                    const event = body.events[0];
                    events.push(event);
                    if (event.type === "input.applied") applied = true;
                    if (event.type === "result.prepared") {
                        assert(applied, "result cannot overtake accepted input");
                        resultReady.resolve();
                    }
                    if (event.type === "attempt.interrupted")
                        assert.equal(event.payload.summary, "");
                    return {receipts: [{job_version: ++version}]};
                }
                if (route === "/runner/stop-evidence") return {receipt: {job_version: ++version}};
                throw Error(`Unexpected route ${route}`);
            };
            transport.binary = async (_route: string, body: any, bytes?: Buffer) => ({
                artifact_id: randomUUID(),
                checksum: body.checksum,
                size: bytes!.length,
            });
            const coordinator = new Coordinator(
                journal,
                transport,
                supervisor,
                d.runner_id,
                registry,
            );
            try {
                await coordinator.recover();
                await coordinator.claim();
                await firstStarted.promise;
                await coordinator.tick();
                assert.equal(heartbeats, 1);
                available = true;
                if (timing === "during-turn") {
                    await coordinator.tick();
                    assert.equal(turns.length, 1);
                    assert.equal(heartbeats, 2);
                }
                firstDone.resolve();
                await inputStarted.promise;
                await coordinator.tick();
                await coordinator.tick();
                assert.equal(heartbeats, timing === "during-turn" ? 4 : 3);
                assert.equal(turns.length, 2);
                assert.equal(events.filter((e) => e.type === "result.prepared").length, 0);
                inputDone.resolve();
                await resultReady.promise;
                await coordinator.applyInput(d, input);
                assert.equal(events.filter((e) => e.type === "input.applied").length, 1);
                assert.equal(supervisor.active.get(d.attempt_id).inputCursor, 1);
                assert.equal(turns[1], input.text);
            } finally {
                firstDone.resolve();
                inputDone.resolve();
                await coordinator.stopActive();
                Object.assign(ContainedEndpointRuntime.prototype, saved);
                journal.close();
            }
        },
    );
}

test("cancellation before the turn boundary retains a not-applied receipt without cursor advancement", async () => {
    const {InputQueue} = await import("../dist/input-queue.js");
    const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-fix1-pending-cancel-"))),
        abort = new AbortController();
    const d = {job_id: randomUUID(), attempt_id: randomUUID(), lease_epoch: 1};
    const input = {id: randomUUID(), sequence: 1, text: "pending", input_type: "answer"};
    try {
        const queue = new InputQueue(journal, d, abort.signal, 0),
            receipt = queue.submit(input);
        abort.abort();
        assert.equal((await receipt).outcome, "not_applied");
        assert.equal(queue.cursor, 0);
        const recovered = new InputQueue(journal, d, new AbortController().signal, 0);
        assert.equal((await recovered.submit(input)).outcome, "not_applied");
        assert.equal(recovered.hasPending(), false);
    } finally {
        journal.close();
    }
});
