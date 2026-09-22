import {createServer} from "node:http";
import {spawn} from "node:child_process";
import {mkdirSync, readFileSync} from "node:fs";
import {createHash} from "node:crypto";
import {exchange} from "./policy.mjs";
const manifest = JSON.parse(readFileSync(new URL("./patch-manifest.json", import.meta.url)));
for (const [name, expected] of [
    ["patched-adapter.mjs", manifest.patched_sha256],
    ["grow-policy.mjs", manifest.policy_sha256],
    ["policy.mjs", manifest.policy_sha256],
])
    if (
        createHash("sha256")
            .update(readFileSync(new URL(name, import.meta.url)))
            .digest("hex") !== expected
    )
        throw Error("Native patch identity mismatch");
const config = JSON.parse(process.env.GROW_NATIVE_CONFIG);
const server = createServer(async (req, res) => {
    try {
        if (
            req.method !== "POST" ||
            req.url !== "/v1/responses" ||
            req.headers.upgrade ||
            (req.headers["transfer-encoding"] && req.headers["content-length"])
        )
            throw Error("Route denied");
        let size = 0;
        const chunks = [];
        for await (const b of req) {
            size += b.length;
            if (size > 2 * 1024 * 1024) throw Error("Request limit");
            chunks.push(b);
        }
        const turn = await exchange("/model", JSON.parse(Buffer.concat(chunks)));
        const id = "resp_grow_" + Date.now();
        const output = [];
        for (const c of turn.calls)
            output.push({
                type: "function_call",
                id: "item_" + c.id,
                call_id: c.id,
                name: c.name,
                arguments: c.arguments,
                status: "completed",
            });
        if (turn.text)
            output.push({
                type: "message",
                id: "message_" + id,
                role: "assistant",
                status: "completed",
                content: [{type: "output_text", text: turn.text, annotations: []}],
            });
        const response = {
            id,
            object: "response",
            created_at: Math.floor(Date.now() / 1000),
            model: config.model,
            status: "completed",
            output,
            usage: {
                input_tokens: turn.inputTokens ?? 0,
                output_tokens: turn.outputTokens ?? 0,
                total_tokens: (turn.inputTokens ?? 0) + (turn.outputTokens ?? 0),
            },
        };
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        let sequence = 0;
        const emit = (type, data) =>
            res.write(
                `event: ${type}\ndata: ${JSON.stringify({type, sequence_number: sequence++, ...data})}\n\n`,
            );
        emit("response.created", {response: {...response, status: "in_progress", output: []}});
        for (const [index, item] of output.entries()) {
            emit("response.output_item.added", {
                output_index: index,
                item: {...item, status: "in_progress"},
            });
            if (item.type === "message")
                emit("response.output_text.delta", {
                    output_index: index,
                    item_id: item.id,
                    content_index: 0,
                    delta: turn.text,
                });
            emit("response.output_item.done", {output_index: index, item});
        }
        emit("response.completed", {response});
        res.end();
    } catch {
        res.writeHead(403).end('{"error":{"code":"grow_authority_denied"}}');
    }
});
server.on("connect", (_req, socket) => socket.destroy());
server.on("upgrade", (_req, socket) => socket.destroy());
await new Promise((resolve) => server.listen(39393, "127.0.0.1", resolve));
mkdirSync("/tmp/home/.codex", {recursive: true});
const features = Object.fromEntries(
    [
        "multi_agent",
        "multi_agent_v2",
        "code_mode",
        "code_mode_only",
        "code_mode_host",
        "shell_tool",
        "view_image",
        "sleep_tool",
        "hooks",
        "plugins",
        "tool_suggest",
        "browser_use",
        "computer_use",
        "in_app_browser",
        "goals",
        "request_permissions_tool",
        "token_budget",
        "image_generation",
    ].map((x) => [x, false]),
);
const nativeConfig = {
    model: config.model,
    model_reasoning_effort: "low",
    web_search: "disabled",
    tools: {update_plan: {enabled: false}, experimental_request_user_input: {enabled: false}},
    orchestrator: {skills: {enabled: false}, mcp: {enabled: false}},
    skills: {bundled: {enabled: false}, include_instructions: false},
    features,
    analytics: {enabled: false},
    feedback: {enabled: false},
};
const child = spawn(process.execPath, ["/opt/grow/native/patched-adapter.mjs"], {
    cwd: "/workspace",
    env: {
        PATH: "/usr/local/bin:/usr/bin:/bin",
        HOME: "/tmp/home",
        CODEX_HOME: "/tmp/home/.codex",
        NO_BROWSER: "1",
        INITIAL_AGENT_MODE: "read-only",
        CODEX_PATH: "/opt/grow/node_modules/.bin/codex",
        CODEX_CONFIG: JSON.stringify(nativeConfig),
        GROW_NATIVE_CONFIG: JSON.stringify(config),
    },
    stdio: ["pipe", "pipe", "ignore"],
});
process.stdin.pipe(child.stdin);
child.stdout.pipe(process.stdout);
child.once("exit", (code) => {
    server.closeAllConnections();
    server.close();
    process.exitCode = code ?? 1;
});
