import {createInterface} from "node:readline";
import {EndpointRuntime, type RuntimeTools} from "./runtime.js";
import type {ModelBroker} from "./model-broker.js";
import type {Data} from "./protocol.js";
import {SecretFilter} from "./redaction.js";
import {exchange, requestTimeoutMs, resolveDeadline} from "./broker-exchange.js";
const config = JSON.parse(process.env.GROW_NATIVE_CONFIG!);
const deadline = resolveDeadline(config);
const send = (path: string, value: unknown) =>
    exchange("/grow/broker.sock", path, value, requestTimeoutMs(deadline));
const controller = new AbortController();
const runtime = new EndpointRuntime(
    {
        filter: new SecretFilter(),
        turn: (messages: Data[]) => send("/model", {messages}),
    } as unknown as ModelBroker,
    {
        catalog: config.tools,
        call: (value: Data) => send("/tool", value).then((r) => r.text),
    } as unknown as RuntimeTools,
    {
        signal: controller.signal,
        deadline,
        assertCurrent: async () => {
            if (controller.signal.aborted) throw new Error("Cancelled");
        },
    },
    config.budget,
);
const lines = createInterface({input: process.stdin, crlfDelay: Infinity});
let busy = false;
for await (const line of lines) {
    if (Buffer.byteLength(line) > 1024 * 1024 || busy) process.exit(1);
    busy = true;
    try {
        const value = JSON.parse(line);
        let result: unknown;
        if (value.method === "start") result = await runtime.startSession();
        else if (value.method === "turn")
            result = await runtime.sendTurn(value.text, value.inputId);
        else if (value.method === "resume") result = await runtime.resume(value.checkpoint);
        else throw new Error("Unsupported endpoint request");
        process.stdout.write(JSON.stringify({id: value.id, result: result ?? null}) + "\n");
    } catch {
        process.stdout.write(JSON.stringify({error: "Endpoint runtime stopped"}) + "\n");
    } finally {
        busy = false;
    }
}
controller.abort();
