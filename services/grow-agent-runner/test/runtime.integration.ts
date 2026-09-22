import {hostEnvironment} from "../dist/containment.js";
import {test, after} from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {mkdtempSync, mkdirSync, writeFileSync, readFileSync} from "node:fs";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {execFileSync} from "node:child_process";
import {randomUUID} from "node:crypto";
import {RootlessSandbox} from "../dist/sandbox.js";
import {ModelBroker} from "../dist/model-broker.js";
import {RuntimeTools, toolCatalog} from "../dist/runtime.js";
import {AcpRuntime} from "../dist/acp-runtime.js";
import {ContainedEndpointRuntime} from "../dist/contained-endpoint.js";
import {Journal} from "../dist/journal.js";
import {SecretFilter} from "../dist/redaction.js";
import {prepareWorkspace} from "../dist/workspace.js";
import {ArtifactStore, ToolBroker} from "../dist/tool-broker.js";
import {OperationBoundary} from "../dist/supervisor.js";
import {digest} from "../dist/protocol.js";
const root = mkdtempSync(join(tmpdir(), "grow-task7-integration-"));
console.log("RETAINED_EVIDENCE", root);
const endpoint = `unix:///run/user/${process.getuid!()}/docker.sock`,
    binary = execFileSync("which", ["docker"], {encoding: "utf8"}).trim();
const image = (name: string) =>
    execFileSync(binary, ["--host", endpoint, "image", "inspect", name, "--format", "{{.Id}}"], {
        encoding: "utf8",
    }).trim();
const modelImage = image("grow-task7-model:20260922"),
    toolImage = image("grow-task6-tools:20260922");
