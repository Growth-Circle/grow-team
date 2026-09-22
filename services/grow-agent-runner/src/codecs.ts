import type {Data} from "./protocol.js";
export interface ToolCall {
    id: string;
    name: string;
    arguments: string;
}
export interface ModelTurn {
    text: string;
    calls: ToolCall[];
    inputTokens?: number;
    outputTokens?: number;
}
export interface Message {
    role: "user" | "assistant" | "tool";
    text: string;
    calls?: ToolCall[];
    callId?: string;
}
export interface ToolDefinition {
    name: string;
    description: string;
    parameters: Data;
}
export type Dialect = "chat_completions" | "responses";
export class ProviderFailure extends Error {
    constructor(
        readonly kind: "context" | "auth" | "quota" | "protocol" | "transient",
        readonly retryAfter = 0,
    ) {
        super(`Provider request failed: ${kind}`);
    }
}
export function encode(
    dialect: Dialect,
    model: string,
    messages: Message[],
    tools: ToolDefinition[],
    limit: number,
): Data {
    if (dialect === "chat_completions")
        return {
            model,
            stream: true,
            stream_options: {include_usage: true},
            max_completion_tokens: limit,
            messages: messages.map((m) =>
                m.role === "tool"
                    ? {role: "tool", tool_call_id: m.callId, content: m.text}
                    : {
                          role: m.role,
                          content: m.text,
                          ...(m.calls?.length
                              ? {
                                    tool_calls: m.calls.map((c) => ({
                                        id: c.id,
                                        type: "function",
                                        function: {name: c.name, arguments: c.arguments},
                                    })),
                                }
                              : {}),
                      },
            ),
            tools: tools.map((t) => ({type: "function", function: t})),
            parallel_tool_calls: false,
        };
    return {
        model,
        stream: true,
        store: false,
        max_output_tokens: limit,
        parallel_tool_calls: false,
        input: messages.flatMap((m) =>
            m.role === "tool"
                ? [{type: "function_call_output", call_id: m.callId, output: m.text}]
                : ([
                      ...(m.text ? [{role: m.role, content: m.text}] : []),
                      ...(m.calls ?? []).map((c) => ({
                          type: "function_call",
                          call_id: c.id,
                          name: c.name,
                          arguments: c.arguments,
                      })),
                  ] as Data[]),
        ),
        tools: tools.map((t) => ({type: "function", ...t})),
    };
}
// Consume a complete bounded response before a caller may execute a tool.
export function decode(
    dialect: Dialect,
    bytes: Buffer,
    contentType: string,
    maximum = 2 * 1024 * 1024,
): ModelTurn {
    if (bytes.length > maximum) throw new ProviderFailure("protocol");
    const result: ModelTurn = {text: "", calls: []};
    const calls = new Map<number, ToolCall>();
    let complete = false;
    const usage = (u: Data | undefined) => {
        if (!u) return;
        const input = u.input_tokens ?? u.prompt_tokens,
            output = u.output_tokens ?? u.completion_tokens;
        if (
            !Number.isSafeInteger(input) ||
            input < 0 ||
            !Number.isSafeInteger(output) ||
            output < 0
        )
            throw new ProviderFailure("protocol");
        result.inputTokens = input;
        result.outputTokens = output;
    };
    const consume = (data: Data, streaming: boolean) => {
        if (data.error) {
            const code = String(data.error.code ?? "");
            throw new ProviderFailure(
                /context_length|context_window/.test(code)
                    ? "context"
                    : /quota/.test(code)
                      ? "quota"
                      : "protocol",
            );
        }
        if (dialect === "chat_completions") {
            usage(data.usage);
            for (const c of data.choices ?? []) {
                if (c.index !== undefined && c.index !== 0) throw new ProviderFailure("protocol");
                const delta = streaming ? c.delta : c.message;
                if (delta?.content != null) {
                    if (typeof delta.content !== "string") throw new ProviderFailure("protocol");
                    result.text += delta.content;
                }
                for (const [position, part] of (delta?.tool_calls ?? []).entries()) {
                    const index = part.index ?? position;
                    if (!Number.isSafeInteger(index) || index < 0 || index > 39)
                        throw new ProviderFailure("protocol");
                    const call = calls.get(index) ?? {id: "", name: "", arguments: ""};
                    if (part.type !== undefined && part.type !== "function")
                        throw new ProviderFailure("protocol");
                    if (part.id) {
                        if (call.id && call.id !== part.id) throw new ProviderFailure("protocol");
                        call.id = part.id;
                    }
                    if (part.function?.name) call.name += part.function.name;
                    if (part.function?.arguments !== undefined) {
                        if (typeof part.function.arguments !== "string")
                            throw new ProviderFailure("protocol");
                        call.arguments += part.function.arguments;
                    }
                    calls.set(index, call);
                }
                if (c.finish_reason != null) {
                    if (!["stop", "tool_calls"].includes(c.finish_reason))
                        throw new ProviderFailure("protocol");
                    complete = true;
                }
            }
        } else {
            if (data.type === "response.failed" || data.type === "response.incomplete")
                throw new ProviderFailure("protocol");
            // Only the completed envelope is authoritative. Ignore reasoning and partial items.
            if (!streaming || data.type === "response.completed") {
                const response = streaming ? data.response : data;
                if (response.status !== "completed" || !Array.isArray(response.output))
                    throw new ProviderFailure("protocol");
                if (complete) throw new ProviderFailure("protocol");
                usage(response.usage);
                for (const item of response.output) {
                    if (item.type === "function_call")
                        result.calls.push({
                            id: item.call_id,
                            name: item.name,
                            arguments: item.arguments,
                        });
                    else if (item.type === "message")
                        for (const part of item.content ?? []) {
                            if (part.type === "output_text") {
                                if (typeof part.text !== "string")
                                    throw new ProviderFailure("protocol");
                                result.text += part.text;
                            }
                        }
                    else if (!["reasoning"].includes(item.type))
                        throw new ProviderFailure("protocol");
                }
                complete = true;
            }
        }
    };
    try {
        if (/text\/event-stream/i.test(contentType)) {
            const frames = bytes.toString("utf8").replace(/\r\n/g, "\n").split("\n\n");
            if (frames.at(-1)?.trim()) throw new ProviderFailure("protocol");
            for (const frame of frames) {
                const payload = frame
                    .split("\n")
                    .filter((x) => x.startsWith("data:"))
                    .map((x) => x.slice(5).trimStart())
                    .join("\n");
                if (!payload || payload === "[DONE]") continue;
                consume(JSON.parse(payload), true);
            }
        } else consume(JSON.parse(bytes.toString("utf8")), false);
    } catch (e) {
        if (e instanceof ProviderFailure) throw e;
        throw new ProviderFailure("protocol");
    }
    result.calls.push(...calls.values());
    if (
        !complete ||
        result.calls.length > 40 ||
        new Set(result.calls.map((c) => c.id)).size !== result.calls.length
    )
        throw new ProviderFailure("protocol");
    for (const c of result.calls)
        if (
            typeof c.id !== "string" ||
            !/^[\w-]{1,200}$/.test(c.id) ||
            typeof c.name !== "string" ||
            !/^[\w-]{1,80}$/.test(c.name) ||
            typeof c.arguments !== "string" ||
            Buffer.byteLength(c.arguments) > 65536
        )
            throw new ProviderFailure("protocol");
    return result;
}
