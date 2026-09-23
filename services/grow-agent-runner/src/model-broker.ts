import {randomUUID} from "node:crypto";
import type {JournalLog} from "./journal.js";
import {lookup} from "node:dns/promises";
import {isIP} from "node:net";
import {request as httpRequest} from "node:http";
import {request as httpsRequest} from "node:https";
import {setTimeout as sleep} from "node:timers/promises";
import type {Data} from "./protocol.js";
import {
    encode,
    decode,
    ProviderFailure,
    type Message,
    type ToolDefinition,
    type ModelTurn,
} from "./codecs.js";
import {SecretFilter} from "./redaction.js";
export interface ModelAuthority {
    signal: AbortSignal;
    deadline: number;
    assertCurrent(): Promise<void>;
    assertLocal?(): void;
}
export function addressClass(address: string): "public" | "private" | "loopback" | "denied" {
    const family = isIP(address);
    if (family === 4) {
        const [a = 0, b = 0, c = 0] = address.split(".").map(Number);
        if (
            (a === 169 && b === 254) ||
            a === 0 ||
            a >= 224 ||
            (a === 100 && b === 100 && c === 100) ||
            (a === 192 && b === 0 && c === 0)
        )
            return "denied";
        if (a === 127) return "loopback";
        if (
            a === 10 ||
            (a === 172 && b >= 16 && b <= 31) ||
            (a === 192 && b === 168) ||
            (a === 100 && b >= 64 && b <= 127)
        )
            return "private";
        // Documentation, benchmark, and reserved ranges are not approved provider destinations.
        if (
            (a === 198 && [18, 19, 51].includes(b)) ||
            (a === 203 && b === 0) ||
            (a === 192 && b === 0)
        )
            return "denied";
        return "public";
    }
    if (family === 6) {
        const value = new URL(`http://[${address}]/`).hostname.slice(1, -1).toLowerCase();
        if (value === "fd00:ec2::254") return "denied";
        if (value === "::1") return "loopback";
        // Deny mapped, translated, multicast, link-local, and unusual compressed ranges.
        if (value === "::" || value.startsWith("::") || /^(ff|fe[89ab]|64:ff9b)/.test(value))
            return "denied";
        if (/^f[cd]/.test(value)) return "private";
        if (
            !/^[23][0-9a-f]{3}:/.test(value) ||
            value.startsWith("2001:db8:") ||
            value.startsWith("2001:0:")
        )
            return "denied";
        return "public";
    }
    return "denied";
}
// Some gateways return model reasoning inside <think> tags in the answer text.
// The answer keeps only the text outside those tags.
const REASONING_BLOCK = /<think>[\s\S]*?<\/think>/g;

export function stripReasoning(text: string): string {
    return text.replace(REASONING_BLOCK, "").trim();
}

