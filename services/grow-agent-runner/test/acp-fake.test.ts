import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, readFileSync, existsSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {randomUUID} from "node:crypto";
import {spawn, type ChildProcessWithoutNullStreams} from "node:child_process";
import {fileURLToPath} from "node:url";
import {AcpRuntime} from "../dist/acp-runtime.js";
import {checkpointContext} from "../dist/checkpoint-store.js";

// Contract 10.4: drive AcpRuntime against the fake ACP agent fixture through a fake
// sandbox whose startModel spawns it directly (no real container, no real model).
const fixture = fileURLToPath(new URL("./fixtures/fake-acp-agent.mjs", import.meta.url));

function readLog(path: string): any[] {
    if (!existsSync(path)) return [];
    return readFileSync(path, "utf8")
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line));
}

async function waitFor(predicate: () => boolean, timeoutMs = 2000): Promise<void> {
    const start = Date.now();
    while (!predicate()) {
        if (Date.now() - start > timeoutMs) throw new Error("Condition did not become true in time");
        await new Promise((r) => setTimeout(r, 10));
    }
}

function makeRuntime(): {runtime: AcpRuntime; logPath: string} {
    const logPath = join(mkdtempSync(join(tmpdir(), "grow-acp-log-")), "log.jsonl");
    const root = mkdtempSync(join(tmpdir(), "grow-acp-root-"));
    const d = {
        attempt_id: randomUUID(),
        provider: {model_id: "fixture-model"},
        policy: {sandbox: {}},
    };
    const model = {filter: {text: (s: string) => s}};
    const tools = {catalog: []};
    const authority = {
        signal: new AbortController().signal,
        deadline: Date.now() + 30000,
        assertCurrent: async () => {},
        assertLocal: () => {},
    };
    const sandbox = {
        startModel: async () => {
            const child = spawn(process.execPath, [fixture], {
                env: {...process.env, FAKE_ACP_LOG: logPath},
                stdio: ["pipe", "pipe", "pipe"],
            }) as ChildProcessWithoutNullStreams;
            child.stderr.on("data", () => {});
            return {child, close: async () => void child.kill()};
        },
    };
    const runtime = new AcpRuntime(
        d as never,
        model as never,
        tools as never,
        authority as never,
        sandbox as never,
        "sha256:" + "a".repeat(64),
        root,
    );
    return {runtime, logPath};
}

test("AT-04 session/update chunks form the answer, and request_permission gets the offered reject id, never an invented one", async () => {
    const {runtime, logPath} = makeRuntime();
    try {
        await runtime.startSession();
        const answer = await runtime.sendTurn("REQUEST_PERMISSION", randomUUID());
        assert.equal(answer, "Preparing the change. Skipped by request.");
        const permission = readLog(logPath).find((e) => e.event === "permission_response");
        assert.ok(permission, "the fixture must record the permission response");
        assert.equal(permission.response.outcome.outcome, "selected");
        assert.equal(permission.response.outcome.optionId, permission.offered.rejectId);
        // The client must echo the ID the agent actually offered, never a hardcoded guess.
        assert.notEqual(permission.offered.rejectId, "reject");
    } finally {
        await runtime.close();
    }
});

test("AT-17 cancel while request_permission waits sends session/cancel and runs no tool", async () => {
    const {runtime, logPath} = makeRuntime();
    try {
        await runtime.startSession();
        const pending = runtime.sendTurn("WAIT_FOR_CANCEL", randomUUID()).catch(() => "cancelled");
        await waitFor(() => readLog(logPath).some((e) => e.event === "permission_requested"));
        await runtime.cancel();
        await pending;
        const events = readLog(logPath);
        assert.ok(
            events.some((e) => e.event === "notification" && e.method === "session/cancel"),
            "the agent must observe session/cancel",
        );
        assert.ok(
            !events.some((e) => e.event === "tool_ran"),
            "cancellation must stop the fixture before it runs a tool",
        );
    } finally {
        await runtime.close().catch(() => {});
    }
});

test("AT-05 and EX-35 resume then start calls session/new, never session/load, with the checkpoint on the first prompt only", async () => {
    const {runtime, logPath} = makeRuntime();
    const checkpoint = {
        summary: "fixture summary",
        remaining_work: ["fixture work"],
        next_step: "fixture next",
    };
    const expectedContext = checkpointContext(checkpoint as never);
    try {
        await runtime.resume(checkpoint as never);
        await runtime.startSession();
        await runtime.sendTurn("first request", randomUUID());
        await runtime.sendTurn("second request", randomUUID());
        const events = readLog(logPath);
        const methods = events.map((e) => e.method);
        assert.ok(methods.includes("session/new"), "startSession must open a fresh session");
        assert.ok(!methods.includes("session/load"), "a resumed attempt must never load a session");
        const prompts = events
            .filter((e) => e.method === "session/prompt")
            .map((e) => e.params.prompt.map((part: any) => part.text).join(""));
        assert.equal(prompts.length, 2);
        assert.equal(prompts[0], `${expectedContext}\nCurrent task:\nfirst request`);
        // The checkpoint text is consumed once; the second turn must not repeat it.
        assert.equal(prompts[1], "second request");
    } finally {
        await runtime.close();
    }
});
