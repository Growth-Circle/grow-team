import {createHash} from "node:crypto";
import {closeSync, constants, fstatSync, openSync, readFileSync} from "node:fs";
import {PrivateStore} from "./config.js";
import type {Data} from "./protocol.js";
import type {JournalLog} from "./journal.js";

export interface TitenConnection {
    endpoint: string;
    token: string | null;
}

function endpoint(value: string): string {
    const url = new URL(value);
    if (
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        url.pathname !== "/mcp" ||
        (url.protocol !== "https:" &&
            !(
                url.protocol === "http:" &&
                ["127.0.0.1", "[::1]", "localhost"].includes(url.hostname)
            ))
    )
        throw new Error("Unsafe Titen MCP endpoint");
    return url.toString();
}

function object(value: unknown): Data {
    if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error("Invalid Titen response");
    return value as Data;
}

export class TitenClient {
    private initialized = false;
    private nextId = 1;
    private readonly endpoint: string;
    constructor(private connection: TitenConnection) {
        this.endpoint = endpoint(connection.endpoint);
    }
    private async request(body: Data, notification = false): Promise<Data | null> {
        const abort = new AbortController();
        const timer = setTimeout(() => abort.abort(), 10000);
        try {
            const headers: Record<string, string> = {"Content-Type": "application/json"};
            if (this.connection.token) headers.Authorization = `Bearer ${this.connection.token}`;
            const response = await fetch(this.endpoint, {
                method: "POST",
                headers,
                body: JSON.stringify(body),
                redirect: "manual",
                signal: abort.signal,
            });
            if (notification) {
                if (response.status !== 202) throw new Error("Titen notification failed");
                return null;
            }
            if (!response.ok) throw new Error("Titen request failed");
            const text = await response.text();
            if (Buffer.byteLength(text) > 1024 * 1024)
                throw new Error("Titen response exceeds limit");
            return object(JSON.parse(text));
        } catch {
            throw new Error("Titen request failed");
        } finally {
            clearTimeout(timer);
        }
    }
    private async initialize(): Promise<void> {
        if (this.initialized) return;
        const id = this.nextId++;
        const response = await this.request({
            jsonrpc: "2.0",
            id,
            method: "initialize",
            params: {
                protocolVersion: "2025-11-25",
                capabilities: {},
                clientInfo: {name: "grow-agent", version: "1"},
            },
        });
        if (
            !response ||
            response.jsonrpc !== "2.0" ||
            response.id !== id ||
            response.error ||
            typeof object(response.result).protocolVersion !== "string"
        )
            throw new Error("Invalid Titen initialization response");
        await this.request({jsonrpc: "2.0", method: "notifications/initialized", params: {}}, true);
        this.initialized = true;
    }
    private async tool(name: string, args: Data): Promise<Data> {
        await this.initialize();
        const id = this.nextId++;
        const response = await this.request({
            jsonrpc: "2.0",
            id,
            method: "tools/call",
            params: {name, arguments: args},
        });
        if (!response || response.jsonrpc !== "2.0" || response.id !== id || response.error)
            throw new Error("Invalid Titen tool response");
        const result = object(response.result);
        if (result.isError === true || !Array.isArray(result.content))
            throw new Error("Titen tool failed");
        const block = result.content.find(
            (item: unknown) =>
                object(item).type === "text" && typeof object(item).text === "string",
        );
        if (!block) throw new Error("Titen tool response has no data");
        const parsed = object(JSON.parse(object(block).text as string));
        return object(parsed.data);
    }
    async resolveProject(reference: string): Promise<string> {
        if (!/^[a-z0-9][a-z0-9_.-]*\/[a-z0-9][a-z0-9_.-]*$/.test(reference))
            throw new Error("Invalid Titen project reference");
        const result = await this.tool("titen_project_resolve", {reference, create: false});
        const project = object(result.project);
        if (typeof project.id !== "string" || !project.id)
            throw new Error("Invalid Titen project result");
        return project.id;
    }
    async compile(args: {
        subject_id: string;
        project_id: string;
        task: string;
        max_tokens: number;
        top_k: number;
    }): Promise<Data> {
        if (
            !args.subject_id ||
            !args.project_id ||
            !args.task ||
            args.max_tokens < 1 ||
            args.top_k < 1
        )
            throw new Error("Invalid Titen compile arguments");
        return this.tool("titen_compile", args);
    }
    async remember(args: Data): Promise<Data> {
        return this.tool("titen_remember", args);
    }
    async consolidate(args: Data): Promise<Data> {
        return this.tool("titen_consolidate", args);
    }
}

