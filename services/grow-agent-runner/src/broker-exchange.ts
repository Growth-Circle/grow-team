import {request} from "node:http";
import {ProviderFailure} from "./codecs.js";

// Neither route streams partial output back to the container, so this socket looks idle
// for the call's entire duration (contract 10.1). The supervisor's abort timer already
// enforces the attempt deadline, and the sandbox enforces the shell timeout and approval
// expiry, so this ceiling only has to reach the deadline: it must never cut a long
// approval wait short, so /tool and /model share the same rule.
export function requestTimeoutMs(deadlineMs: number, now = Date.now()): number {
    return Math.max(1, deadlineMs - now);
}
// endpoint-child.ts supplies deadline_ms on every real container config (contract 10.1);
// the local computation only guards a config that omits it.
export function resolveDeadline(config: {deadline_ms?: number; budget: {active_seconds: number}}): number {
    return config.deadline_ms ?? Date.now() + config.budget.active_seconds * 1000;
}

export function exchange(
    socketPath: string,
    path: string,
    value: unknown,
    timeoutMs: number,
): Promise<any> {
    return new Promise((resolve, reject) => {
        const body = Buffer.from(JSON.stringify(value));
        const req = request(
            {
                socketPath,
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
        req.setTimeout(timeoutMs, () => req.destroy());
        req.end(body);
    });
}
