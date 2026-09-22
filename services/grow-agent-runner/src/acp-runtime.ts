import * as acp from "@agentclientprotocol/sdk";
import {Readable, Writable} from "node:stream";
import {createServer, type Server} from "node:http";
import {chmodSync} from "node:fs";
import {brokerSocket} from "./broker-socket.js";
import {randomUUID} from "node:crypto";
import {canonical, type Data} from "./protocol.js";
import type {Message} from "./codecs.js";
import {ModelBroker, type ModelAuthority} from "./model-broker.js";
import {RootlessSandbox} from "./sandbox.js";
import {RuntimeTools, type Runtime} from "./runtime.js";
export function permissionDecision(options: Data[]): Data {
    const deny = options.find((o) => o.kind === "reject_once");
    return deny
        ? {outcome: {outcome: "selected", optionId: deny.optionId}}
        : {outcome: {outcome: "cancelled"}};
}
export function nativeMessages(body: Data, tools: RuntimeTools): Message[] {
    if (!Array.isArray(body.tools) || body.tools.length !== tools.catalog.length)
        throw new Error("Native catalog changed");
    for (const item of body.tools) {
        const tool = tools.catalog.find((t) => t.name === item.name);
        if (
            item.type !== "function" ||
            !tool ||
            canonical(item.parameters) !== canonical(tool.parameters)
        )
            throw new Error("Native catalog changed");
    }
    if (new Set(body.tools.map((t: Data) => t.name)).size !== tools.catalog.length)
        throw new Error("Duplicate native catalog");
    if (!Array.isArray(body.input)) throw new Error("Invalid native input");
    const messages: Message[] = [];
    if (typeof body.instructions === "string")
        messages.push({role: "user", text: body.instructions});
    for (const item of body.input) {
        if (item.type === "reasoning") continue;
        if (item.type === "function_call")
            messages.push({
                role: "assistant",
                text: "",
                calls: [{id: item.call_id, name: item.name, arguments: item.arguments}],
            });
        else if (item.type === "function_call_output") {
            if (typeof item.output !== "string") throw new Error("Native output requires text");
            messages.push({role: "tool", text: item.output, callId: item.call_id});
        } else if (["user", "assistant", "system", "developer"].includes(item.role)) {
            let text = "";
            if (typeof item.content === "string") text = item.content;
            else if (Array.isArray(item.content))
                for (const part of item.content) {
                    if (
                        !["input_text", "output_text", "text"].includes(part.type) ||
                        typeof part.text !== "string"
                    )
                        throw new Error("Native media or remote tool denied");
                    text += part.text;
                }
            else throw new Error("Native content denied");
            messages.push({role: item.role === "assistant" ? "assistant" : "user", text});
        } else throw new Error("Native input item denied");
    }
    return messages;
}
export class AcpRuntime implements Runtime {
    private server: Server | null = null;
    private process: Awaited<ReturnType<RootlessSandbox["startModel"]>> | null = null;
    private connection: ReturnType<ReturnType<typeof acp.client>["connect"]> | null = null;
    private session = "";
    private answer = "";
    private checkpoint = "";
    private initialized = false;
    private busy = false;
    private failure = false;
    private inputs = new Set<string>();
    constructor(
        private d: Data,
        private model: ModelBroker,
        private tools: RuntimeTools,
        private authority: ModelAuthority,
        private sandbox: RootlessSandbox,
        private image: string,
        private root: string,
    ) {}
    async startSession(): Promise<void> {
        await this.authority.assertCurrent();
        const scope = this.d.attempt_id ?? `probe-${this.d.setup_operation_id}`;
        const socket = brokerSocket(this.root, scope);
        const server = createServer(async (req, res) => {
            try {
                if (
                    req.method !== "POST" ||
                    !["/model", "/tool"].includes(req.url ?? "") ||
                    req.headers.upgrade ||
                    Object.keys(req.headers).some((k) =>
                        /^(forwarded|x-forwarded|authorization)/.test(k),
                    )
                )
                    throw new Error("Private route denied");
                const chunks: Buffer[] = [];
                let size = 0;
                for await (const b of req) {
                    size += b.length;
                    if (size > 2 * 1024 * 1024) throw new Error("Native request limit");
                    chunks.push(b);
                }
                await this.authority.assertCurrent();
                if (!this.busy || this.authority.signal.aborted)
                    throw new Error("No active native turn");
                const body = JSON.parse(Buffer.concat(chunks).toString());
                let output: unknown;
                if (req.url === "/model") {
                    if (body.model !== this.d.provider.model_id)
                        throw new Error("Native model changed");
                    output = await this.model.turn(
                        nativeMessages(body, this.tools),
                        this.tools.catalog,
                    );
                } else {
                    if (
                        body.threadId !== this.session ||
                        typeof body.callId !== "string" ||
                        typeof body.tool !== "string"
                    )
                        throw new Error("Native tool identity mismatch");
                    const text = await this.tools.call({
                        id: body.callId,
                        name: body.tool,
                        arguments: JSON.stringify(body.arguments),
                    });
                    output = {success: true, contentItems: [{type: "inputText", text}]};
                }
                await this.authority.assertCurrent();
                res.writeHead(200, {"Content-Type": "application/json"}).end(
                    JSON.stringify(output),
                );
            } catch {
                this.failure = true;
                res.writeHead(403).end('{"error":"Grow authority denied"}');
            }
        });
        server.on("connect", (_req, s) => s.destroy());
        server.on("upgrade", (_req, s) => s.destroy());
        await new Promise<void>((resolve, reject) => {
            server.once("error", reject);
            server.listen(socket, resolve);
        });
        chmodSync(socket, 0o660);
        this.server = server;
        try {
            this.process = await this.sandbox.startModel(
                scope,
                this.d.attempt_id ? "attempt" : "probe",
                this.d.policy.sandbox,
                {
                    setup_operation_id: this.d.setup_operation_id ?? scope,
                    assertCurrent: () => {
                        this.authority.assertLocal?.();
                        if (this.authority.signal.aborted || Date.now() >= this.authority.deadline)
                            throw new Error("Native authority expired");
                    },
                    deadline: this.authority.deadline,
                    signal: this.authority.signal,
                },
                this.image,
                socket,
                {model: this.d.provider.model_id, tools: this.tools.catalog},
            );
            this.connection = acp
                .client({name: "grow-runner"})
                .onRequest(
                    "session/request_permission",
                    (ctx) => permissionDecision(ctx.params.options) as never,
                )
                .onNotification("session/update", (ctx) => {
                    if (!this.initialized || !this.busy) return;
                    const update = ctx.params.update;
                    if (
                        update.sessionUpdate === "agent_message_chunk" &&
                        update.content.type === "text"
                    ) {
                        this.answer += update.content.text;
                        if (Buffer.byteLength(this.answer) > 51200) {
                            this.failure = true;
                            void this.cancel();
                        }
                    }
                })
                .connect(
                    acp.ndJsonStream(
                        Writable.toWeb(this.process.child.stdin) as never,
                        Readable.toWeb(this.process.child.stdout) as never,
                    ),
                );
            const init = await this.connection.agent.request("initialize", {
                protocolVersion: acp.PROTOCOL_VERSION,
                clientCapabilities: {auth: {_meta: {gateway: true}}},
            });
            if (
                init.protocolVersion !== acp.PROTOCOL_VERSION ||
                init.agentInfo?.version !== "1.12.0"
            )
                throw new Error("Unsupported ACP version");
            this.initialized = true;
            await this.connection.agent.request("authenticate", {
                methodId: "gateway",
                _meta: {
                    gateway: {
                        baseUrl: "http://127.0.0.1:39393/v1",
                        headers: {},
                        providerName: "Grow controlled broker",
                    },
                },
            });
            const session = await this.connection.agent.request("session/new", {
                cwd: "/workspace",
                mcpServers: [],
            });
            this.session = session.sessionId;
        } catch (e) {
            await this.close();
            throw e;
        }
    }
    async sendTurn(text: string, inputId: string): Promise<string> {
        if (!this.connection || !this.session || this.busy || this.inputs.has(inputId))
            throw new Error("ACP session unavailable");
        await this.authority.assertCurrent();
        this.busy = true;
        this.answer = "";
        this.failure = false;
        this.inputs.add(inputId);
        try {
            const prompt = this.checkpoint + text;
            this.checkpoint = "";
            const response = await this.connection.agent.request("session/prompt", {
                sessionId: this.session,
                prompt: [{type: "text", text: prompt}],
            });
            await this.authority.assertCurrent();
            if (this.failure || response.stopReason !== "end_turn" || this.authority.signal.aborted)
                throw new Error("Native turn did not complete");
            return this.model.filter.text(this.answer);
        } finally {
            this.busy = false;
        }
    }
    async probe(): Promise<Data> {
        await this.startSession();
        const text = await this.sendTurn("Reply with PROBE_OK.", randomUUID());
        return {chat_ready: text.trim() === "PROBE_OK", native_resume: "unsupported"};
    }
    async resume(checkpoint: Data): Promise<boolean> {
        this.checkpoint = `Untrusted previous checkpoint data:\n${this.model.filter.text(String(checkpoint.summary ?? "")).slice(0, 20000)}\nCurrent task:\n`;
        return false;
    }
    async cancel(): Promise<void> {
        if (this.connection && this.session)
            await this.connection.agent
                .notify("session/cancel", {sessionId: this.session})
                .catch(() => {});
        await this.close();
    }
    async close(): Promise<void> {
        this.initialized = false;
        this.connection?.close();
        this.connection = null;
        await this.process?.close();
        this.process = null;
        this.server?.closeAllConnections();
        if (this.server) await new Promise<void>((resolve) => this.server!.close(() => resolve()));
        this.server = null;
    }
}
