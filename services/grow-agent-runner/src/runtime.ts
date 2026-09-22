import {checkpointContext} from "./checkpoint-store.js";
import {randomUUID} from "node:crypto";
import type {Data} from "./protocol.js";
import type {JournalLog} from "./journal.js";
import {ModelBroker, type ModelAuthority} from "./model-broker.js";
import {ProviderFailure, type Message, type ToolDefinition, type ToolCall} from "./codecs.js";
import {validateTool} from "./tool-broker.js";
import {SecretFilter} from "./redaction.js";
export interface Runtime {
    probe(): Promise<Data>;
    startSession(): Promise<void>;
    sendTurn(text: string, inputId: string): Promise<string>;
    cancel(): Promise<void>;
    close(): Promise<void>;
    resume(checkpoint: Data): Promise<boolean>;
}
const schemas: Record<string, Data> = {
    read: {path: {type: "string"}},
    search: {path: {type: "string"}, query: {type: "string"}},
    edit: {path: {type: "string"}, content: {type: "string"}},
    shell: {argv: {type: "array", items: {type: "string"}}, cwd: {type: "string"}},
};
export function toolCatalog(d: Data): ToolDefinition[] {
    const kinds: string[] = [];
    if (d.repository && d.policy.actions.includes("repository.read")) kinds.push("read", "search");
    if (d.job_kind !== "answer" && d.repository) {
        if (d.policy.actions.includes("repository.edit")) kinds.push("edit");
        if (d.policy.actions.includes("shell.run")) kinds.push("shell");
    }
    return kinds.map((kind) => ({
        name: `grow_${kind}`,
        description: `Run the approved ${kind} tool.`,
        parameters: {
            type: "object",
            properties: schemas[kind],
            required: Object.keys(schemas[kind]!),
            additionalProperties: false,
        },
    }));
}
export class RuntimeTools {
    private busy = false;
    constructor(
        readonly catalog: ToolDefinition[],
        private scope: string,
        private journal: JournalLog,
        private authority: ModelAuthority,
        private filter: SecretFilter,
        private execute: (id: string, request: Data) => Promise<string>,
        private validate: (call: ToolCall, raw: Data) => Data = (call, raw) =>
            validateTool({...raw, kind: call.name.slice(5)}),
    ) {}
    async call(call: ToolCall): Promise<string> {
        if (this.busy) throw new Error("Concurrent dynamic tool denied");
        this.busy = true;
        try {
            return await this.callOnce(call);
        } finally {
            this.busy = false;
        }
    }
    private async callOnce(call: ToolCall): Promise<string> {
        if (
            !/^[\w-]{1,200}$/.test(call.id) ||
            typeof call.arguments !== "string" ||
            Buffer.byteLength(call.arguments) > 65536
        )
            throw new Error("Invalid model call identity or size");
        if (!this.catalog.some((t) => t.name === call.name))
            throw new Error("Tool is outside the effective catalog");
        this.filter.assertArguments(call);
        let raw: Data;
        try {
            raw = JSON.parse(call.arguments);
        } catch {
            throw new Error("Incomplete tool arguments");
        }
        if (!raw || typeof raw !== "object" || Array.isArray(raw) || Object.hasOwn(raw, "kind"))
            throw new Error("Invalid tool arguments");
        const tool = this.validate(call, raw);
        this.filter.assertArguments(tool);
        await this.authority.assertCurrent();
        if (this.authority.signal.aborted || Date.now() >= this.authority.deadline)
            throw new Error("Tool authority expired");
        const key = `model-call:${this.scope}:${call.id}`;
        if (this.journal.get(key)) throw new Error("Tool outcome requires reconciliation");
        const operationId = randomUUID();
        this.journal.prepare("model-call", key, "local", {
            call_id: call.id,
            operation_id: operationId,
            name: call.name,
            arguments: tool,
        });
        this.journal.uncertain(key);
        this.busy = true;
        try {
            const output = this.filter.text(await this.execute(operationId, tool));
            this.journal.complete(key, {call_id: call.id, operation_id: operationId, output});
            return output;
        } finally {
            this.busy = false;
        }
    }
}
export class EndpointRuntime implements Runtime {
    private messages: Message[] = [];
    private busy = false;
    private closed = false;
    private recovery = 0;
    private inputs = new Set<string>();
    constructor(
        private model: ModelBroker,
        private tools: RuntimeTools,
        private authority: ModelAuthority,
        private budget: Data,
    ) {}
    async startSession(): Promise<void> {
        await this.authority.assertCurrent();
    }
    async resume(checkpoint: Data): Promise<boolean> {
        // A checkpoint is data. Native sessions are not an execution authority.
        this.messages = [
            {
                role: "user",
                text: this.model.filter.text(checkpointContext(checkpoint)),
            },
        ];
        return false;
    }
    async probe(): Promise<Data> {
        await this.startSession();
        const text = await this.sendTurn("Reply with PROBE_OK.", randomUUID());
        return {
            chat_ready: text.trim() === "PROBE_OK",
            tool_calling: "unknown",
            streaming: "unknown",
            usage: "unknown",
        };
    }
    async sendTurn(text: string, inputId: string): Promise<string> {
        if (this.closed || this.busy || this.inputs.has(inputId))
            throw new Error("Runtime turn is unavailable or already applied");
        this.busy = true;
        this.inputs.add(inputId);
        this.messages.push({role: "user", text});
        const activeIndex = this.messages.length - 1;
        try {
            for (let round = 0; round <= this.budget.tool_rounds; round++) {
                await this.authority.assertCurrent();
                if (this.closed || this.authority.signal.aborted)
                    throw new Error("Runtime cancelled");
                let result;
                try {
                    result = await this.model.turn(this.messages, this.tools.catalog);
                } catch (e) {
                    if (
                        e instanceof ProviderFailure &&
                        e.kind === "context" &&
                        this.recovery++ < this.budget.context_recoveries &&
                        activeIndex > 0 &&
                        round === 0
                    ) {
                        this.messages = this.messages.slice(activeIndex);
                        continue;
                    }
                    throw e;
                }
                if (this.closed || this.authority.signal.aborted)
                    throw new Error("Runtime cancelled");
                this.messages.push({role: "assistant", text: result.text, calls: result.calls});
                if (!result.calls.length) return result.text;
                if (round === this.budget.tool_rounds)
                    throw new Error("Tool round budget exhausted");
                for (const call of result.calls) {
                    const output = await this.tools.call(call);
                    this.messages.push({role: "tool", text: output, callId: call.id});
                }
            }
            throw new Error("Runtime round limit");
        } finally {
            this.busy = false;
        }
    }
    async cancel(): Promise<void> {
        this.closed = true;
    }
    async close(): Promise<void> {
        this.closed = true;
    }
}