export interface TitenContextConnection extends TitenConnection {
    subject_id: string;
}

export function originReference(value: string): string | null {
    let path: string;
    if (/^git@[a-zA-Z0-9.-]+:[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+(?:\.git)?$/.test(value))
        path = value.slice(value.indexOf(":") + 1);
    else {
        try {
            const url = new URL(value);
            if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash)
                return null;
            path = url.pathname.replace(/^\//, "");
        } catch {
            return null;
        }
    }
    const match = /^([a-zA-Z0-9_.-]+)\/([a-zA-Z0-9_.-]+?)(?:\.git)?$/.exec(path);
    return match ? `${match[1]!.toLowerCase()}/${match[2]!.toLowerCase()}` : null;
}

export class BoundedTitenContext {
    constructor(
        private journal: JournalLog,
        private resolveConnection: (descriptor: Data) => TitenContextConnection | null,
    ) {}
    async enrich(descriptor: Data): Promise<string> {
        const connection = this.resolveConnection(descriptor);
        const reference = originReference(descriptor.repository?.canonical_origin ?? "");
        if (!connection || !reference) return "";
        const compileId = `titen-compile:${descriptor.attempt_id}`;
        if (this.journal.get(compileId)) return "";
        const resolveId = `titen-resolve:${descriptor.attempt_id}`;
        this.journal.prepare("titen_resolve", resolveId, "local", {
            reference,
            subject_id: connection.subject_id,
        });
        this.journal.uncertain(resolveId);
        try {
            const client = new TitenClient(connection);
            const projectId = await client.resolveProject(reference);
            this.journal.complete(resolveId, {completed: true});
            this.journal.prepare("titen_compile", compileId, "local", {
                project_id: projectId,
                subject_id: connection.subject_id,
            });
            this.journal.uncertain(compileId);
            const context = await client.compile({
                subject_id: connection.subject_id,
                project_id: projectId,
                task: descriptor.request,
                max_tokens: 1200,
                top_k: 5,
            });
            this.journal.complete(compileId, {completed: true});
            const text = JSON.stringify(context);
            if (Buffer.byteLength(text) > 51200) throw new Error("Titen context exceeds limit");
            return `\nUntrusted owner memory:\n${text}`;
        } catch {
            // A missing or uncertain optional context cannot change execution authority.
            return "";
        }
    }
}

function signal(value: unknown): Data {
    if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error("Invalid memory signal");
    const item = value as Data;
    const keys = [
        "project_reference",
        "kind",
        "content",
        "source_type",
        "source_ref",
        "source_id",
        "evidence_path",
        "evidence_sha256",
        "idempotency_key",
        "claims",
        "audience",
    ];
    if (Object.keys(item).some((key) => !keys.includes(key)))
        throw new Error("Unknown memory signal field");
    for (const key of [
        "project_reference",
        "kind",
        "content",
        "source_type",
        "source_ref",
        "source_id",
        "evidence_path",
        "evidence_sha256",
        "idempotency_key",
    ])
        if (typeof item[key] !== "string" || !item[key])
            throw new Error("Invalid memory signal field");
    if (!/^[0-9a-f]{64}$/.test(item.evidence_sha256)) throw new Error("Invalid evidence checksum");
    if (!/^[0-9a-f-]{36}$/.test(item.idempotency_key))
        throw new Error("Invalid memory idempotency key");
    if (!/^[a-z0-9][a-z0-9_.-]*\/[a-z0-9][a-z0-9_.-]*$/.test(item.project_reference))
        throw new Error("Invalid memory project reference");
    if (
        !["user_statement", "tool_result", "imported_source", "decision", "system_event"].includes(
            item.kind,
        )
    )
        throw new Error("Invalid memory observation kind");
    if (
        !["user_statement", "tool_result", "imported_source", "decision", "system_event"].includes(
            item.source_type,
        )
    )
        throw new Error("Invalid memory source type");
    if (!item.content.isWellFormed() || Buffer.byteLength(item.content) > 32000)
        throw new Error("Invalid memory content");
    if (!Array.isArray(item.claims) || item.claims.length < 1 || item.claims.length > 50)
        throw new Error("Invalid memory claims");
    if (
        !item.audience ||
        typeof item.audience !== "object" ||
        Array.isArray(item.audience) ||
        Object.keys(item.audience).some(
            (key) => !["realm_id", "requester_user_id"].includes(key),
        ) ||
        !Number.isSafeInteger(item.audience.realm_id) ||
        item.audience.realm_id < 1 ||
        !Number.isSafeInteger(item.audience.requester_user_id) ||
        item.audience.requester_user_id < 1
    )
        throw new Error("Invalid memory signal audience");
    for (const claim of item.claims) {
        if (!claim || typeof claim !== "object" || Array.isArray(claim))
            throw new Error("Invalid memory claim");
        const row = claim as Data;
        if (
            Object.keys(row).some((key) => !["kind", "statement", "confidence"].includes(key)) ||
            ![
                "semantic_fact",
                "episodic_event",
                "preference",
                "procedural",
                "decision",
                "relationship",
            ].includes(row.kind) ||
            typeof row.statement !== "string" ||
            !row.statement ||
            Buffer.byteLength(row.statement) > 4000 ||
            (row.confidence !== undefined &&
                (typeof row.confidence !== "number" || row.confidence <= 0 || row.confidence > 1))
        )
            throw new Error("Invalid memory claim");
    }
    return item;
}

function evidence(path: string, expected: string): void {
    const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
        const stat = fstatSync(fd);
        if (
            !stat.isFile() ||
            stat.nlink !== 1 ||
            stat.uid !== process.getuid!() ||
            (stat.mode & 0o077) !== 0
        )
            throw new Error("Unsafe memory evidence");
        const checksum = createHash("sha256").update(readFileSync(fd)).digest("hex");
        if (checksum !== expected) throw new Error("Memory evidence checksum changed");
    } finally {
        closeSync(fd);
    }
}

