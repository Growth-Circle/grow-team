import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, statSync, symlinkSync, writeFileSync, chmodSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {PrivateStore, controlOrigin, childEnvironment} from "../dist/config.js";
import {Journal} from "../dist/journal.js";
const dir = () => mkdtempSync(join(tmpdir(), "grow-runner-test-"));
test("private atomic files, symlink denial and frozen control origin", () => {
    const root = dir(),
        store = new PrivateStore(root);
    store.write("config.json", {origin: controlOrigin("https://grow.example")});
    assert.equal(statSync(root).mode & 0o777, 0o700);
    assert.equal(statSync(join(root, "config.json")).mode & 0o777, 0o600);
    symlinkSync("/etc/passwd", join(root, "bad.json"));
    assert.throws(() => store.read("bad.json"));
    assert.throws(() => store.write("bad.json", {}));
    for (const url of [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?x",
    ])
        assert.throws(() => controlOrigin(url));
    assert.equal(controlOrigin("http://127.0.0.1:9000"), "http://127.0.0.1:9000");
    assert.throws(() => childEnvironment({GROW_CONTROL_ORIGIN: "evil"}));
    assert.throws(() => childEnvironment({NODE_OPTIONS: "--require evil"}));
    assert.deepEqual(childEnvironment({LANG: "C"}), {LANG: "C"});
});
test("journal survives lost acknowledgement and holds exclusive supervisor ownership", () => {
    const root = dir();
    let j = new Journal(root);
    const request = {claim_key: "same-id"};
    j.prepare("claim", "same-id", "/claims", request);
    j.uncertain("same-id");
    assert.throws(() => new Journal(root));
    j.close();
    j = new Journal(root);
    assert.equal(j.get("same-id")?.state, "uncertain");
    assert.deepEqual(j.get("same-id")?.request, request);
    assert.throws(() => j.prepare("claim", "same-id", "/claims", {claim_key: "changed"}));
    j.complete("same-id", {attempt: null});
    j.close();
    j = new Journal(root);
    assert.equal(j.get("same-id")?.state, "done");
    j.close();
});
test("ordinary journal rejects credential keys", () => {
    const j = new Journal(dir());
    assert.throws(() =>
        j.prepare("event", "secret", "/events", {nested: {refresh_token: "secret"}}),
    );
    j.close();
});
test("known local secret material cannot enter event text", () => {
    const j = new Journal(dir());
    j.protectSecret("private-provider-value");
    assert.throws(() =>
        j.prepare("event", "x", "/runner/events", {
            payload: {summary: "leak private-provider-value"},
        }),
    );
    j.close();
});
