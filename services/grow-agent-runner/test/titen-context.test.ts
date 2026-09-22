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
                  ? {observation_id: "obs_approved"}
                  : {claim_ids: ["claim_approved"]};
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
        const context = new BoundedTitenContext(journal.partition("runner:fixture"), () => ({
            endpoint,
            token: null,
            subject_id: "person:approved",
        }));
        const descriptor: any = {
            attempt_id: "attempt-1",
            request: "Review the requested change",
            repository: {canonical_origin: "https://github.com/Growth-Circle/grow-team.git"},
        };
        const first = await context.enrich(descriptor);
        const second = await context.enrich(descriptor);
        assert.match(first, /Untrusted owner memory/);
        assert.equal(second, "");
        assert.equal(compileCalls, 1);
        assert.deepEqual(
            journal.partition("runner:fixture").get("titen-compile:attempt-1")?.response,
            {
                completed: true,
            },
        );
    } finally {
        journal.close();
        await new Promise<void>((resolve, reject) =>
            server.close((error) => (error ? reject(error) : resolve())),
        );
    }
});
