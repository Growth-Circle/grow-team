#!/usr/bin/env node
// Probe an Anthropic-compatible /v1/messages endpoint before it is added to
// runtime.json's fast_lane_providers (internals/docs/spec/2026-09-24-agent-
// fast-lane.md §9 "fast_lane per provider", §12 step 12). It checks the two
// things the fast lane needs and the SDK alone will not tell an operator
// ahead of time: the endpoint streams Server-Sent Events, and it can return
// a tool_use block. This script makes its own HTTP request on purpose - it
// is checking wire compatibility of a third-party endpoint, not calling
// Anthropic's API for a feature, so it does not go through @anthropic-ai/sdk.
//
// Usage:
//   ANTHROPIC_API_KEY=... node scripts/probe-anthropic-endpoint.mjs \
//     --base-url https://host/v1 --model <id> [options]
//
// Options:
//   --auth-header <style>    "x-api-key" (default, Anthropic's own header) or
//                             "bearer" for a provider that wants
//                             Authorization: Bearer <key> instead.
//   --no-tool                Skip the tool_use check; stream check only.
//
// The key is read only from ANTHROPIC_API_KEY, never from a flag: an argv
// value is visible to every other process on the host (e.g. `ps`).
//
// Exit code 0 = both checks the runtime.json entry needs passed. Exit code 1
// = a check failed; the printed report says which one and why.

function parseArgs(argv) {
    const out = {
        baseUrl: null,
        model: null,
        apiKey: process.env.ANTHROPIC_API_KEY ?? null,
        authHeader: "x-api-key",
        tool: true,
    };
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i];
        if (a === "--base-url") out.baseUrl = argv[++i];
        else if (a === "--model") out.model = argv[++i];
        else if (a === "--auth-header") out.authHeader = argv[++i];
        else if (a === "--no-tool") out.tool = false;
        else throw new Error(`Unknown argument: ${a}`);
    }
    if (!out.baseUrl || !out.model) throw new Error("--base-url and --model are required");
    if (!["x-api-key", "bearer"].includes(out.authHeader))
        throw new Error('--auth-header must be "x-api-key" or "bearer"');
    return out;
}

// Splits one SSE byte stream into {event, data} pairs. Anthropic's stream
// frames each event as "event: <type>\ndata: <json>\n\n"; this keeps the
// parsing local to this script rather than pulling in an SSE dependency for
// one probe.
function* splitEvents(text) {
    for (const block of text.split("\n\n")) {
        if (!block.trim()) continue;
        let type = null,
            data = null;
        for (const line of block.split("\n")) {
            if (line.startsWith("event:")) type = line.slice(6).trim();
            else if (line.startsWith("data:")) data = line.slice(5).trim();
        }
        if (type && data) yield {type, data};
    }
}

async function probe({baseUrl, model, apiKey, authHeader, tool}) {
    // Match src/anthropic-runtime.ts's own client(): it strips a trailing /v1
    // from base_url and gives that to the SDK, which appends v1/messages
    // itself. A bare "messages" resolved against the raw base_url (still
    // ending in /v1) hit a path one segment short of what the runtime sends.
    const url = new URL("v1/messages", `${baseUrl.replace(/\/v1\/?$/, "")}/`);
    const headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        Accept: "text/event-stream",
        ...(apiKey
            ? authHeader === "bearer"
                ? {Authorization: `Bearer ${apiKey}`}
                : {"x-api-key": apiKey}
            : {}),
    };
    const body = {
        model,
        max_tokens: 256,
        stream: true,
        messages: [
            {
                role: "user",
                content: tool
                    ? "Call the get_probe_time tool now, with no arguments."
                    : "Reply with exactly: PROBE_OK",
            },
        ],
        ...(tool
            ? {
                  tools: [
                      {
                          name: "get_probe_time",
                          description: "Return the current time.",
                          input_schema: {
                              type: "object",
                              properties: {},
                              additionalProperties: false,
                          },
                      },
                  ],
              }
            : {}),
    };
    const response = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        redirect: "manual",
        signal: AbortSignal.timeout(60_000),
    });
    const report = {
        url: url.toString(),
        status: response.status,
        content_type: response.headers.get("content-type") ?? "",
        streamed: false,
        tool_use_seen: false,
        stop_reason: null,
        checks: {stream: false, tool_use: !tool},
    };
    if (response.status >= 300 && response.status < 400) {
        report.error = "Endpoint returned a redirect; the fast lane never follows one";
        return report;
    }
    const text = await response.text();
    if (response.status !== 200) {
        report.error = text.slice(0, 2000);
        return report;
    }
    report.streamed = /text\/event-stream/i.test(report.content_type);
    report.checks.stream = report.streamed;
    for (const {type, data} of splitEvents(text)) {
        if (type === "content_block_start") {
            const parsed = JSON.parse(data);
            if (parsed.content_block?.type === "tool_use") report.tool_use_seen = true;
        }
        if (type === "message_delta") {
            const parsed = JSON.parse(data);
            if (parsed.delta?.stop_reason) report.stop_reason = parsed.delta.stop_reason;
        }
    }
    if (tool) report.checks.tool_use = report.tool_use_seen && report.stop_reason === "tool_use";
    return report;
}

const args = parseArgs(process.argv.slice(2));
const report = await probe(args);
console.log(JSON.stringify(report, null, 2));
process.exit(Object.values(report.checks).every(Boolean) ? 0 : 1);
