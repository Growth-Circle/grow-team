import {test} from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {mkdtempSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {createHash} from "node:crypto";
import {Journal} from "../dist/journal.js";
import {PrivateStore} from "../dist/config.js";
import {BoundedTitenContext, OwnerMemorySignals, TitenClient} from "../dist/titen-context.js";
import {main} from "../dist/cli.js";

test("Titen client uses canonical resolve and bounded compile envelopes", async () => {
    const calls: any[] = [];
    const server = createServer(async (request, response) => {
        let text = "";
        for await (const chunk of request) text += chunk;
        const body = JSON.parse(text);
        calls.push(body);
        if (body.method === "notifications/initialized") {
            response.writeHead(202).end();
            return;
        }
        if (body.method === "initialize") {
            response.writeHead(200, {"Content-Type": "application/json"}).end(
                JSON.stringify({
                    jsonrpc: "2.0",
                    id: body.id,
                    result: {protocolVersion: "2025-11-25"},
                }),
            );
            return;
        }
        const {name, arguments: args} = body.params;
        if (name === "titen_project_resolve")
            assert.deepEqual(args, {reference: "growth-circle/grow-team", create: false});
        if (name === "titen_compile")
            assert.deepEqual(args, {
                subject_id: "person:approved",
                project_id: "project_1",
                task: "Review the requested change",
                max_tokens: 400,
                top_k: 3,
            });
        const data =
            name === "titen_project_resolve"
                ? {project: {id: "project_1"}}
                : {items: [{text: "Untrusted reference"}]};
        response.writeHead(200, {"Content-Type": "application/json"}).end(
            JSON.stringify({
                jsonrpc: "2.0",
                id: body.id,
                result: {content: [{type: "text", text: JSON.stringify({data})}]},
            }),
        );
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
        const endpoint = `http://127.0.0.1:${(server.address() as any).port}/mcp`;
        const client = new TitenClient({endpoint, token: null});
        const project = await client.resolveProject("growth-circle/grow-team");
        const context = await client.compile({
            subject_id: "person:approved",
            project_id: project,
            task: "Review the requested change",
            max_tokens: 400,
            top_k: 3,
        });
        assert.deepEqual(context, {items: [{text: "Untrusted reference"}]});
        assert.equal(calls.filter((item) => item.method === "initialize").length, 1);
        assert.equal(calls.filter((item) => item.method === "notifications/initialized").length, 1);
    } finally {
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});

test("owner memory signals retain evidence and send explicit shared claims", async () => {
    const calls: any[] = [];
    const server = createServer(async (request, response) => {
        let text = "";
        for await (const chunk of request) text += chunk;
        const body = JSON.parse(text);
        if (body.method === "notifications/initialized") return response.writeHead(202).end();
        if (body.method === "initialize") {
            response.writeHead(200, {"Content-Type": "application/json"}).end(
                JSON.stringify({
                    jsonrpc: "2.0",
                    id: body.id,
                    result: {protocolVersion: "2025-11-25"},
                }),
            );
            return;
        }
        calls.push(body.params);
        const data =
            body.params.name === "titen_project_resolve"
                ? {project: {id: "project_approved"}}
                : body.params.name === "titen_remember"
                  ? {
                        observation_id: "obs_approved",
                        subject_id: "person:approved",
                        project_id: "project_approved",
                        kind: "decision",
                        trust: "verified",
                        visibility: "organization",
                    }
                  : {
                        subject_id: "person:approved",
                        project_id: "project_approved",
                        workspace_id: null,
                        model_used: false,
                        claims: [
                            {
                                claim_id: "claim_approved",
                                kind: "decision",
                                trust: "verified",
                                visibility: "organization",
                                evidence_ids: ["obs_approved"],
                            },
                        ],
                    };
        response.writeHead(200, {"Content-Type": "application/json"}).end(
            JSON.stringify({
                jsonrpc: "2.0",
                id: body.id,
                result: {content: [{type: "text", text: JSON.stringify({data})}]},
            }),
        );
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const root = mkdtempSync(join(tmpdir(), "grow-memory-signal-"));
    const evidence = join(root, "evidence.txt");
    writeFileSync(evidence, "verified release receipt\n", {mode: 0o600});
    const checksum = createHash("sha256").update("verified release receipt\n").digest("hex");
    try {
        const endpoint = `http://127.0.0.1:${(server.address() as any).port}/mcp`;
        const writer = new OwnerMemorySignals(new PrivateStore(root), () => ({
            endpoint,
            token: null,
            subject_id: "person:approved",
        }));
        await writer.record({
            audience: {realm_id: 9, requester_user_id: 12},
            project_reference: "growth-circle/grow-team",
            kind: "decision",
            content: "The checked release is accepted.",
            source_type: "tool_result",
            source_ref: "release:task8",
            source_id: "task8-release-1",
            evidence_path: evidence,
            evidence_sha256: checksum,
            idempotency_key: "38b74448-84d5-42f2-a1ef-7ff3b3e0e1cf",
            claims: [{kind: "decision", statement: "The checked release is accepted."}],
        });
        const remember = calls.find((call) => call.name === "titen_remember").arguments;
        assert.equal(remember.visibility, "organization");
        assert.equal(remember.trust, "verified");
        const consolidate = calls.find((call) => call.name === "titen_consolidate").arguments;
        assert.equal(consolidate.claims[0].sources[0].observation_id, "obs_approved");
        assert.equal(
            new PrivateStore(root).read("memory-signals.json")?.[
                "38b74448-84d5-42f2-a1ef-7ff3b3e0e1cf"
            ].evidence_sha256,
            checksum,
        );
        const before = calls.length;
        await assert.rejects(
            () =>
                writer.record({
                    audience: {realm_id: 9, requester_user_id: 12},
                    project_reference: "growth-circle/grow-team",
                    kind: "decision",
                    content: "A changed owner decision.",
                    source_type: "tool_result",
                    source_ref: "release:task8",
                    source_id: "task8-release-1",
                    evidence_path: evidence,
                    evidence_sha256: checksum,
                    idempotency_key: "38b74448-84d5-42f2-a1ef-7ff3b3e0e1cf",
                    claims: [{kind: "decision", statement: "A changed owner decision."}],
                }),
            /idempotency key/,
        );
        assert.equal(calls.length, before);
    } finally {
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});

test("bounded context stores only compile fencing and returns untrusted data once", async () => {
    let compileCalls = 0;
    const server = createServer(async (request, response) => {
        let text = "";
        for await (const chunk of request) text += chunk;
        const body = JSON.parse(text);
        if (body.method === "notifications/initialized") return response.writeHead(202).end();
        if (body.method === "initialize") {
            response.writeHead(200, {"Content-Type": "application/json"}).end(
                JSON.stringify({
                    jsonrpc: "2.0",
                    id: body.id,
                    result: {protocolVersion: "2025-11-25"},
                }),
            );
            return;
        }
        const name = body.params.name;
        if (name === "titen_compile") compileCalls++;
        const data =
            name === "titen_project_resolve"
                ? {project: {id: "project_approved"}}
                : {items: [{text: "memory result"}]};
        response.writeHead(200, {"Content-Type": "application/json"}).end(
            JSON.stringify({
                jsonrpc: "2.0",
                id: body.id,
                result: {content: [{type: "text", text: JSON.stringify({data})}]},
            }),
        );
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-titen-context-")));
    try {
        const endpoint = `http://127.0.0.1:${(server.address() as any).port}/mcp`;
        const context = new BoundedTitenContext(
            journal.partition("runner:fixture"),
            () => ({
                endpoint,
                token: null,
                subject_id: "person:approved",
            }),
            "runner:fixture",
        );
        const descriptor: any = {
            job_id: "job-1",
            attempt_id: "attempt-1",
            request: "Review the requested change",
            audience: {realm_id: 9, requester_user_id: 12},
            repository: {canonical_origin: "https://github.com/Growth-Circle/grow-team.git"},
        };
        const first = await context.enrich(descriptor);
        const second = await context.enrich({...descriptor, attempt_id: "attempt-2"});
        assert.match(first, /Untrusted owner memory/);
        assert.equal(second, "");
        assert.equal(compileCalls, 1);
        assert.deepEqual(
            journal.partition("runner:fixture").get("titen-compile:job:job-1")?.response,
            {
                completed: true,
                project_id: "project_approved",
            },
        );
    } finally {
        journal.close();
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});

test("optional owner Titen credential resolution cannot stop patch context", async () => {
    const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-titen-optional-")));
    try {
        const context = new BoundedTitenContext(
            journal.partition("runner:fixture"),
            () => {
                throw new Error("missing owner secret");
            },
            "runner:fixture",
        );
        assert.equal(
            await context.enrich({
                job_id: "job-optional",
                attempt_id: "attempt",
                request: "Safe patch work",
                audience: {realm_id: 9, requester_user_id: 12},
                repository: {canonical_origin: "https://github.com/growth-circle/grow-team.git"},
            }),
            "",
        );
    } finally {
        journal.close();
    }
});

test("memory retains a remembered partial outcome when consolidation is invalid", async () => {
    const server = createServer(async (request, response) => {
        let text = "";
        for await (const chunk of request) text += chunk;
        const body = JSON.parse(text);
        if (body.method === "notifications/initialized") return response.writeHead(202).end();
        if (body.method === "initialize") {
            response.writeHead(200, {"Content-Type": "application/json"}).end(
                JSON.stringify({
                    jsonrpc: "2.0",
                    id: body.id,
                    result: {protocolVersion: "2025-11-25"},
                }),
            );
            return;
        }
        const data =
            body.params.name === "titen_project_resolve"
                ? {project: {id: "project_approved"}}
                : body.params.name === "titen_remember"
                  ? {
                        observation_id: "obs_approved",
                        subject_id: "person:approved",
                        project_id: "project_approved",
                        kind: "decision",
                        trust: "verified",
                        visibility: "organization",
                    }
                  : {};
        response.writeHead(200, {"Content-Type": "application/json"}).end(
            JSON.stringify({
                jsonrpc: "2.0",
                id: body.id,
                result: {content: [{type: "text", text: JSON.stringify({data})}]},
            }),
        );
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const root = mkdtempSync(join(tmpdir(), "grow-memory-partial-"));
    const evidence = join(root, "evidence");
    writeFileSync(evidence, "verified", {mode: 0o600});
    try {
        const writer = new OwnerMemorySignals(new PrivateStore(root), () => ({
            endpoint: `http://127.0.0.1:${(server.address() as any).port}/mcp`,
            token: null,
            subject_id: "person:approved",
        }));
        await assert.rejects(
            () =>
                writer.record({
                    audience: {realm_id: 9, requester_user_id: 12},
                    project_reference: "growth-circle/grow-team",
                    kind: "decision",
                    content: "Verified release decision.",
                    source_type: "decision",
                    source_ref: "release:fixture",
                    source_id: "fixture-1",
                    evidence_path: evidence,
                    evidence_sha256: createHash("sha256").update("verified").digest("hex"),
                    idempotency_key: "76710f21-d622-4b39-9b50-63e89384c975",
                    claims: [{kind: "decision", statement: "Verified release decision."}],
                }),
            /Invalid consolidated memory claim/,
        );
        assert.equal(
            new PrivateStore(root).read("memory-signals.json")?.[
                "76710f21-d622-4b39-9b50-63e89384c975"
            ].state,
            "remembered",
        );
    } finally {
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});

test("memory CLI rejects protected or changed signals before a Titen request", async () => {
    const root = mkdtempSync(join(tmpdir(), "grow-memory-cli-"));
    const store = new PrivateStore(root);
    const secret = "SYNTHETIC_MEMORY_CREDENTIAL_DO_NOT_STORE";
    const secretPath = join(root, "secret");
    const evidencePath = join(root, "evidence");
    writeFileSync(secretPath, secret, {mode: 0o600});
    writeFileSync(evidencePath, "verified owner receipt", {mode: 0o600});
    const evidenceSha = createHash("sha256").update("verified owner receipt").digest("hex");
    store.write("connection.json", {
        state: "connected",
        origin: "https://control.example",
        runner_id: "runner-fixture",
    });
    store.write("registry.json", {
        workspaces: {},
        catalog: null,
        secrets: {titen: secretPath},
        titen: {
            endpoint: "http://127.0.0.1:1/mcp",
            credential_secret_ref: "titen",
            subjects: [
                {
                    control_origin: "https://control.example",
                    realm_id: 9,
                    requester_user_id: 12,
                    subject_id: "person:approved",
                },
            ],
        },
    });
    const signal = {
        audience: {realm_id: 9, requester_user_id: 12},
        project_reference: "growth-circle/grow-team",
        kind: "tool_result",
        content: `Authorization: Bearer ${secret}`,
        source_type: "tool_result",
        source_ref: "recalled-model-output",
        source_id: "fixture-1",
        evidence_path: evidencePath,
        evidence_sha256: evidenceSha,
        idempotency_key: "38b74448-84d5-42f2-a1ef-7ff3b3e0e1cf",
        claims: [{kind: "decision", statement: "Verified fixture result."}],
    };
    const signalPath = join(root, "signal.json");
    writeFileSync(signalPath, JSON.stringify(signal), {mode: 0o600});
    const oldState = process.env.GROW_AGENT_STATE;
    process.env.GROW_AGENT_STATE = root;
    try {
        await assert.rejects(() => main(["memory-signal", signalPath]), /Protected content/);
        signal.content = "Verified fixture result.";
        writeFileSync(signalPath, JSON.stringify(signal), {mode: 0o600});
        await assert.rejects(() => main(["memory-signal", signalPath]), /provenance/);
    } finally {
        if (oldState === undefined) delete process.env.GROW_AGENT_STATE;
        else process.env.GROW_AGENT_STATE = oldState;
    }
});

test("memory signals reject supported credential forms without a Titen request", async () => {
    const root = mkdtempSync(join(tmpdir(), "grow-memory-credential-"));
    const store = new PrivateStore(root);
    const evidencePath = join(root, "evidence");
    writeFileSync(evidencePath, "verified owner receipt", {mode: 0o600});
    const evidenceSha = createHash("sha256").update("verified owner receipt").digest("hex");
    let calls = 0;
    const server = createServer((_request, response) => {
        calls++;
        response.writeHead(500).end();
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
        const writer = new OwnerMemorySignals(
            store,
            () => ({
                endpoint: `http://127.0.0.1:${(server.address() as any).port}/mcp`,
                token: null,
                subject_id: "person:approved",
            }),
            () => [],
        );
        for (const [index, content] of [
            "token=unconfigured-fixture-value",
            "credential=unconfigured-fixture-value",
            "Authorization: Token unconfigured-fixture-value",
            "Authorization: Basic unconfigured-fixture-value",
            "Authorization: Bearer unconfigured-fixture-value",
        ].entries())
            await assert.rejects(
                () =>
                    writer.record({
                        audience: {realm_id: 9, requester_user_id: 12},
                        project_reference: "growth-circle/grow-team",
                        kind: "tool_result",
                        content,
                        source_type: "tool_result",
                        source_ref: "owner-evidence",
                        source_id: `fixture-${index}`,
                        evidence_path: evidencePath,
                        evidence_sha256: evidenceSha,
                        idempotency_key: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
                        claims: [{kind: "decision", statement: "Verified fixture result."}],
                    }),
                /Protected content/,
            );
        assert.equal(calls, 0);
    } finally {
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});
