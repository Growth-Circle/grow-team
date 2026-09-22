import {createInterface} from "node:readline";
import {request} from "node:http";
import {EndpointRuntime, type RuntimeTools} from "./runtime.js";
import type {ModelBroker} from "./model-broker.js";
import type {Data} from "./protocol.js";
import {ProviderFailure} from "./codecs.js";
import {SecretFilter} from "./redaction.js";
const config = JSON.parse(process.env.GROW_NATIVE_CONFIG!);
function exchange(path: string, value: unknown): Promise<any> {
    return new Promise((resolve, reject) => {
        const body = Buffer.from(JSON.stringify(value));
        const req = request(
            {
                socketPath: "/grow/broker.sock",
                path,
                method: "POST",
                headers: {"Content-Type": "application/json", "Content-Length": body.length},
            },
            (res) => {
                let size = 0;
                const chunks: Buffer[] = [];
                res.on("data", (b) => {
                    size += b.length;
                    if (size > 2 * 1024 * 1024) req.destroy();
                    else chunks.push(b);
                });
                res.on("error", () => reject(new Error("Broker unavailable")));
                res.on("end", () => {
                    try {
                        const data = JSON.parse(Buffer.concat(chunks).toString());
                        if (res.statusCode !== 200) {
                            reject(
                                data.error === "context"
                                    ? new ProviderFailure("context")
                                    : new Error("Broker denied"),
                            );
                            return;
                        }
                        resolve(data);
                    } catch {
                        reject(new Error("Broker denied"));
                    }
                });
            },
        );
        req.on("error", () => reject(new Error("Broker unavailable")));
        req.setTimeout(60000, () => req.destroy());
        req.end(body);
    });
}
const controller = new AbortController();
const runtime = new EndpointRuntime(
    {
        filter: new SecretFilter(),
        turn: (messages: Data[]) => exchange("/model", {messages}),
    } as unknown as ModelBroker,
    {
        catalog: config.tools,
        call: (call: Data) => exchange("/tool", call).then((r) => r.text),
    } as unknown as RuntimeTools,
    {
        signal: controller.signal,
        deadline: Date.now() + config.budget.active_seconds * 1000,
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
