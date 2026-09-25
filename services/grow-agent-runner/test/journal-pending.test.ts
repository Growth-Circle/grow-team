import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Journal} from "../dist/journal.js";
const root = () => mkdtempSync(join(tmpdir(), "grow-journal-pending-test-"));

test("pending(kind) returns only the still-open entries of that kind", () => {
    const j = new Journal(root());
    j.prepare("claim", "claim:done", "local", {n: 1});
    j.complete("claim:done", {ok: true});
    j.prepare("claim", "claim:open-1", "local", {n: 2});
    j.prepare("claim", "claim:open-2", "local", {n: 3});
    j.uncertain("claim:open-2");
    j.prepare("attempt", "attempt:1", "local", {n: 4});
    const pending = j.pending("claim");
    assert.deepEqual(pending.map((e) => e.id).sort(), ["claim:open-1", "claim:open-2"]);
    assert(pending.every((e) => e.state !== "done"));
    j.close();
});

test("pending(kind) matches list(kind) filtered to not-done, for the same data", () => {
    const j = new Journal(root());
    for (let i = 0; i < 5; i++) j.prepare("event", `event:${i}`, "local", {i});
    j.complete("event:1", {ok: true});
    j.complete("event:3", {ok: true});
    const viaPending = j.pending("event").map((e) => e.id);
    const viaList = j
        .list("event")
        .filter((e) => e.state !== "done")
        .map((e) => e.id);
    assert.deepEqual(viaPending, viaList);
    j.close();
});

test("pending(kind) inside a scope only returns that scope's entries", () => {
    const j = new Journal(root());
    const a = j.partition("runner:a"),
        b = j.partition("runner:b");
    a.prepare("claim", "claim:x", "local", {who: "a"});
    b.prepare("claim", "claim:x", "local", {who: "b"});
    assert.deepEqual(
        a.pending("claim").map((e) => e.request.who),
        ["a"],
    );
    assert.deepEqual(
        b.pending("claim").map((e) => e.request.who),
        ["b"],
    );
    j.close();
});

test("pending(kind) is empty once every entry of that kind is done", () => {
    const j = new Journal(root());
    j.prepare("stop", "stop:1", "local", {n: 1});
    j.complete("stop:1", {ok: true});
    assert.deepEqual(j.pending("stop"), []);
    j.close();
});
