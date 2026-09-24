import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {randomUUID} from "node:crypto";
import {decode, encode} from "../dist/codecs.js";
import {addressClass, approveAddress} from "../dist/model-broker.js";
import {SecretFilter} from "../dist/redaction.js";
import {RuntimeTools, EndpointRuntime, toolCatalog} from "../dist/runtime.js";
import {Journal} from "../dist/journal.js";
import {ArtifactStore} from "../dist/tool-broker.js";
import {permissionDecision} from "../dist/acp-runtime.js";
const sse = (frames: any[]) =>
    Buffer.from(frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join(""));
test("adding the manage job kind leaves the code and answer repository catalogs unchanged", () => {
    const repository = {id: "r1"};
    const code = {
        job_kind: "code",
        repository,
        policy: {actions: ["repository.read", "repository.edit", "shell.run"]},
    };
    assert.deepEqual(
        toolCatalog(code).map((t) => t.name),
        ["grow_read", "grow_search", "grow_edit", "grow_shell"],
    );
    assert.deepEqual(
        toolCatalog({...code, job_kind: "answer"}).map((t) => t.name),
        ["grow_read", "grow_search"],
    );
    // A manage job has no repository (contract 2.5), so this repository-bound catalog
    // stays empty for it; team tools come from team-tools.ts's own catalog instead.
    assert.deepEqual(toolCatalog({...code, job_kind: "manage", repository: null}), []);
});
test("Chat tool arguments assemble across stream frames and retain IDs", () => {
    const turn = decode(
        "chat_completions",
        sse([
            {
                choices: [
                    {
                        index: 0,
                        delta: {
                            tool_calls: [
                                {
                                    index: 0,
                                    id: "call_a",
                                    type: "function",
                                    function: {name: "grow_edit", arguments: '{"path":"a",'},
                                },
                            ],
                        },
                    },
                ],
            },
            {
                choices: [
                    {
                        index: 0,
                        delta: {tool_calls: [{index: 0, function: {arguments: '"content":"b"}'}}]},
                        finish_reason: "tool_calls",
                    },
                ],
            },
        ]),
        "text/event-stream",
    );
    assert.deepEqual(turn.calls, [
        {id: "call_a", name: "grow_edit", arguments: '{"path":"a","content":"b"}'},
    ]);
});
test("partial and malformed Chat frames cannot yield tools", () => {
    assert.throws(() =>
        decode(
            "chat_completions",
            sse([
                {
                    choices: [
                        {
                            delta: {
                                tool_calls: [
                                    {id: "x", function: {name: "grow_edit", arguments: '{"path":'}},
                                ],
                            },
                        },
                    ],
                },
            ]),
            "text/event-stream",
        ),
    );
    assert.throws(() =>
        decode("chat_completions", Buffer.from('data: {"bad":'), "text/event-stream"),
    );
});
test("Responses ignores reasoning and requires completed envelope", () => {
    const result = decode(
        "responses",
        sse([
            {type: "response.reasoning_text.delta", delta: "PRIVATE_REASONING"},
            {
                type: "response.completed",
                response: {
                    status: "completed",
                    output: [
                        {type: "reasoning", summary: [{text: "PRIVATE"}]},
                        {
                            type: "function_call",
                            call_id: "id",
                            name: "grow_read",
                            arguments: '{"path":"a"}',
                        },
                    ],
                    usage: {input_tokens: 1, output_tokens: 2},
                },
            },
        ]),
        "text/event-stream",
    );
    assert.equal(result.text, "");
    assert.equal(result.calls[0]!.id, "id");
    assert.equal(result.inputTokens, 1);
    assert.throws(() =>
        decode(
            "responses",
            sse([{type: "response.output_item.added", item: {type: "function_call"}}]),
            "text/event-stream",
        ),
    );
});
test("dialects encode separate tool/result IDs and output limits", () => {
    for (const dialect of ["responses", "chat_completions"] as const) {
        const body = encode(
            dialect,
            "fixed",
            [
                {
                    role: "assistant",
                    text: "",
                    calls: [{id: "id", name: "grow_read", arguments: "{}"}],
                },
                {role: "tool", callId: "id", text: "result"},
            ],
            [],
            16,
        );
        assert.equal(body.model, "fixed");
        assert.equal(body.max_output_tokens ?? body.max_completion_tokens, 16);
        assert.match(JSON.stringify(body), /id/);
    }
});
test("metadata denial overrides exact owner-private approval including IPv6 aliases", () => {
    const policies = [{targets: [{hostname: "approved.test", port: 443, allow_private: true}]}];
    for (const address of [
        "169.254.169.254",
        "100.100.100.200",
        "fd00:ec2::254",
        "fd00:0ec2:0000:0000:0000:0000:0000:0254",
        "::ffff:169.254.169.254",
    ]) {
        assert.equal(addressClass(address), "denied");
        assert.throws(() => approveAddress(new URL("https://approved.test"), address, policies));
    }
    assert.equal(addressClass("100.64.1.2"), "private");
    assert.throws(() => approveAddress(new URL("http://approved.test"), "100.64.1.2", policies));
});
test("secret split across chunks is removed before retention and checksum", () => {
    const filter = new SecretFilter();
    filter.add("CANARY-SECRET");
    const text = ["A CANARY-", "SECRET B"].join("");
    assert.equal(filter.text(text), "A [REDACTED] B");
    assert.throws(() => filter.assertArguments({content: text}));
    const store = new ArtifactStore(
        mkdtempSync(join(tmpdir(), "grow-redaction-")),
        1000,
        1000,
        filter,
    );
    const artifact = store.retain("j", "a", "summary", Buffer.from(text));
    assert.equal(readFileSync(artifact.path, "utf8"), "A [REDACTED] B");
});
test("tool names, schemas, cancel and uncertain call IDs prevent mutation", async () => {
    const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-runtime-"))),
        abort = new AbortController(),
        filter = new SecretFilter();
    let effects = 0;
    const authority = {
        signal: abort.signal,
        deadline: Date.now() + 10000,
        assertCurrent: async () => {},
    };
    const tools = new RuntimeTools(
        [{name: "grow_edit", description: "", parameters: {}}],
        "scope",
        journal,
        authority,
        filter,
        async () => {
            effects++;
            throw new Error("lost receipt");
        },
    );
    const call = {id: "one", name: "grow_edit", arguments: '{"path":"a","content":"b"}'};
    await assert.rejects(() => tools.call({...call, name: "exec_command"}));
    await assert.rejects(() => tools.call({...call, arguments: '{"path":'}));
    assert.equal(effects, 0);
    await assert.rejects(() => tools.call(call));
    assert.equal(effects, 1);
    await assert.rejects(() => tools.call(call));
    assert.equal(effects, 1);
    abort.abort();
    await assert.rejects(() => tools.call({...call, id: "two"}));
    assert.equal(effects, 1);
    journal.close();
});
test("ACP permissions use the offered reject option ID", () => {
    assert.deepEqual(
        permissionDecision([
            {kind: "allow_once", optionId: "yes"},
            {kind: "reject_once", optionId: "NO-42"},
        ]),
        {outcome: {outcome: "selected", optionId: "NO-42"}},
    );
    assert.deepEqual(permissionDecision([{kind: "allow_once", optionId: "yes"}]), {
        outcome: {outcome: "cancelled"},
    });
});
test("text resembling tool JSON never executes and input ID cannot repeat", async () => {
    let calls = 0;
    const abort = new AbortController();
    const runtime = new EndpointRuntime(
        {
            filter: new SecretFilter(),
            turn: async () => ({text: '{"name":"grow_edit"}', calls: []}),
        } as any,
        {
            catalog: [],
            call: async () => {
                calls++;
            },
        } as any,
        {signal: abort.signal, deadline: Date.now() + 10000, assertCurrent: async () => {}},
        {tool_rounds: 2, context_recoveries: 1},
    );
    await runtime.startSession();
    assert.match(await runtime.sendTurn("hello", "one"), /grow_edit/);
    await assert.rejects(() => runtime.sendTurn("hello", "one"));
    assert.equal(calls, 0);
});
test("provider data scope fails before any job runtime or tool starts", async () => {
    const {assertDataScope} = await import("../dist/runtime-supervisor.js");
    assert.throws(() => assertDataScope({provider: {data_scope: ["synthetic"]}}), /selected chat/);
    assert.throws(
        () => assertDataScope({provider: {data_scope: ["selected_chat"]}, repository: {}}),
        /repository/,
    );
    assert.doesNotThrow(() =>
        assertDataScope({
            provider: {data_scope: ["selected_chat", "selected_repository"]},
            repository: {},
        }),
    );
});
test("quoted multiline secret is rejected before a journal or effect", () => {
    const filter = new SecretFilter();
    const secret = 'synthetic-quote"-line\nvalue';
    filter.add(secret);
    assert.throws(() => filter.assertArguments({content: secret}));
    const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-secret-journal-")));
    journal.protectSecret(secret);
    assert.throws(() => journal.prepare("intent", "one", "local", {content: secret}));
    journal.close();
});
test("bounded context recovery preserves the active input once", async () => {
    const {ProviderFailure} = await import("../dist/codecs.js");
    const observed: any[] = [];
    const runtime = new EndpointRuntime(
        {
            filter: new SecretFilter(),
            turn: async (messages: any[]) => {
                observed.push(structuredClone(messages));
                if (observed.length === 1) throw new ProviderFailure("context");
                return {text: "ok", calls: []};
            },
        } as any,
        {catalog: []} as any,
        {
            signal: new AbortController().signal,
            deadline: Date.now() + 1000,
            assertCurrent: async () => {},
        },
        {tool_rounds: 3, context_recoveries: 1},
    );
    await runtime.resume({summary: "Old untrusted checkpoint"});
    assert.equal(await runtime.sendTurn("CURRENT_UNIQUE", "id"), "ok");
    assert.equal(observed[1].filter((m: any) => m.text === "CURRENT_UNIQUE").length, 1);
    assert.equal(observed[1].length, 1);
});
test("AF-38 a second context failure fails the turn, runs no tool, and keeps the active input once", async () => {
    const {ProviderFailure} = await import("../dist/codecs.js");
    const observed: any[] = [];
    let toolCalls = 0;
    const runtime = new EndpointRuntime(
        {
            filter: new SecretFilter(),
            turn: async (messages: any[]) => {
                observed.push(structuredClone(messages));
                throw new ProviderFailure("context");
            },
        } as any,
        {
            catalog: [],
            call: async () => {
                toolCalls++;
                return "";
            },
        } as any,
        {
            signal: new AbortController().signal,
            deadline: Date.now() + 1000,
            assertCurrent: async () => {},
        },
        {tool_rounds: 3, context_recoveries: 1},
    );
    await runtime.resume({summary: "Old untrusted checkpoint"});
    await assert.rejects(
        () => runtime.sendTurn("CURRENT_UNIQUE", "id"),
        (e: unknown) => e instanceof ProviderFailure && e.kind === "context",
    );
    assert.equal(observed.length, 2, "one attempt plus one recovery retry, then the turn gives up");
    assert.equal(toolCalls, 0, "a failed turn must never reach a tool call");
    assert.equal(observed[1].filter((m: any) => m.text === "CURRENT_UNIQUE").length, 1);
    assert.equal(observed[1].length, 1);
});
test("native model catalog rejects remote tools and media", async () => {
    const {nativeMessages} = await import("../dist/acp-runtime.js");
    assert.throws(() =>
        nativeMessages({tools: [{type: "web_search"}], input: []}, {catalog: []} as any),
    );
    assert.throws(() =>
        nativeMessages(
            {
                tools: [],
                input: [
                    {role: "user", content: [{type: "input_image", image_url: "http://metadata/"}]},
                ],
            },
            {catalog: []} as any,
        ),
    );
});
test("native patch gate freezes policy and denies alternate request paths", async () => {
    const saved = process.env.GROW_NATIVE_CONFIG;
    process.env.GROW_NATIVE_CONFIG = JSON.stringify({model: "fixed", tools: []});
    try {
        const {constrain} = await import("../native/policy.mjs");
        for (const method of [
            "review/start",
            "thread/fork",
            "thread/resume",
            "thread/goal/set",
            "mcpServer/oauth/login",
            "account/login/start",
            "config/batchWrite",
        ])
            assert.throws(() => constrain({method, params: {}}));
        for (const method of ["thread/start", "turn/start"]) {
            const request = constrain({
                method,
                params: {
                    environments: ["host"],
                    model: "other",
                    approvalPolicy: "never",
                    sandboxPolicy: {type: "dangerFullAccess"},
                },
            });
            assert.deepEqual(request.params.environments, []);
            assert.equal(request.params.model, "fixed");
            assert.equal(request.params.approvalPolicy, "on-request");
            if (method === "turn/start")
                assert.deepEqual(request.params.sandboxPolicy, {
                    type: "externalSandbox",
                    networkAccess: "restricted",
                });
        }
    } finally {
        if (saved === undefined) delete process.env.GROW_NATIVE_CONFIG;
        else process.env.GROW_NATIVE_CONFIG = saved;
    }
});
test("uncertain provider reservations survive journal restart", async () => {
    const {ModelBudgetLedger} = await import("../dist/model-broker.js");
    const root = mkdtempSync(join(tmpdir(), "grow-model-budget-"));
    let journal = new Journal(root);
    let ledger = new ModelBudgetLedger(journal, "job");
    ledger.reserve(100, 50);
    const settled = ledger.reserve(100, 50);
    ledger.settle(settled, 3);
    journal.close();
    journal = new Journal(root);
    ledger = new ModelBudgetLedger(journal, "job");
    assert.deepEqual(ledger.used(), {input: 200, output: 53});
    assert.deepEqual(new ModelBudgetLedger(journal, "other-job").used(), {input: 0, output: 0});
    journal.close();
});
