import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore} from "../dist/config.js";
import {Journal} from "../dist/journal.js";
import {OwnerRegistry} from "../dist/owner.js";
import {createRuntimeExtensions} from "../dist/cli.js";

test("CLI construction keeps patch-only jobs safe without owner remote or Titen configuration", async () => {
    const store = new PrivateStore(mkdtempSync(join(tmpdir(), "grow-cli-test-")));
    store.write("connection.json", {
        runner_id: "runner",
        origin: "https://control.example",
        state: "connected",
    });
    const journal = new Journal(store.root);
    try {
        const extensions = createRuntimeExtensions(
            store,
            journal,
            new OwnerRegistry(store, {journal, currentScope: () => "runner:runner"} as any),
        );
        assert.equal(
            await extensions.context?.(
                {
                    attempt_id: "attempt",
                    repository: {canonical_origin: "https://github.com/org/repo.git"},
                },
                {lease: () => ({})} as any,
            ),
            "",
        );
        await extensions.publish?.({delivery_target: "patch"}, {} as any, {} as any, {} as any);
    } finally {
        journal.close();
    }
});
