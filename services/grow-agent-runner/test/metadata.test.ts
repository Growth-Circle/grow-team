import {test} from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore} from "../dist/config.js";
import {Journal} from "../dist/journal.js";
import {main, parseMetadataCommand, setRunnerMetadata} from "../dist/cli.js";
import {Transport} from "../dist/transport.js";

function success(response: any, metadata: object): void {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({schema_version: 1, result: "success", msg: "", metadata}));
}

test("metadata command validates the declared name, category, and revision", () => {
    assert.equal(parseMetadataCommand(["metadata"]), null);
    assert.deepEqual(parseMetadataCommand(["metadata", "set", "Build server", "server", "2"]), {
        name: "Build server",
        host_kind: "server",
        expected_metadata_revision: 2,
    });
    for (const args of [
        ["metadata", "set", "/private/path", "server", "1"],
        ["metadata", "set", "Agent", "laptop", "1"],
        ["metadata", "set", "Agent", "server", "0"],
        ["metadata", "set", "Agent", "server", "1.5"],
        ["metadata", "set", "Agent", "server", "1", "extra"],
    ])
        assert.throws(() => parseMetadataCommand(args));
});

test("metadata CLI uses the paired bearer and exact device route and body", async () => {
    const requests: {method: string; path: string; authorization: string; body: string}[] = [];
    let metadata = {name: "runner", host_kind: "unknown", metadata_revision: 1};
    const server = createServer(async (request, response) => {
        let body = "";
        for await (const part of request) body += part;
        requests.push({
            method: request.method ?? "",
            path: request.url ?? "",
            authorization: request.headers.authorization ?? "",
            body,
        });
        if (request.method === "POST") {
            const update = JSON.parse(body);
            metadata = {
                name: update.name,
                host_kind: update.host_kind,
                metadata_revision: update.expected_metadata_revision + 1,
            };
        }
        success(response, metadata);
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    assert(address && typeof address !== "string");
    const origin = `http://127.0.0.1:${address.port}`;
    const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-metadata-cli-")));
    store.write("connection.json", {
        state: "connected",
        runner_id: "runner-one",
        origin,
        token: "paired-token",
        expires_at: "2099-01-01T00:00:00Z",
    });
    const oldState = process.env.GROW_AGENT_STATE,
        oldLog = console.log,
        output: string[] = [];
    process.env.GROW_AGENT_STATE = store.root;
    console.log = (value: string) => output.push(value);
    try {
        await main(["metadata"]);
        await main(["metadata", "set", "Build server", "server", "1"]);
        assert.deepEqual(JSON.parse(output[0]!), {
            metadata: {name: "runner", host_kind: "unknown", metadata_revision: 1},
        });
        assert.deepEqual(JSON.parse(output[1]!), {
            outcome: "updated",
            metadata: {name: "Build server", host_kind: "server", metadata_revision: 2},
        });
        assert.deepEqual(
            requests.map(({method, path}) => [method, path]),
            [
                ["GET", "/api/v1/agent/runner/metadata"],
                ["POST", "/api/v1/agent/runner/metadata"],
            ],
        );
        assert(requests.every(({authorization}) => authorization === "Bearer paired-token"));
        assert.deepEqual(JSON.parse(requests[1]!.body), {
            schema_version: 1,
            name: "Build server",
            host_kind: "server",
            expected_metadata_revision: 1,
        });
    } finally {
        console.log = oldLog;
        if (oldState === undefined) delete process.env.GROW_AGENT_STATE;
        else process.env.GROW_AGENT_STATE = oldState;
        await new Promise<void>((resolve) => server.close(() => resolve()));
    }
});

test("lost update response reads observed metadata without replay", async () => {
    let posts = 0,
        reads = 0;
    const server = createServer(async (request, response) => {
        if (request.method === "POST") {
            posts++;
            request.socket.destroy();
            return;
        }
        reads++;
        success(response, {name: "Other value", host_kind: "unknown", metadata_revision: 2});
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    assert(address && typeof address !== "string");
    const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-metadata-uncertain-")));
    store.write("connection.json", {state: "connected", runner_id: "runner-one"});
    const journal = new Journal(store.root);
    try {
        const transport = new Transport(
            `http://127.0.0.1:${address.port}`,
            journal,
            () => "paired-token",
        );
        assert.deepEqual(
            await setRunnerMetadata(transport, {
                name: "Requested value",
                host_kind: "server",
                expected_metadata_revision: 1,
            }),
            {
                outcome: "observed_after_uncertain_update",
                metadata: {name: "Other value", host_kind: "unknown", metadata_revision: 2},
                requested_matches: false,
            },
        );
        assert.equal(posts, 1);
        assert.equal(reads, 1);
        assert.equal(journal.list("runner_metadata")[0]?.state, "uncertain");
    } finally {
        journal.close();
        await new Promise<void>((resolve) => server.close(() => resolve()));
    }
});

test("empty successful update response reads observed metadata without replay", async () => {
    let posts = 0,
        reads = 0;
    const server = createServer(async (request, response) => {
        if (request.method === "POST") {
            posts++;
            response.statusCode = 200;
            response.end("");
            return;
        }
        reads++;
        success(response, {name: "Requested value", host_kind: "server", metadata_revision: 2});
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    assert(address && typeof address !== "string");
    const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-metadata-empty-")));
    store.write("connection.json", {state: "connected", runner_id: "runner-one"});
    const journal = new Journal(store.root);
    try {
        const transport = new Transport(
            `http://127.0.0.1:${address.port}`,
            journal,
            () => "paired-token",
        );
        assert.deepEqual(
            await setRunnerMetadata(transport, {
                name: "Requested value",
                host_kind: "server",
                expected_metadata_revision: 1,
            }),
            {
                outcome: "observed_after_uncertain_update",
                metadata: {name: "Requested value", host_kind: "server", metadata_revision: 2},
                requested_matches: true,
            },
        );
        assert.equal(posts, 1);
        assert.equal(reads, 1);
        assert.equal(journal.list("runner_metadata")[0]?.state, "uncertain");
    } finally {
        journal.close();
        await new Promise<void>((resolve) => server.close(() => resolve()));
    }
});
