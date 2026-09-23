import {request} from "node:http";
import {ProviderFailure} from "./codecs.js";

// Docker workspace seed and final-snapshot copy run outside the shell timeout itself
// (see sandbox.ts), so give the /tool ceiling room for that fixed overhead.
const TOOL_TIMEOUT_MARGIN_MS = 30_000;

// Neither route streams partial output back to the container, so this socket looks idle
// for the call's entire duration. The timeout must match the real ceiling of whatever it
// is waiting for: the descriptor's shell budget for /tool, the session deadline for /model.
export function requestTimeoutMs(
    path: string,
    shellTimeoutSeconds: number,
    deadline: number,
    now = Date.now(),
): number {
    const ceiling = path === "/tool" ? shellTimeoutSeconds * 1000 + TOOL_TIMEOUT_MARGIN_MS : Infinity;
    return Math.min(ceiling, Math.max(1, deadline - now));
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
