import {randomBytes, randomUUID, createHash} from "node:crypto";
import {hostname} from "node:os";
import {setTimeout as sleep} from "node:timers/promises";
import {PrivateStore, controlOrigin} from "./config.js";
import {Journal, type Entry} from "./journal.js";
import {canonical, type Data} from "./protocol.js";
const routes = new Set([
    "/pairings",
    "/pairings/status",
    "/pairings/exchange",
    "/runner/token/refresh",
    "/runner/catalog",
    "/runner/metadata",
    "/runner/setup-authority",
    "/runner/authority",
    "/runner/credential-access",
    "/runner/probe-credential-access",
    "/runner/workspaces",
    "/runner/claims",
    "/runner/leases",
    "/runner/controls",
    "/runner/heartbeat",
    "/runner/stop-evidence",
    "/runner/inputs",
    "/runner/inputs/reconcile",
    "/runner/events",
    "/runner/checkpoints",
    "/runner/context",
    "/runner/operations/propose",
    "/runner/operations/consume",
    "/runner/operations",
    "/runner/operations/reconcile",
    "/runner/operations/reconcile-local",
    "/runner/setups",
    "/runner/setups/claim",
    "/runner/setups/result",
]);
export class TransportError extends Error {
    readonly kind: "credential" | "policy" | "contention" | "transient" | "protocol";
    readonly code: string;
    readonly retryAfter: number;
    constructor(kind: TransportError["kind"], code = "", retryAfter = 0) {
        super(`Control request failed: ${kind}${code ? ` (${code})` : ""}`);
        this.kind = kind;
        this.code = code;
        this.retryAfter = retryAfter;
    }
}
export class Transport {
    readonly origin: string;
    readonly journal: Journal;
    private token: () => string | null;
    constructor(origin: string, journal: Journal, token: () => string | null) {
        this.origin = controlOrigin(origin);
        this.journal = journal;
        this.token = token;
    }
    async request(route: string, payload?: Data, anonymous = false): Promise<Data> {
        const scope = anonymous ? this.journal.identityScope() : this.currentScope();
        if (!routes.has(route)) throw new TransportError("protocol", "unsupported_route");
        const body = payload === undefined ? undefined : canonical({schema_version: 1, ...payload});
        if (body && Buffer.byteLength(body) > 1024 * 1024)
            throw new TransportError("protocol", "request_too_large");
        const headers: Record<string, string> = {Accept: "application/json"};
        if (body) headers["Content-Type"] = "application/json";
        if (!anonymous) {
            const token = this.token();
            if (!token) throw new TransportError("credential", "credential_missing");
            headers.Authorization = `Bearer ${token}`;
        }
        const abort = new AbortController(),
            timer = setTimeout(() => abort.abort(), 25000);
        try {
            const response = await fetch(`${this.origin}/api/v1/agent${route}`, {
                method: body ? "POST" : "GET",
                headers,
                body,
                redirect: "manual",
                signal: abort.signal,
            });
            if (response.status >= 300 && response.status < 400)
                throw new TransportError("protocol", "redirect_rejected");
            let size = 0;
            const chunks: Uint8Array[] = [];
            if (response.body)
                for await (const chunk of response.body) {
                    size += chunk.length;
                    if (size > 2 * 1024 * 1024) {
                        abort.abort();
                        throw new TransportError("protocol", "response_too_large");
                    }
                    chunks.push(chunk);
                }
            let data: Data;
            try {
                data = JSON.parse(Buffer.concat(chunks).toString("utf8"));
            } catch {
                if (!response.ok) data = {};
                else
                    throw new TransportError(
                        response.status >= 500 ? "transient" : "protocol",
                        "invalid_response",
                    );
            }
            if (response.status === 401)
                throw new TransportError(
                    "credential",
                    /^credential_(invalid|revoked|expired)$/.test(data.code)
                        ? data.code
                        : "credential_invalid",
                );
            if ([409, 429, 503].includes(response.status))
                throw new TransportError(
                    "contention",
                    "",
                    Math.min(60, Math.max(1, Number(response.headers.get("retry-after")) || 1)),
                );
            if (response.status >= 500) throw new TransportError("transient");
            if (!response.ok) throw new TransportError("policy");
            if (data.schema_version !== 1 || data.result !== "success")
                throw new TransportError("protocol", "invalid_envelope");
            if (scope !== (anonymous ? this.journal.identityScope() : this.currentScope()))
                throw new TransportError("credential", "scope_changed");
            return data;
        } catch (e) {
            if (e instanceof TransportError) throw e;
            throw new TransportError("transient", "network_or_timeout");
        } finally {
            clearTimeout(timer);
        }
    }
    async binary(
        route: "/runner/artifacts" | "/runner/context-file",
        payload: Data,
        bytes?: Buffer,
    ): Promise<Data | Buffer> {
        const scope = this.currentScope(),
            token = this.token();
        if (!token) throw new TransportError("credential");
        const abort = new AbortController(),
            timer = setTimeout(() => abort.abort(), 25000);
        try {
            let body: FormData | string;
            const headers: Record<string, string> = {Authorization: `Bearer ${token}`};
            if (route === "/runner/artifacts") {
                if (!bytes || bytes.length > 8 * 1024 * 1024)
                    throw new TransportError("protocol", "upload_limit");
                const form = new FormData();
                form.set("payload", canonical(payload));
                form.set(
                    "file",
                    new Blob([new Uint8Array(bytes)], {type: payload.media_type}),
                    payload.filename,
                );
                body = form;
            } else {
                body = canonical(payload);
                headers["Content-Type"] = "application/json";
            }
            const response = await fetch(`${this.origin}/api/v1/agent${route}`, {
                method: "POST",
                headers,
                body,
                redirect: "manual",
                signal: abort.signal,
            });
            if (!response.ok)
                throw new TransportError(
                    response.status === 401 ? "credential" : "policy",
                    "binary_request_failed",
                );
            const chunks: Uint8Array[] = [];
            let size = 0;
            if (response.body)
                for await (const chunk of response.body) {
                    size += chunk.length;
                    if (size > (route === "/runner/context-file" ? 51200 : 65536)) {
                        abort.abort();
                        throw new TransportError("protocol", "response_too_large");
                    }
                    chunks.push(chunk);
                }
            if (scope !== this.currentScope())
                throw new TransportError("credential", "scope_changed");
            const result = Buffer.concat(chunks);
            if (route === "/runner/context-file") return result;
            const data = JSON.parse(result.toString());
            if (data.schema_version !== 1 || data.result !== "success")
                throw new TransportError("protocol", "invalid_envelope");
            return data;
        } catch (e) {
            if (e instanceof TransportError) throw e;
            throw new TransportError("transient", "binary_outcome_uncertain");
        } finally {
            clearTimeout(timer);
        }
    }
    currentScope(): string {
        return this.journal.connectionScope();
    }
    scopedJournal() {
        return this.journal.partition(this.currentScope());
    }
    async send(entry: Entry): Promise<Data> {
        const scope = this.currentScope();
        if (entry.scope !== scope)
            throw new Error("Journal request belongs to another runner scope");
        const log = this.journal.partition(scope);
        const id = entry.id.startsWith(`${scope}/`) ? entry.id.slice(scope.length + 1) : entry.id;
        if (entry.state === "done") return entry.response!;
        if (entry.kind === "consume" && entry.state === "uncertain")
            throw new Error(
                "Operation consume is uncertain; reconcile authority before any effect",
            );
        log.uncertain(id);
        const response = await this.request(entry.route, entry.request);
        // Keep the original receipt in its original partition, even if pairing changed during the request.
        log.complete(id, response);
        if (this.currentScope() !== scope) throw new Error("Runner scope changed during request");
        return response;
    }
    async mutate(kind: string, id: string, route: string, request: Data): Promise<Data> {
        return this.send(this.scopedJournal().prepare(kind, id, route, request));
    }
    async flush(): Promise<void> {
        // Input and operation records describe local effects. Only explicit outgoing records are replayed.
        for (const entry of this.scopedJournal().list())
            if (
                [
                    "event",
                    "claim",
                    "setup_claim",
                    "setup_result",
                    "catalog",
                    "workspace",
                    "checkpoint",
                    "proposal",
                    "input_receipt",
                    "remote_receipt",
                    "local_receipt",
                    "stop",
                ].includes(entry.kind) &&
                entry.state !== "done"
            )
                await this.send(entry);
    }
    async poll(callback: () => Promise<void>, signal: AbortSignal): Promise<void> {
        let failures = 0;
        while (!signal.aborted) {
            let wait = 2000;
            try {
                await callback();
                failures = 0;
            } catch (e) {
                if (!(e instanceof TransportError) || !["transient", "contention"].includes(e.kind))
                    throw e;
                failures++;
                wait = Math.min(30000, 1000 * 2 ** Math.min(failures, 5));
                wait = Math.max(wait, e.retryAfter * 1000);
            }
            try {
                await sleep(Math.min(60000, wait + Math.floor(Math.random() * 250)), undefined, {
                    signal,
                });
            } catch {
                if (!signal.aborted) throw new Error("Polling interrupted");
            }
        }
    }
}
export class Connection {
    private store: PrivateStore;
    private transport: Transport;
    constructor(store: PrivateStore, transport: Transport) {
        this.store = store;
        this.transport = transport;
        const current = this.read();
        if (current && current.origin !== transport.origin)
            throw new Error("Control-plane identity is frozen");
    }
    read(): Data | null {
        return this.store.read("connection.json");
    }
    async start(deviceName: string, explicitRepair = false): Promise<Data> {
        const old = this.read();
        if (old && !explicitRepair)
            throw new Error("Connection exists; use status or explicit re-pair");
        const archive = old ? `connection-history-${randomUUID()}.json` : null;
        if (old) {
            if (old.runner_id)
                this.transport.journal.preserveLegacyScope(`runner:${old.runner_id}`);
            this.store.write(archive!, old);
            const registry = this.store.read("registry.json");
            if (registry) this.store.write(`registry-history-${randomUUID()}.json`, registry);
        }
        const state: Data = {
            recovery_history: [...(old?.recovery_history ?? []), ...(archive ? [archive] : [])],
            origin: this.transport.origin,
            state: "pairing_start_uncertain",
            device_name: deviceName,
            polling_secret: randomBytes(32).toString("base64url"),
            fingerprint:
                old?.fingerprint ??
                createHash("sha256").update(`${hostname()}:${randomUUID()}`).digest("hex"),
            orphan_runner_id: old?.runner_id ?? old?.orphan_runner_id ?? null,
        };
        this.store.write("connection.json", state);
        const response = await this.transport.request(
            "/pairings",
            {
                device_name: state.device_name,
                polling_secret: state.polling_secret,
                fingerprint: state.fingerprint,
            },
            true,
        );
        if (typeof response.pairing_id !== "string" || typeof response.user_code !== "string")
            throw new TransportError("protocol");
        this.store.write("connection.json", {
            ...state,
            state: "pending",
            pairing_id: response.pairing_id,
            user_code: response.user_code,
            pairing_expires_at: response.expires_at,
        });
        return {
            state: "pending",
            pairing_id: response.pairing_id,
            user_code: response.user_code,
            expires_at: response.expires_at,
        };
    }
    async poll(): Promise<Data> {
        const state = this.read();
        if (!state) throw new Error("Connect first");
        if (["connected", "rotation_uncertain", "re_pair_required"].includes(state.state))
            return {
                state: state.state,
                runner_id: state.runner_id ?? null,
                orphan_runner_id: state.orphan_runner_id ?? null,
            };
        if (!state.pairing_id)
            throw new Error("Pairing start response was lost; explicit re-pair is required");
        const identity = {pairing_id: state.pairing_id, polling_secret: state.polling_secret};
        const status = await this.transport.request("/pairings/status", identity, true);
        if (status.state === "exchanged") {
            this.store.write("connection.json", {
                ...state,
                state: "re_pair_required",
                orphan_runner_id: status.runner_id,
            });
            return {state: "re_pair_required", orphan_runner_id: status.runner_id};
        }
        if (["expired", "rejected"].includes(status.state)) {
            this.store.write("connection.json", {...state, state: "re_pair_required"});
            return {state: "re_pair_required"};
        }
        if (status.state !== "approved") return {state: status.state};
        // An exchange error does not prove that no credential was issued.
        this.store.write("connection.json", {...state, state: "exchange_uncertain"});
        const issued = await this.transport.request("/pairings/exchange", identity, true);
        this.accept(state, issued);
        return {state: "connected", runner_id: issued.runner_id};
    }
    private accept(old: Data, issued: Data): void {
        if (old.runner_id && issued.runner_id !== old.runner_id)
            throw new TransportError("protocol", "runner_identity_changed");
        for (const k of ["token", "refresh_token", "runner_id", "expires_at", "refresh_expires_at"])
            if (typeof issued[k] !== "string" || !issued[k])
                throw new TransportError("protocol", "invalid_credential_response");
        if (
            !Number.isFinite(Date.parse(issued.expires_at)) ||
            !Number.isFinite(Date.parse(issued.refresh_expires_at))
        )
            throw new TransportError("protocol", "invalid_expiry");
        this.store.write("connection.json", {
            recovery_history: old.recovery_history ?? [],
            origin: old.origin,
            fingerprint: old.fingerprint,
            state: "connected",
            runner_id: issued.runner_id,
            token: issued.token,
            refresh_token: issued.refresh_token,
            expires_at: issued.expires_at,
            refresh_expires_at: issued.refresh_expires_at,
            orphan_runner_id: old.orphan_runner_id ?? null,
        });
    }
    async rotate(): Promise<void> {
        const state = this.read();
        if (!state || state.state !== "connected")
            throw new Error("Credential recovery requires explicit re-pair");
        if (Date.parse(state.refresh_expires_at) <= Date.now()) {
            this.store.write("connection.json", {...state, state: "re_pair_required"});
            throw new TransportError("credential", "credential_expired");
        }
        this.store.write("connection.json", {...state, state: "rotation_uncertain"});
        const issued = await this.transport.request(
            "/runner/token/refresh",
            {refresh_token: state.refresh_token},
            true,
        );
        this.accept(state, issued);
    }
    async access(): Promise<void> {
        const state = this.read();
        if (!state || state.state !== "connected")
            throw new TransportError("credential", "credential_missing");
        if (Date.parse(state.expires_at) <= Date.now() + 60000) await this.rotate();
    }
}