export function approveAddress(url: URL, address: string, policies: Data[]): void {
    const hostname = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
    if (/metadata|metadata\.google\.internal/i.test(hostname))
        throw new Error("Metadata target denied");
    const kind = addressClass(address),
        port = Number(url.port || (url.protocol === "https:" ? 443 : 80));
    if (kind === "denied") throw new Error("Provider address denied");
    for (const policy of policies) {
        const target = policy.targets?.find(
            (t: Data) => t.hostname.toLowerCase() === hostname && t.port === port,
        );
        if (
            kind !== "public" &&
            (!target?.allow_private ||
                (kind === "loopback" && url.protocol === "http:" && !target.allow_http_loopback))
        )
            throw new Error("Private provider target is not approved");
        if (
            url.protocol === "http:" &&
            !(
                (kind === "loopback" && target?.allow_http_loopback) ||
                (kind === "private" && target?.allow_private && target?.allow_http_private)
            )
        )
            throw new Error("Provider requires HTTPS");
    }
}
// Destroys the connection once `windowMs()` has passed since the last `arm()`. A caller
// that keeps calling `arm()` while bytes are still arriving never looks idle, so a slow
// but active response is not treated the same as one that has actually stalled.
export function idleTimer(onExpire: () => void, windowMs: () => number): {arm(): void; clear(): void} {
    let handle: ReturnType<typeof setTimeout> | undefined;
    return {
        arm() {
            clearTimeout(handle);
            handle = setTimeout(onExpire, Math.max(1, windowMs()));
        },
        clear() {
            clearTimeout(handle);
        },
    };
}
export class ModelBudgetLedger {
    constructor(
        private journal: JournalLog,
        private scope: string,
    ) {}
    used(): {input: number; output: number} {
        return this.journal
            .list("model-reservation")
            .filter((e) => e.request.scope === this.scope)
            .reduce(
                (total, e) => ({
                    input: total.input + e.request.input,
                    output:
                        total.output + (e.state === "done" ? e.response!.output : e.request.output),
                }),
                {input: 0, output: 0},
            );
    }
    reserve(input: number, output: number): string {
        const id = `model-reservation:${randomUUID()}`;
        this.journal.prepare("model-reservation", id, "local", {scope: this.scope, input, output});
        this.journal.uncertain(id);
        return id;
    }
    settle(id: string, output: number): void {
        this.journal.complete(id, {output});
    }
}
export class ModelBroker {
    readonly observed = {streaming: false, usage: false, tool_request_rejected: false};
    private busy = false;
    private inputUsed = 0;
    private outputUsed = 0;
    private requests = 0;
    readonly filter: SecretFilter;
    constructor(
        private provider: Data,
        private policy: Data,
        private budget: Data,
        private authority: ModelAuthority,
        private credential: () => Promise<string | null>,
        filter = new SecretFilter(),
        private ledger?: ModelBudgetLedger,
    ) {
        this.provider = structuredClone(provider);
        this.policy = structuredClone(policy);
        this.budget = structuredClone(budget);
        this.filter = filter;
        const url = new URL(provider.base_url);
        if (
            !["https:", "http:"].includes(url.protocol) ||
            url.username ||
            url.password ||
            url.search ||
            url.hash ||
            !provider.allowed_models.includes(provider.model_id)
        )
            throw new Error("Invalid fixed provider");
        if (policy.hard_cost_cap)
            throw new Error("Priced budget reservations require certification");
    }
    private async current(): Promise<void> {
        this.authority.assertLocal?.();
        if (this.authority.signal.aborted || Date.now() >= this.authority.deadline)
            throw new Error("Model authority expired");
        await this.authority.assertCurrent();
        if (this.authority.signal.aborted || Date.now() >= this.authority.deadline)
            throw new Error("Model authority expired");
    }
    async turn(messages: Message[], tools: ToolDefinition[]): Promise<ModelTurn> {
        if (this.busy) throw new Error("Concurrent model request denied");
        this.busy = true;
        try {
            return await this.turnOnce(messages, tools);
        } finally {
            this.busy = false;
        }
    }
    private async turnOnce(messages: Message[], tools: ToolDefinition[]): Promise<ModelTurn> {
        await this.current();
        if (this.ledger) {
            const used = this.ledger.used();
            this.inputUsed = used.input;
            this.outputUsed = used.output;
        }
        if (++this.requests > this.budget.tool_rounds + this.budget.context_recoveries + 2)
            throw new Error("Model round budget exhausted");
        const input =
            Buffer.byteLength(JSON.stringify(messages)) + Buffer.byteLength(JSON.stringify(tools));
        // Byte reservations are conservative for supported tokenizers. Unknown tokenizers require certification.
        if (
            input > this.provider.context_window_tokens ||
            input + this.inputUsed > this.budget.input_tokens
        )
            throw new ProviderFailure("context");
        const limit = Math.min(
            this.provider.max_output_tokens,
            this.budget.output_tokens - this.outputUsed,
        );
        if (limit <= 0) throw new Error("Model output budget exhausted");
        const payload = encode(
            this.provider.api_mode,
            this.provider.model_id,
            messages,
            tools,
            limit,
        );
        const base = new URL(
            this.provider.base_url.endsWith("/")
                ? this.provider.base_url
                : this.provider.base_url + "/",
        );
        const url = new URL(
            this.provider.api_mode === "responses" ? "responses" : "chat/completions",
            base,
        );
        for (let retry = 0; ; retry++) {
            await this.current();
            if (
                this.inputUsed + input > this.budget.input_tokens ||
                this.outputUsed + limit > this.budget.output_tokens
            )
                throw new Error("Model retry budget exhausted");
            // Reserve before dispatch. Response loss does not refund a reservation.
            const reservation = this.ledger?.reserve(input, limit);
            this.inputUsed += input;
            this.outputUsed += limit;
            try {
                const secret = await this.credential();
                if (secret) this.filter.add(secret);
                await this.current();
                const response = await this.exchange(url, payload, secret);
                await this.current();
                const result = decode(this.provider.api_mode, response.bytes, response.contentType);
                this.observed.streaming ||= /text\/event-stream/i.test(response.contentType);
                this.observed.usage ||=
                    result.inputTokens !== undefined && result.outputTokens !== undefined;
                if (result.outputTokens !== undefined && result.outputTokens > limit)
                    throw new ProviderFailure("protocol");
                // Keep input reservations. Refund output only after a measured successful response.
                if (result.outputTokens !== undefined) {
                    this.outputUsed -= limit - result.outputTokens;
                    if (reservation) this.ledger!.settle(reservation, result.outputTokens);
                }
                result.text = this.filter.text(stripReasoning(result.text));
                return result;
            } catch (e) {
                if (
                    !(e instanceof ProviderFailure) ||
                    e.kind !== "transient" ||
                    retry >= this.budget.transport_retries
                )
                    throw e;
                await this.current();
                const delay = Math.max(250 * 2 ** retry, e.retryAfter);
                if (Date.now() + delay >= this.authority.deadline)
                    throw new Error("Retry exceeds deadline");
                await sleep(delay, undefined, {signal: this.authority.signal});
            }
        }
    }
    private async exchange(
        url: URL,
        payload: Data,
        secret: string | null,
    ): Promise<{bytes: Buffer; contentType: string}> {
        const hostname = url.hostname.replace(/^\[|\]$/g, "");
        const addresses = await lookup(hostname, {all: true, verbatim: true});
        if (!addresses.length) throw new Error("Provider DNS returned no addresses");
        for (const a of addresses)
            approveAddress(url, a.address, [this.provider.network, this.policy.network]);
        await this.current();
        const selected = addresses[0]!;
        const body = Buffer.from(JSON.stringify(payload));
        if (body.length > 2 * 1024 * 1024) throw new Error("Provider request limit");
        return new Promise((resolve, reject) => {
            const idle = idleTimer(
                () => request.destroy(new Error("Deadline")),
                () => Math.min(60000, Math.max(1, this.authority.deadline - Date.now())),
            );
            const request = (url.protocol === "https:" ? httpsRequest : httpRequest)(
                url,
                {
                    method: "POST",
                    agent: false,
                    servername: hostname,
                    rejectUnauthorized: true,
                    lookup: ((_host: string, _options: unknown, callback: Function) =>
                        callback(null, selected.address, selected.family)) as never,
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "text/event-stream, application/json",
                        "Content-Length": body.length,
                        ...(secret ? {Authorization: `Bearer ${secret}`} : {}),
                    },
                    signal: this.authority.signal,
                },
                (response) => {
                    const chunks: Buffer[] = [];
                    let size = 0;
                    response.on("data", (chunk: Buffer) => {
                        // A response still sending bytes is not idle; only real silence,
                        // not the reply's total length, should destroy the request.
                        idle.arm();
                        size += chunk.length;
                        if (size > 2 * 1024 * 1024)
                            request.destroy(new ProviderFailure("protocol"));
                        else chunks.push(chunk);
                    });
                    response.on("error", () => reject(new ProviderFailure("protocol")));
                    response.on("end", () => {
                        const status = response.statusCode ?? 0;
                        if ([401, 403].includes(status)) {
                            reject(new ProviderFailure("auth"));
                            return;
                        }
                        if (status === 429 || status >= 500) {
                            const header = response.headers["retry-after"];
                            const seconds = Number(header);
                            const wait = Number.isFinite(seconds)
                                ? seconds * 1000
                                : Date.parse(String(header)) - Date.now();
                            const quota = /insufficient_quota|quota_exceeded/.test(
                                Buffer.concat(chunks).toString(),
                            );
                            reject(
                                new ProviderFailure(
                                    quota ? "quota" : "transient",
                                    Math.max(0, Math.min(60000, wait || 0)),
                                ),
                            );
                            return;
                        }
                        if (status < 200 || status >= 300) {
                            const context = /context_length_exceeded|context_window_exceeded/.test(
                                Buffer.concat(chunks).toString(),
                            );
                            if (
                                status === 400 &&
                                !context &&
                                Array.isArray(payload.tools) &&
                                payload.tools.length > 0
                            )
                                this.observed.tool_request_rejected = true;
                            reject(new ProviderFailure(context ? "context" : "protocol"));
                            return;
                        }
                        resolve({
                            bytes: Buffer.concat(chunks),
                            contentType: String(response.headers["content-type"] ?? ""),
                        });
                    });
                },
            );
            idle.arm();
            const pulse = setInterval(() => {
                try {
                    this.authority.assertLocal?.();
                    if (Date.now() >= this.authority.deadline) throw new Error("Deadline");
                } catch {
                    request.destroy(new Error("Authority expired"));
                }
            }, 100);
            request.once("close", () => {
                idle.clear();
                clearInterval(pulse);
            });
            // An uncertain network outcome is never retried. Only explicit 429/5xx are retried.
            request.on("error", () => reject(new ProviderFailure("protocol")));
            request.on("socket", (socket) =>
                socket.once("connect", () => {
                    if (
                        socket.remoteAddress !== selected.address &&
                        socket.remoteAddress !== `::ffff:${selected.address}`
                    )
                        request.destroy(new Error("Connection address changed"));
                }),
            );
            request.end(body);
        });
    }
}
