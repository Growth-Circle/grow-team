import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore} from "../dist/config.js";
import {Journal} from "../dist/journal.js";
import {OwnerRegistry} from "../dist/owner.js";
import {RuntimeSupervisor} from "../dist/runtime-supervisor.js";

// Contract 10.2 (AS-07): probe() must report each of these five setup conditions as a
// plain requirement, never as a thrown error.
function makeProbe(catalog: unknown): {supervisor: any; authority: any; journal: Journal} {
    const root = mkdtempSync(join(tmpdir(), "grow-probe-req-"));
    const store = new PrivateStore(root);
    store.write("registry.json", {
        workspaces: {},
        secrets: {},
        server_scope: "runner:test",
        catalog_reported: true,
        catalog,
    });
    const registry = new OwnerRegistry(store, {currentScope: () => "runner:test"} as any);
    const journal = new Journal(join(root, "journal"));
    const supervisor = new (RuntimeSupervisor as any)(store, journal, registry, {}, {});
    const authority = {
        signal: new AbortController().signal,
        deadline: Date.now() + 10000,
        assertCurrent: () => {},
        validate: async () => {},
        request: async () => ({}),
    };
    return {supervisor, authority, journal};
}

async function assertRequirement(
    catalog: unknown,
    d: unknown,
    expected: {code: string; surface: string; action: string},
): Promise<void> {
    const {supervisor, authority, journal} = makeProbe(catalog);
    try {
        const result = await supervisor.probe(d, authority);
        assert.equal(result.state, "needs_action");
        assert.equal(result.capabilities.code_ready, false);
        assert.deepEqual(result.requirements, [{...expected, diagnostic_id: null}]);
    } finally {
        journal.close();
    }
}

test("runtime_missing is reported as needs_action, not thrown, when the adapter is absent from the catalog", async () => {
    await assertRequirement(
        {revision: 1, adapters: [], sandboxes: []},
        {adapter: {id: "codex", version: "0.1.0", mode: "endpoint"}, policy: {sandbox: {}}},
        {code: "runtime_missing", surface: "adapter", action: "install_adapter"},
    );
});

test("runtime_unsupported is reported as needs_action, not thrown, for an unsupported adapter version", async () => {
    await assertRequirement(
        {
            revision: 1,
            adapters: [{id: "codex", version: "9.9.9", auth_state: "ready", capabilities: {}}],
            sandboxes: [],
        },
        {adapter: {id: "codex", version: "9.9.9", mode: "endpoint"}, policy: {sandbox: {}}},
        {code: "runtime_unsupported", surface: "adapter", action: "install_adapter"},
    );
});

for (const auth_state of ["login_required", "expired"]) {
    test(`auth_required is reported as needs_action, not thrown, when auth_state is ${auth_state}`, async () => {
        await assertRequirement(
            {
                revision: 1,
                adapters: [{id: "codex", version: "0.1.0", auth_state, capabilities: {}}],
                sandboxes: [],
            },
            {adapter: {id: "codex", version: "0.1.0", mode: "endpoint"}, policy: {sandbox: {}}},
            {code: "auth_required", surface: "adapter", action: "login_vendor"},
        );
    });
}

for (const auth_state of ["unchecked", "error"]) {
    test(`auth_unknown is reported as needs_action, not thrown, when auth_state is ${auth_state}`, async () => {
        await assertRequirement(
            {
                revision: 1,
                adapters: [{id: "codex", version: "0.1.0", auth_state, capabilities: {}}],
                sandboxes: [],
            },
            {adapter: {id: "codex", version: "0.1.0", mode: "endpoint"}, policy: {sandbox: {}}},
            {code: "auth_unknown", surface: "adapter", action: "login_vendor"},
        );
    });
}

test("sandbox_unavailable is reported as needs_action, not thrown, when the sandbox is not owner-approved", async () => {
    await assertRequirement(
        {
            revision: 1,
            adapters: [{id: "codex", version: "0.1.0", auth_state: "ready", capabilities: {}}],
            sandboxes: [],
        },
        {
            adapter: {id: "codex", version: "0.1.0", mode: "endpoint"},
            policy: {sandbox: {alias: "default", image_digest: "sha256:" + "a".repeat(64)}},
        },
        {code: "sandbox_unavailable", surface: "sandbox", action: "configure_sandbox"},
    );
});
