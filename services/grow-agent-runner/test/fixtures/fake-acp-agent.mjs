#!/usr/bin/env node
// Fake ACP agent for runner tests (contract 10.4). Speaks the stable ACP v1 stdio
// protocol as the AGENT side, matching what src/acp-runtime.ts drives as the client.
// Scripted entirely by the incoming prompt text, so a test needs no separate control
// channel: the ACP stdio pair stays pure protocol traffic.
//
// FAKE_ACP_LOG (required): path to a JSONL file. Every method this fixture receives,
// every permission request it sends, and the response it gets back are appended there
// as one JSON object per line, so a test can read them back after the fact.
import {appendFileSync} from "node:fs";
import {randomUUID} from "node:crypto";
import {Readable, Writable} from "node:stream";
import * as acp from "@agentclientprotocol/sdk";

const logPath = process.env.FAKE_ACP_LOG;
function record(event) {
    if (logPath) appendFileSync(logPath, JSON.stringify(event) + "\n");
}

// session/cancel is a plain ACP notification, not a JSON-RPC $/cancel_request for the
// in-flight session/prompt call, so the request handler's own AbortSignal never fires
// for it. Track cancellation per session instead, the same way the SDK's own example
// agent does with its pendingPrompt controller.
const sessions = new Map();

async function requestPermission(client, sessionId) {
    const allowId = `allow-${randomUUID()}`,
        rejectId = `decline-${randomUUID()}`;
    record({event: "permission_requested", sessionId, allowId, rejectId});
    const response = await client.request(acp.methods.client.session.requestPermission, {
        sessionId,
        toolCall: {
            toolCallId: "fixture-call",
            title: "Fixture change",
            kind: "edit",
            status: "pending",
            locations: [],
            rawInput: {},
        },
        options: [
            {kind: "allow_once", name: "Allow", optionId: allowId},
            {kind: "reject_once", name: "Reject", optionId: rejectId},
        ],
    });
    record({event: "permission_response", sessionId, response, offered: {allowId, rejectId}});
    return {response, rejectId};
}

async function chunk(client, sessionId, text) {
    await client.notify(acp.methods.client.session.update, {
        sessionId,
        update: {sessionUpdate: "agent_message_chunk", content: {type: "text", text}},
    });
}

async function prompt(params, client) {
    const sessionId = params.sessionId;
    const session = sessions.get(sessionId);
    const text = (params.prompt ?? []).map((part) => part.text ?? "").join("");
    if (text.includes("WAIT_FOR_CANCEL")) {
        await chunk(client, sessionId, "Waiting for approval. ");
        await requestPermission(client, sessionId);
        // AT-17: the client answers a permission request on its own, synchronous
        // policy (never a human wait), so a cancel sent right after the answer must
        // still land before this fixture acts on it. Give session/cancel a moment to
        // arrive, then check, instead of racing straight into the tool_ran record.
        await new Promise((resolve) => setTimeout(resolve, 100));
        if (session.abort.signal.aborted) return {stopReason: "cancelled"};
        record({event: "tool_ran", sessionId});
        return {stopReason: "end_turn"};
    }
    if (text.includes("REQUEST_PERMISSION")) {
        await chunk(client, sessionId, "Preparing the change. ");
        const {response, rejectId} = await requestPermission(client, sessionId);
        const declined =
            response.outcome.outcome === "cancelled" || response.outcome.optionId === rejectId;
        await chunk(client, sessionId, declined ? "Skipped by request." : "Applied.");
        if (!declined) record({event: "tool_ran", sessionId});
        return {stopReason: "end_turn"};
    }
    await chunk(client, sessionId, "Fixture answer.");
    return {stopReason: "end_turn"};
}

const stream = acp.ndJsonStream(Writable.toWeb(process.stdout), Readable.toWeb(process.stdin));
acp.agent({name: "fake-acp-agent"})
    .onRequest("initialize", (ctx) => {
        record({event: "request", method: "initialize", params: ctx.params});
        return {
            protocolVersion: acp.PROTOCOL_VERSION,
            agentInfo: {name: "fake-acp-agent", version: "1.12.0"},
            agentCapabilities: {loadSession: false},
        };
    })
    .onRequest("authenticate", (ctx) => {
        record({event: "request", method: "authenticate", params: ctx.params});
        return {};
    })
    .onRequest("session/new", (ctx) => {
        record({event: "request", method: "session/new", params: ctx.params});
        const sessionId = randomUUID();
        sessions.set(sessionId, {abort: new AbortController()});
        return {sessionId};
    })
    .onRequest("session/prompt", (ctx) => {
        record({event: "request", method: "session/prompt", params: ctx.params});
        return prompt(ctx.params, ctx.client);
    })
    .onNotification("session/cancel", (ctx) => {
        record({event: "notification", method: "session/cancel", params: ctx.params});
        sessions.get(ctx.params.sessionId)?.abort.abort();
    })
    .connect(stream);
