import {createServer, type Server} from "node:http";
import {chmodSync} from "node:fs";
import {brokerSocket} from "./broker-socket.js";
import {randomUUID} from "node:crypto";
import type {Data} from "./protocol.js";
import {RootlessSandbox} from "./sandbox.js";
import {ProviderFailure} from "./codecs.js";
import {ModelBroker, type ModelAuthority} from "./model-broker.js";
import {RuntimeTools, type Runtime} from "./runtime.js";
export class ContainedEndpointRuntime implements Runtime {
    private process: Awaited<ReturnType<RootlessSandbox["startModel"]>> | null = null;
    private server: Server | null = null;
    private pending: {id: string; resolve: (v: any) => void; reject: (e: Error) => void} | null =
        null;
    private active = false;
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
                    !this.active ||
                    req.method !== "POST" ||
                    !["/model", "/tool"].includes(req.url ?? "")
                )
                    throw new Error("Endpoint route denied");
                let size = 0;
                const chunks: Buffer[] = [];
                for await (const b of req) {
                    size += b.length;
                    if (size > 2 * 1024 * 1024) throw new Error("Endpoint request limit");
                    chunks.push(b);
                }
                await this.authority.assertCurrent();
                const body = JSON.parse(Buffer.concat(chunks).toString());
                let output: unknown;
                if (req.url === "/model") {
                    if (Object.keys(body).join() !== "messages" || !Array.isArray(body.messages))
                        throw new Error("Invalid endpoint payload");
                    for (const m of body.messages)
                        if (
                            !["user", "assistant", "tool"].includes(m.role) ||
                            typeof m.text !== "string"
                        )
                            throw new Error("Invalid endpoint message");
                    output = await this.model.turn(body.messages, this.tools.catalog);
                } else output = {text: await this.tools.call(body)};
                await this.authority.assertCurrent();
                res.writeHead(200, {"Content-Type": "application/json"}).end(
                    JSON.stringify(output),
                );
            } catch (error) {
                res.writeHead(403).end(
                    JSON.stringify({
                        error: error instanceof ProviderFailure ? error.kind : "authority",
                    }),
                );
            }
        });
        server.on("connect", (_r, s) => s.destroy());
        server.on("upgrade", (_r, s) => s.destroy());
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
                            throw new Error("Endpoint authority revoked");
                    },
                    signal: this.authority.signal,
                    deadline: this.authority.deadline,
                },
                this.image,
                socket,
                {mode: "endpoint", tools: this.tools.catalog, budget: this.d.budget},
            );
            let buffer = "";
            this.process.child.stdout.on("data", (b) => {
                buffer += b.toString();
                if (Buffer.byteLength(buffer) > 1024 * 1024) {
                    this.pending?.reject(new Error("Endpoint output limit"));
                    void this.close();
                    return;
                }
                let position: number;
                while ((position = buffer.indexOf("\n")) >= 0) {
                    const line = buffer.slice(0, position);
                    buffer = buffer.slice(position + 1);
                    try {
                        const response = JSON.parse(line);
                        if (!this.pending || response.id !== this.pending.id || response.error)
                            throw new Error("Endpoint response rejected");
                        this.pending.resolve(response.result);
                        this.pending = null;
                    } catch {
                        this.pending?.reject(new Error("Endpoint response rejected"));
                        this.pending = null;
                    }
                }
            });
            this.process.child.on("error", () =>
                this.pending?.reject(new Error("Endpoint process failed")),
            );
            this.process.child.on("exit", () =>
                this.pending?.reject(new Error("Endpoint process stopped")),
            );
            await this.call({method: "start"});
        } catch (e) {
            await this.close();
            throw e;
        }
    }
    private async call(value: Data): Promise<any> {
        if (!this.process || this.pending) throw new Error("Endpoint process unavailable");
        await this.authority.assertCurrent();
        const id = randomUUID();
        return new Promise((resolve, reject) => {
            this.pending = {id, resolve, reject};
            this.process!.child.stdin.write(JSON.stringify({id, ...value}) + "\n");
        });
    }
    async sendTurn(text: string, inputId: string): Promise<string> {
        this.active = true;
        try {
            const answer = await this.call({method: "turn", text, inputId});
            if (typeof answer !== "string") throw new Error("Invalid endpoint answer");
            return this.model.filter.text(answer);
        } finally {
            this.active = false;
        }
    }
    async resume(checkpoint: Data): Promise<boolean> {
        return this.call({method: "resume", checkpoint});
    }
    async probe(): Promise<Data> {
        await this.startSession();
        return {
            chat_ready:
                (await this.sendTurn("Reply with PROBE_OK.", randomUUID())).trim() === "PROBE_OK",
        };
    }
    async cancel(): Promise<void> {
        await this.close();
    }
    async close(): Promise<void> {
        this.pending?.reject(new Error("Endpoint stopped"));
        this.pending = null;
        await this.process?.close();
        this.process = null;
        this.server?.closeAllConnections();
        if (this.server) await new Promise<void>((r) => this.server!.close(() => r()));
        this.server = null;
    }
}