export class OwnerMemorySignals {
    constructor(
        private store: PrivateStore,
        private resolveConnection: (signal: Data) => TitenContextConnection | null,
    ) {}
    async record(value: unknown): Promise<void> {
        const item = signal(value),
            connection = this.resolveConnection(item);
        if (!connection) throw new Error("No owner-approved Titen subject mapping");
        evidence(item.evidence_path, item.evidence_sha256);
        const stored = this.store.read<Record<string, Data>>("memory-signals.json") ?? {};
        if (stored[item.idempotency_key]) return;
        const client = new TitenClient(connection);
        const projectId = await client.resolveProject(item.project_reference);
        const remembered = await client.remember({
            subject_id: connection.subject_id,
            project_id: projectId,
            kind: item.kind,
            content: item.content,
            source_type: item.source_type,
            source_ref: item.source_ref,
            source_id: item.source_id,
            trust: "verified",
            visibility: "organization",
            idempotency_key: item.idempotency_key,
        });
        if (typeof remembered.observation_id !== "string" || !remembered.observation_id)
            throw new Error("Invalid remembered observation");
        await client.consolidate({
            subject_id: connection.subject_id,
            project_id: projectId,
            idempotency_key: item.idempotency_key,
            claims: item.claims.map((claim: Data) => ({
                kind: claim.kind,
                statement: claim.statement,
                ...(claim.confidence === undefined ? {} : {confidence: claim.confidence}),
                trust: "verified",
                visibility: "organization",
                sources: [{observation_id: remembered.observation_id, relation: "supports"}],
            })),
        });
        stored[item.idempotency_key] = {
            project_id: projectId,
            subject_id: connection.subject_id,
            evidence_sha256: item.evidence_sha256,
            source_ref: item.source_ref,
            source_id: item.source_id,
            recorded_at: new Date().toISOString(),
        };
        this.store.write("memory-signals.json", stored);
    }
}