const sandbox = await RootlessSandbox.open({
    root: join(root, "containment"),
    endpoint,
    docker: binary,
    images: [modelImage, toolImage],
});
const journal = new Journal(join(root, "journal"));
after(async () => {
    for (const item of await sandbox.inspect()) await sandbox.stopScope(item.attempt_id, item.kind);
    journal.close();
    execFileSync(
        "/usr/bin/systemctl",
        ["--user", "stop", `grow-watchdog-${sandbox.installation.id}`],
        {env: hostEnvironment()},
    );
});
for (const mode of [
    "endpoint-chat",
    "endpoint-responses",
    "acp",
    "endpoint-chat-cancel",
    "acp-cancel",
]) {
    test(
        `${mode} actual isolated model calls separately contained broker edit`,
        {timeout: 90000},
        async () => {
            const id = randomUUID(),
                source = join(root, `source-${id}`);
            mkdirSync(source);
            const git = (...args: string[]) =>
                execFileSync("/usr/bin/git", args, {
                    cwd: source,
                    encoding: "utf8",
                    env: {
                        PATH: "/usr/bin:/bin",
                        HOME: "/nonexistent",
                        GIT_CONFIG_GLOBAL: "/dev/null",
                        GIT_CONFIG_NOSYSTEM: "1",
                    },
                }).trim();
            git("init", "-q");
            git("config", "user.name", "Synthetic fixture");
            git("config", "user.email", "fixture@invalid");
            writeFileSync(join(source, "a.txt"), "old\n");
            git("add", "a.txt");
            git("commit", "-qm", "fixture\n\nCo-Authored-By: CADIS <agent@cadis.digital>");
            git("remote", "add", "origin", "https://example.invalid/test/repo");
            const api = mode.startsWith("endpoint-chat") ? "chat_completions" : "responses";
            let count = 0;
            const requests: any[] = [];
            let entered!: () => void;
            const reached = new Promise<void>((r) => {
                entered = r;
            });
            const provider = createServer(async (req, res) => {
                let raw = "";
                for await (const part of req) raw += part.toString();
                const body = JSON.parse(raw);
                requests.push(body);
                count++;
                assert.equal(req.headers.authorization, "Bearer FAKE_TASK7_CANARY");
                assert.equal(body.model, "gpt-5.4");
                assert.deepEqual(
                    body.tools.map((t: any) => t.name ?? t.function.name),
                    ["grow_read", "grow_search", "grow_edit"],
                );
                if (mode.endsWith("cancel")) {
                    res.writeHead(200, {"Content-Type": "text/event-stream"});
                    res.write('data: {"partial":"tool arguments');
                    entered();
                    return;
                }
                if (count > 2) {
                    res.writeHead(400).end();
                    return;
                }
                const call = {
                    id: "call_edit",
                    name: "grow_edit",
                    arguments: '{"path":"a.txt","content":"new\\n"}',
                };
                if (count === 2) assert.match(JSON.stringify(body), /call_edit/);
                res.writeHead(200, {"Content-Type": "text/event-stream"});
                const emit = (data: any) => res.write(`data: ${JSON.stringify(data)}\n\n`);
                if (api === "chat_completions") {
                    if (count === 1) {
                        emit({
                            choices: [
                                {
                                    index: 0,
                                    delta: {
                                        tool_calls: [
                                            {
                                                index: 0,
                                                id: call.id,
                                                type: "function",
                                                function: {
                                                    name: call.name,
                                                    arguments: call.arguments.slice(0, 12),
                                                },
                                            },
                                        ],
                                    },
                                },
                            ],
                        });
                        emit({
                            choices: [
                                {
                                    index: 0,
                                    delta: {
                                        tool_calls: [
                                            {
                                                index: 0,
                                                function: {arguments: call.arguments.slice(12)},
                                            },
                                        ],
                                    },
                                    finish_reason: "tool_calls",
                                },
                            ],
                        });
                    } else
                        emit({
                            choices: [
                                {
                                    index: 0,
                                    delta: {content: "DONE FAKE_TASK7_CANARY"},
                                    finish_reason: "stop",
                                },
                            ],
                            usage: {prompt_tokens: 1, completion_tokens: 1},
                        });
                } else
                    emit({
                        type: "response.completed",
                        response: {
                            id: `resp_${count}`,
                            status: "completed",
                            output:
                                count === 1
                                    ? [
                                          {
                                              type: "function_call",
                                              call_id: call.id,
                                              name: call.name,
                                              arguments: call.arguments,
                                          },
                                      ]
                                    : [
                                          {
                                              type: "message",
                                              role: "assistant",
                                              content: [
                                                  {
                                                      type: "output_text",
                                                      text: "DONE FAKE_TASK7_CANARY",
                                                  },
                                              ],
                                          },
                                      ],
                            usage: {input_tokens: 1, output_tokens: 1},
                        },
                    });
                res.end();
            });
            await new Promise<void>((r) => provider.listen(0, "127.0.0.1", r));
            const port = (provider.address() as any).port;
            const network = {
                targets: [
                    {hostname: "127.0.0.1", port, allow_private: true, allow_http_loopback: true},
                ],
                public_https_only: true,
                block_metadata: true,
                cross_origin_authorization: false,
                project_network: false,
            };
            const d: any = {
                job_id: randomUUID(),
                attempt_id: id,
                lease_epoch: 1,
                job_kind: "code",
                base_ref: "HEAD",
                repository: {
                    id: randomUUID(),
                    canonical_origin: "https://example.invalid/test/repo",
                    allowed_refs: ["HEAD"],
                    required_checks: [],
                },
                policy: {
                    actions: ["repository.read", "repository.edit"],
                    network,
                    sandbox: {
                        image_digest: toolImage,
                        cpu_millicores: 500,
                        memory_bytes: 536870912,
                        pids_limit: 128,
                        temporary_bytes: 134217728,
                    },
                },
                provider: {
                    base_url: `http://127.0.0.1:${port}/v1`,
                    api_mode: api,
                    model_id: "gpt-5.4",
                    allowed_models: ["gpt-5.4"],
                    network,
                    context_window_tokens: 200000,
                    max_output_tokens: 512,
                },
                budget: {
                    input_tokens: 500000,
                    output_tokens: 4096,
                    tool_rounds: 4,
                    context_recoveries: 1,
                    transport_retries: 0,
                    shell_timeout_seconds: 10,
                    tool_output_bytes: 51200,
                    active_seconds: 60,
                },
            };
            const abort = new AbortController(),
                authority = {
                    signal: abort.signal,
                    deadline: Date.now() + 60000,
                    assertCurrent: async () => {
                        if (abort.signal.aborted) throw new Error("revoked");
                    },
                };
            const lease = () => ({
                job_id: d.job_id,
                attempt_id: id,
                lease_epoch: 1,
                job_version: 1,
            });
            const workspace = await prepareWorkspace(d, lease, {
                root: join(root, "workspaces"),
                source,
                approvedCommit: git("rev-parse", "HEAD"),
            });
            let proposals = 0,
                consumes = 0;
            const effects: any[] = [];
            const operationBoundary = new OperationBoundary(
                journal,
                {
                    mutate: async (kind: string, _id: string, _route: string, p: any) => {
                        if (kind === "proposal") {
                            proposals++;
                            return {
                                operation: {
                                    operation_id: p.operation_id,
                                    operation_hash: digest(p),
                                    version: 1,
                                    status: "authorized",
                                },
                            };
                        }
                        consumes++;
                        return {
                            operation: {
                                operation_id: p.operation_id,
                                operation_hash: p.operation_hash,
                                version: 2,
                                status: "started",
                            },
                        };
                    },
                } as any,
                lease,
            );
            const filter = new SecretFilter();
            filter.add("FAKE_TASK7_CANARY");
            const broker = new ToolBroker(
                d,
                {lease, deadline: authority.deadline, signal: abort.signal},
                workspace,
                sandbox,
                {
                    lease,
                    operations: operationBoundary,
                    event: async (type: string, p: any) => {
                        effects.push({type, p});
                    },
                },
                journal,
                new ArtifactStore(join(root, `artifacts-${id}`), 100000, 500000, filter),
                {
                    upload: async (a) => ({
                        artifact_id: randomUUID(),
                        checksum: a.record.checksum,
                        size: a.record.size,
                    }),
                    publishTree: async () => {},
                },
                filter,
            );
            const tools = new RuntimeTools(
                toolCatalog(d),
                id,
                journal,
                authority,
                filter,
                async (operationId, tool) => {
                    const result = await broker.runSandboxedTool(operationId, tool);
                    return result.result.output.toString();
                },
            );
            const model = new ModelBroker(
                d.provider,
                d.policy,
                d.budget,
                authority,
                async () => "FAKE_TASK7_CANARY",
                filter,
            );
            const runtime = mode.startsWith("acp")
                ? new AcpRuntime(d, model, tools, authority, sandbox, modelImage, root)
                : new ContainedEndpointRuntime(
                      d,
                      model,
                      tools,
                      authority,
                      sandbox,
                      modelImage,
                      root,
                  );
            try {
                await runtime.startSession();
                if (mode.endsWith("cancel")) {
                    const pending = runtime.sendTurn("Change a.txt.", randomUUID());
                    const rejected = assert.rejects(pending);
                    await reached;
                    abort.abort();
                    await runtime.cancel();
                    await rejected;
                    assert.equal(readFileSync(join(workspace.checkout, "a.txt"), "utf8"), "old\n");
                    assert.equal(proposals, 0);
                    assert.equal(consumes, 0);
                    writeFileSync(
                        join(root, `${mode}-evidence.json`),
                        JSON.stringify(
                            {
                                mode,
                                modelImage,
                                requests: count,
                                proposals,
                                consumes,
                                stopped:
                                    (await sandbox.inspect()).filter((x) => x.attempt_id === id)
                                        .length === 0,
                            },
                            null,
                            2,
                        ),
                    );
                    return;
                }
                const answer = await runtime.sendTurn(
                    "Change a.txt to new and report DONE.",
                    randomUUID(),
                );
                assert.match(answer, /DONE/);
                assert(!answer.includes("FAKE_TASK7_CANARY"));
                assert.equal(readFileSync(join(workspace.checkout, "a.txt"), "utf8"), "new\n");
                assert.equal(readFileSync(join(source, "a.txt"), "utf8"), "old\n");
                assert.equal(proposals, 1);
                assert.equal(consumes, 1);
                assert.equal(count, 2);
                assert.equal(effects.filter((e) => e.type === "tool.finished").length, 1);
                writeFileSync(
                    join(root, `${mode}-evidence.json`),
                    JSON.stringify(
                        {
                            mode,
                            modelImage,
                            toolImage,
                            requests: count,
                            proposals,
                            consumes,
                            events: effects.map((e) => e.type),
                            answer,
                            workspace: workspace.root,
                        },
                        null,
                        2,
                    ),
                );
            } finally {
                abort.abort();
                await runtime.close();
                await sandbox.stopScope(id, "attempt");
                provider.closeAllConnections();
                await new Promise<void>((r) => provider.close(() => r()));
            }
        },
    );
}
for (const mode of ["endpoint", "acp", "endpoint-text"]) {
    test(
        `${mode} setup probe uses selected contained runtime and synthetic tool round-trip`,
        {timeout: 90000},
        async () => {
            const {PrivateStore} = await import("../dist/config.js"),
                {RuntimeSupervisor} = await import("../dist/runtime-supervisor.js");
            const state = new PrivateStore(join(root, `probe-${randomUUID()}`));
            state.write("runtime.json", {
                owner_approved: true,
                docker: binary,
                endpoint,
                images: [modelImage, toolImage],
                model_image: modelImage,
            });
            const probeJournal = new Journal(state.root);
            let count = 0;
            const provider = createServer(async (req, res) => {
                let raw = "";
                for await (const b of req) raw += b.toString();
                const body = JSON.parse(raw);
                count++;
                assert.deepEqual(
                    (body.tools ?? []).map((t: any) => t.name),
                    count === 1 ? [] : ["grow_probe_echo"],
                );
                if (count === 1) {
                    assert(!("tools" in body));
                    assert(!("parallel_tool_calls" in body));
                }
                if (mode === "endpoint-text" && count === 2) {
                    res.writeHead(400).end('{"error":{"code":"tools_unsupported"}}');
                    return;
                }
                const output =
                    count === 2
                        ? [
                              {
                                  type: "function_call",
                                  name: "grow_probe_echo",
                                  call_id: "probe_echo",
                                  arguments: '{"value":"PROBE_OK"}',
                              },
                          ]
                        : [
                              {
                                  type: "message",
                                  role: "assistant",
                                  content: [{type: "output_text", text: "PROBE_OK"}],
                              },
                          ];
                if (count === 3) assert.match(raw, /probe_echo/);
                res.writeHead(200, {"Content-Type": "text/event-stream"}).end(
                    `data: ${JSON.stringify({type: "response.completed", response: {status: "completed", output, usage: {input_tokens: 1, output_tokens: 1}}})}\n\n`,
                );
            });
            await new Promise<void>((r) => provider.listen(0, "127.0.0.1", r));
            const port = (provider.address() as any).port;
            const network = {
                targets: [
                    {hostname: "127.0.0.1", port, allow_private: true, allow_http_loopback: true},
                ],
            };
            const d: any = {
                setup_operation_id: randomUUID(),
                profile_revision: 1,
                adapter: {
                    mode: mode === "acp" ? "acp" : "endpoint",
                    version: mode === "acp" ? "1.12.0" : "0.1.0",
                },
                provider: {
                    base_url: `http://127.0.0.1:${port}/v1`,
                    api_mode: "responses",
                    model_id: "gpt-5.4",
                    allowed_models: ["gpt-5.4"],
                    data_scope: ["synthetic"],
                    config_version: 1,
                    network,
                    context_window_tokens: 200000,
                    max_output_tokens: 512,
                },
                policy: {
                    network,
                    sandbox: {
                        image_digest: toolImage,
                        cpu_millicores: 500,
                        memory_bytes: 536870912,
                        pids_limit: 128,
                        temporary_bytes: 134217728,
                    },
                },
                budget: {
                    input_tokens: 500000,
                    output_tokens: 4096,
                    tool_rounds: 4,
                    context_recoveries: 1,
                    transport_retries: 0,
                    active_seconds: 60,
                },
            };
            const supervisor = await RuntimeSupervisor.open(state, probeJournal, {
                assertRuntime: () => {},
            } as any);
            const abort = new AbortController();
            let checks = 0;
            try {
                const result = await supervisor.probe(d, {
                    setup_operation_id: d.setup_operation_id,
                    signal: abort.signal,
                    deadline: Date.now() + 60000,
                    assertCurrent: () => {
                        if (abort.signal.aborted) throw Error("revoked");
                    },
                    validate: async () => {
                        checks++;
                        if (abort.signal.aborted) throw Error("revoked");
                    },
                    request: async () => {
                        throw Error("No credential expected");
                    },
                });
                assert.equal(result.state, "ready", JSON.stringify({result, count}));
                assert.equal(result.capabilities.chat_ready, true);
                assert.equal(result.capabilities.code_ready, false);
                assert.equal(
                    result.capabilities.tool_calling,
                    mode === "endpoint-text" ? "unsupported" : "passed",
                );
                assert.equal(result.capabilities.streaming, "passed");
                assert.equal(result.capabilities.usage, "passed");
                assert.equal(result.capabilities.sandbox, "passed");
                assert.equal(count, mode === "endpoint-text" ? 2 : 3);
                assert(checks > 5);
                assert.deepEqual(await supervisor.inspect(), []);
                writeFileSync(
                    join(root, `${mode}-probe-evidence.json`),
                    JSON.stringify({result, checks, requests: count, state: state.root}, null, 2),
                );
            } finally {
                abort.abort();
                for (const handle of await supervisor.inspect()) await supervisor.stop(handle);
                provider.closeAllConnections();
                await new Promise<void>((r) => provider.close(() => r()));
                probeJournal.close();
                const installation = JSON.parse(
                    readFileSync(join(state.root, "containment/installation.json"), "utf8"),
                );
                execFileSync(
                    "/usr/bin/systemctl",
                    ["--user", "stop", `grow-watchdog-${installation.id}`],
                    {env: hostEnvironment()},
                );
            }
        },
    );
}
