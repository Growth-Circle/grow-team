import {request} from "node:http";
const config = JSON.parse(process.env.GROW_NATIVE_CONFIG);
const allowed = new Set([
    "initialize",
    "thread/start",
    "turn/start",
    "turn/interrupt",
    "thread/unsubscribe",
    "thread/read",
    "account/read",
    "config/read",
    "model/list",
    "mcpServerStatus/list",
    "skills/list",
]);
export function constrain(request) {
    if (!allowed.has(request.method))
        throw Error("Unproved native request denied: " + request.method);
    if (request.method === "thread/start" || request.method === "turn/start") {
        const p = request.params;
        if (!p || (p.cwd && p.cwd !== "/workspace")) throw Error("Native workspace denied");
        request.params = {
            ...p,
            environments: [],
            approvalPolicy: "on-request",
            approvalsReviewer: "user",
            model: config.model,
        };
        if (request.method === "thread/start") {
            request.params.dynamicTools = config.tools.map((t) => ({
                type: "function",
                name: t.name,
                description: t.description,
                inputSchema: t.parameters,
                deferLoading: false,
            }));
            request.params.sandbox = "read-only";
            request.params.config = {
                ...p.config,
                approval_policy: "on-request",
                approvals_reviewer: "user",
            };
        } else
            request.params.sandboxPolicy = {type: "externalSandbox", networkAccess: "restricted"};
    }
    return request;
}
export function exchange(path, value) {
    return new Promise((resolve, reject) => {
        const body = Buffer.from(JSON.stringify(value));
        if (body.length > 2 * 1024 * 1024) {
            reject(Error("Request size"));
            return;
        }
        const r = request(
            {
                socketPath: "/grow/broker.sock",
                path,
                method: "POST",
                headers: {"Content-Type": "application/json", "Content-Length": body.length},
            },
            (res) => {
                const chunks = [];
                let size = 0;
                res.on("data", (b) => {
                    size += b.length;
                    if (size > 2 * 1024 * 1024) r.destroy(Error("Response size"));
                    else chunks.push(b);
                });
                res.on("end", () => {
                    try {
                        if (res.statusCode !== 200) throw Error("Broker denied");
                        resolve(JSON.parse(Buffer.concat(chunks)));
                    } catch {
                        reject(Error("Broker denied"));
                    }
                });
                res.on("error", () => reject(Error("Broker unavailable")));
            },
        );
        r.setTimeout(60000, () => r.destroy(Error("Broker deadline")));
        r.on("error", () => reject(Error("Broker unavailable")));
        r.end(body);
    });
}
export async function tool(params) {
    try {
        return await exchange("/tool", params);
    } catch {
        return {
            success: false,
            contentItems: [{type: "inputText", text: "Grow tool authority unavailable"}],
        };
    }
}
