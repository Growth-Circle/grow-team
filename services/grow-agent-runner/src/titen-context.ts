import {createHash} from "node:crypto";
import {closeSync, constants, fstatSync, openSync, readFileSync} from "node:fs";
import {PrivateStore} from "./config.js";
import {canonical, type Data} from "./protocol.js";
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
        private runnerScope = "",
    ) {}
    async enrich(descriptor: Data): Promise<string> {
        try {
            const connection = this.resolveConnection(descriptor);
            const reference = originReference(descriptor.repository?.canonical_origin ?? "");
            if (
                !connection ||
                !reference ||
                typeof descriptor.job_id !== "string" ||
                !descriptor.job_id
            )
                return "";
            const audience = descriptor.audience;
            if (
                !audience ||
                !Number.isSafeInteger(audience.realm_id) ||
                !Number.isSafeInteger(audience.requester_user_id)
            )
                throw new Error("Invalid Titen context audience");
            const compileId = `titen-compile:job:${descriptor.job_id}`;
            if (this.journal.get(compileId)) return "";
            // Create the job fence before any optional Titen request.
            this.journal.prepare("titen_compile", compileId, "local", {
                job_id: descriptor.job_id,
                project_reference: reference,
                subject_id: connection.subject_id,
                requester: {
                    realm_id: audience.realm_id,
                    requester_user_id: audience.requester_user_id,
                },
                runner_scope: this.runnerScope,
            });
            this.journal.uncertain(compileId);
            const client = new TitenClient(connection);
            const projectId = await client.resolveProject(reference);
            const context = await client.compile({
                subject_id: connection.subject_id,
                project_id: projectId,
                task: descriptor.request,
                max_tokens: 1200,
                top_k: 5,
            });
            this.journal.complete(compileId, {completed: true, project_id: projectId});
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
    if (!["tool_result", "decision", "system_event"].includes(item.kind))
        throw new Error("Invalid memory observation kind");
    if (!["tool_result", "decision", "system_event"].includes(item.source_type))
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

const protectedPattern =
    /-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----|\b(?:authorization\s*:\s*(?:basic|bearer)|bearer\s+)[a-z0-9._~+\/-]+=*|\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*\S+|\bAKIA[0-9A-Z]{16}\b/i;
const forbiddenProvenance =
    /\b(?:recall(?:ed)?|transcript|prompt|chain.of.thought|reasoning|model[ _-]?output)\b/i;

function protectedText(item: Data, knownSecrets: string[]): void {
    const fields = [
        item.content,
        item.source_ref,
        item.source_id,
        ...item.claims.map((claim: Data) => claim.statement),
    ];
    if (
        fields.some(
            (value) =>
                typeof value !== "string" ||
                protectedPattern.test(value) ||
                knownSecrets.some((secret) => secret && value.includes(secret)),
        )
    )
        throw new Error("Protected content cannot enter memory");
    if (forbiddenProvenance.test(item.source_ref) || forbiddenProvenance.test(item.source_id))
        throw new Error("Memory provenance is not eligible for durable storage");
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
        private knownSecrets: () => string[] = () => [],
    ) {}
    async record(value: unknown): Promise<void> {
        const item = signal(value),
            connection = this.resolveConnection(item);
        if (!connection) throw new Error("No owner-approved Titen subject mapping");
        evidence(item.evidence_path, item.evidence_sha256);
        const stored = this.store.read<Record<string, Data>>("memory-signals.json") ?? {};
        const requestDigest = createHash("sha256").update(canonical(item)).digest("hex");
        const existing = stored[item.idempotency_key];
        if (existing && existing.request_digest !== requestDigest)
            throw new Error("Memory idempotency key belongs to a different signal");
        protectedText(item, this.knownSecrets());
        if (existing?.state === "complete") return;
        const client = new TitenClient(connection);
        const projectId = await client.resolveProject(item.project_reference);
        if (
            existing &&
            (existing.project_id !== projectId || existing.subject_id !== connection.subject_id)
        )
            throw new Error("Memory idempotency scope changed");
        let observationId = existing?.observation_id;
        if (typeof observationId !== "string" || !observationId) {
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
            if (
                typeof remembered.observation_id !== "string" ||
                !remembered.observation_id ||
                remembered.subject_id !== connection.subject_id ||
                remembered.project_id !== projectId ||
                remembered.kind !== item.kind ||
                remembered.trust !== "verified" ||
                remembered.visibility !== "organization"
            )
                throw new Error("Invalid remembered observation");
            observationId = remembered.observation_id;
            stored[item.idempotency_key] = {
                request_digest: requestDigest,
                project_id: projectId,
                subject_id: connection.subject_id,
                evidence_sha256: item.evidence_sha256,
                source_ref: item.source_ref,
                source_id: item.source_id,
                observation_id: observationId,
                state: "remembered",
            };
            this.store.write("memory-signals.json", stored);
        }
        const consolidated = await client.consolidate({
            subject_id: connection.subject_id,
            project_id: projectId,
            idempotency_key: item.idempotency_key,
            claims: item.claims.map((claim: Data) => ({
                kind: claim.kind,
                statement: claim.statement,
                ...(claim.confidence === undefined ? {} : {confidence: claim.confidence}),
                trust: "verified",
                visibility: "organization",
                sources: [{observation_id: observationId, relation: "supports"}],
            })),
        });
        if (
            consolidated.subject_id !== connection.subject_id ||
            consolidated.project_id !== projectId ||
            !Array.isArray(consolidated.claims) ||
            consolidated.claims.length !== item.claims.length ||
            consolidated.claims.some(
                (claim: Data, index: number) =>
                    typeof claim.claim_id !== "string" ||
                    !claim.claim_id ||
                    claim.kind !== item.claims[index].kind ||
                    claim.trust !== "verified" ||
                    claim.visibility !== "organization" ||
                    !Array.isArray(claim.evidence_ids) ||
                    claim.evidence_ids.length !== 1 ||
                    claim.evidence_ids[0] !== observationId,
            )
        )
            throw new Error("Invalid consolidated memory claim");
        stored[item.idempotency_key] = {
            request_digest: requestDigest,
            project_id: projectId,
            subject_id: connection.subject_id,
            evidence_sha256: item.evidence_sha256,
            source_ref: item.source_ref,
            source_id: item.source_id,
            observation_id: observationId,
            claim_ids: consolidated.claims.map((claim: Data) => claim.claim_id),
            state: "complete",
            recorded_at: new Date().toISOString(),
        };
        this.store.write("memory-signals.json", stored);
    }
}
