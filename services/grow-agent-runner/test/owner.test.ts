import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore} from "../dist/config.js";
import {OwnerRegistry, doctor} from "../dist/owner.js";
test("workspace mapping stays local while immutable metadata retries match", async () => {
    const root = mkdtempSync(join(tmpdir(), "grow-owner-test-")),
        store = new PrivateStore(root),
        sent: any[] = [];
    const transport: any = {
        mutate: async (kind: any, id: any, route: any, body: any) => {
            sent.push(body);
            return {repository: {id: "r1", revision: 1, policy_version: 1}};
        },
    };
    const registry = new OwnerRegistry(store, transport),
        metadata = {
            canonical_origin: null,
            allowed_refs: ["main"],
            required_checks: [],
            revision: 1,
        };
    await registry.workspace("app", root, metadata);
    await registry.workspace("app", root, metadata);
    assert(!JSON.stringify(sent).includes(root));
    assert.equal(store.read("registry.json")?.workspaces.app.path, root);
    assert.throws(() =>
        registry.assertWorkspace({
            id: "other",
            workspace_alias: "app",
            canonical_origin: null,
            allowed_refs: ["main"],
            required_checks: [],
            policy_version: 1,
        }),
    );
});
test("catalog approval rejects browser command injection and preserves a revision", async () => {
    const root = mkdtempSync(join(tmpdir(), "grow-owner-test-")),
        store = new PrivateStore(root),
        sent: any[] = [];
    const registry = new OwnerRegistry(store, {
        mutate: async (k: any, id: any, r: any, p: any) => {
            sent.push(p);
            return {catalog_revision: 1};
        },
    } as any);
    const catalog = {revision: 1, adapters: [], sandboxes: []};
    await registry.catalog(catalog);
    await registry.catalog(catalog);
    assert.deepEqual(sent[0], sent[1]);
    await assert.rejects(() => registry.catalog({...catalog, command: ["sh", "-c", "evil"]}));
});
test("doctor does not equate installed binaries with readiness", async () => {
    const result = await doctor();
    assert.equal(result.chat_ready, false);
    assert.equal(result.code_ready, false);
    assert.deepEqual(result.certified_modes, []);
    assert.equal(result.node.expected, "24.18.0");
});
test("workspace checks use the server's normalized defaults", async () => {
    const root = mkdtempSync(join(tmpdir(), "grow-owner-test-")),
        store = new PrivateStore(root);
    const registry = new OwnerRegistry(store, {
        mutate: async () => ({repository: {id: "r1", revision: 1, policy_version: 1}}),
    } as any);
    await registry.workspace("app", root, {
        canonical_origin: null,
        allowed_refs: ["main"],
        required_checks: [{id: "test", argv: ["npm", "test"]}],
        revision: 1,
    });
    assert.equal(
        registry.assertWorkspace({
            id: "r1",
            workspace_alias: "app",
            canonical_origin: null,
            allowed_refs: ["main"],
            required_checks: [{id: "test", argv: ["npm", "test"], cwd: ".", timeout_seconds: 120}],
            policy_version: 1,
        }),
        root,
    );
});

test("owner Titen settings map each immutable requester to an approved stable subject", () => {
    const root = mkdtempSync(join(tmpdir(), "grow-owner-titen-"));
    const secret = join(root, "titen-token");
    writeFileSync(secret, "fixture-token\n", {mode: 0o600});
    const store = new PrivateStore(root);
    store.write("connection.json", {origin: "https://control.example", runner_id: "runner"});
    const registry = new OwnerRegistry(store, {journal: {protectSecret: () => {}}} as any);
    registry.secret("titen", secret);
    registry.configureTiten({
        endpoint: "http://127.0.0.1:9999/mcp",
        credential_secret_ref: "titen",
        subjects: [
            {
                control_origin: "https://control.example",
                realm_id: 9,
                requester_user_id: 12,
                subject_id: "person:approved",
            },
        ],
    });
    assert.deepEqual(
        registry.titenFor({
            audience: {realm_id: 9, requester_user_id: 12},
            repository: {canonical_origin: "https://github.com/Growth-Circle/grow-team.git"},
        }),
        {
            endpoint: "http://127.0.0.1:9999/mcp",
            token: "fixture-token",
            subject_id: "person:approved",
        },
    );
    assert.equal(
        registry.titenFor({
            audience: {realm_id: 9, requester_user_id: 13},
            repository: {canonical_origin: "https://github.com/Growth-Circle/grow-team.git"},
        }),
        null,
    );
});

test("owner publication settings bind the descriptor repository and remote", () => {
    const root = mkdtempSync(join(tmpdir(), "grow-owner-publication-"));
    const store = new PrivateStore(root);
    const registry = new OwnerRegistry(store, {journal: {protectSecret: () => {}}} as any);
    registry.configureRemoteOperations({
        git: [
            {
                repository_id: "repo-1",
                remote: "https://github.com/Growth-Circle/grow-team.git",
                credential_secret_ref: null,
            },
        ],
        github: [],
    });
    assert.deepEqual(
        registry.publicationFor({
            repository: {
                id: "repo-1",
                canonical_origin: "https://github.com/Growth-Circle/grow-team.git",
            },
        }),
        {
            remote: "https://github.com/Growth-Circle/grow-team.git",
            git_credential: null,
            github: null,
        },
    );
    assert.equal(
        registry.publicationFor({
            repository: {
                id: "repo-2",
                canonical_origin: "https://github.com/Growth-Circle/grow-team.git",
            },
        }),
        null,
    );
});
