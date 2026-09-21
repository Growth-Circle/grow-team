import {DatabaseSync} from "node:sqlite";
import {PrivateStore} from "./config.js";
import {canonical, type Data} from "./protocol.js";
export interface Entry {
    id: string;
    scope: string;
    kind: string;
    route: string;
    request: Data;
    state: "prepared" | "uncertain" | "done";
    response: Data | null;
}
function noSecrets(value: unknown): void {
    if (!value || typeof value !== "object") return;
    for (const [k, v] of Object.entries(value)) {
        if (
            /^(token|refresh_token|polling_secret|authorization|credential|api_key|secret)$/i.test(
                k,
            )
        )
            throw new Error("Credentials cannot enter the journal");
        noSecrets(v);
    }
}
export interface JournalLog {
    get(id: string): Entry | null;
    list(kind?: string): Entry[];
    prepare(kind: string, id: string, route: string, request: Data): Entry;
    uncertain(id: string): void;
    complete(id: string, response: Data): void;
    protectSecret(secret: string): void;
}
export class Journal {
    private secrets = new Set<string>();
    protectSecret(secret: string): void {
        if (!secret) throw new Error("Secret is empty");
        this.secrets.add(secret);
    }
    private protect(value: unknown): void {
        noSecrets(value);
        const text = canonical(value);
        for (const secret of this.secrets)
            if (text.includes(secret)) throw new Error("Secret material cannot enter the journal");
    }
    private store: PrivateStore;
    identityScope(): string {
        const state = this.store.read("connection.json");
        return state?.runner_id ? `runner:${state.runner_id}` : "";
    }
    connectionScope(): string {
        const state = this.store.read("connection.json");
        if (!state) return "";
        if (state.state !== "connected")
            throw new Error("Runner scope requires connected credentials");
        return `runner:${state.runner_id}`;
    }
    partition(scope: string): JournalLog {
        const prefix = scope ? `${scope}/` : "";
        const normalize = (entry: Entry | null): Entry | null =>
            entry ? {...entry, id: entry.id.slice(prefix.length)} : null;
        return {
            get: (id) => normalize(this.get(prefix + id)),
            list: (kind) =>
                this.list(kind)
                    .filter((e) => e.scope === scope)
                    .map((e) => normalize(e)!),
            prepare: (kind, id, route, request) =>
                normalize(this.prepare(kind, prefix + id, route, request, scope))!,
            uncertain: (id) => this.uncertain(prefix + id),
            complete: (id, response) => this.complete(prefix + id, response),
            protectSecret: (secret) => this.protectSecret(secret),
        };
    }
    preserveLegacyScope(scope: string): void {
        if (!scope) return;
        this.db.exec("BEGIN IMMEDIATE");
        try {
            this.db
                .prepare("UPDATE entries SET id=? || '/' || id, scope=? WHERE scope='' ")
                .run(scope, scope);
            this.db.exec("COMMIT");
        } catch (error) {
            this.db.exec("ROLLBACK");
            throw error;
        }
    }
    private db: DatabaseSync;
    private ownership: DatabaseSync;
    constructor(root: string) {
        const store = new PrivateStore(root);
        this.store = store;
        for (const sidecar of [
            "journal.sqlite-wal",
            "journal.sqlite-shm",
            "supervisor.sqlite-journal",
        ])
            store.check(sidecar);
        this.ownership = new DatabaseSync(store.createFile("supervisor.sqlite"));
        try {
            this.ownership.exec("PRAGMA busy_timeout=0; BEGIN EXCLUSIVE");
        } catch {
            this.ownership.close();
            throw new Error("Another supervisor owns this state directory");
        }
        try {
            this.db = new DatabaseSync(store.createFile("journal.sqlite"));
            this.db.exec(
                "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=2000",
            );
            this.db.exec(
                "CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, kind TEXT NOT NULL, route TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared','uncertain','done')), response TEXT)",
            );
            if (
                !this.db
                    .prepare("PRAGMA table_info(entries)")
                    .all()
                    .some((row) => row.name === "scope")
            )
                this.db.exec("ALTER TABLE entries ADD COLUMN scope TEXT NOT NULL DEFAULT ''");
            const connection = store.read("connection.json");
            if (connection?.runner_id) this.preserveLegacyScope(`runner:${connection.runner_id}`);
        } catch (e) {
            this.ownership.close();
            throw e;
        }
    }
    get(id: string): Entry | null {
        const r = this.db.prepare("SELECT * FROM entries WHERE id=?").get(id) as unknown as
            | Data
            | undefined;
        return r
            ? ({
                  ...r,
                  request: JSON.parse(r.request),
                  response: r.response ? JSON.parse(r.response) : null,
              } as Entry)
            : null;
    }
    list(kind?: string): Entry[] {
        return (
            kind
                ? this.db.prepare("SELECT id FROM entries WHERE kind=? ORDER BY rowid").all(kind)
                : this.db.prepare("SELECT id FROM entries ORDER BY rowid").all()
        ).map((r) => this.get(r.id as string)!);
    }
    prepare(kind: string, id: string, route: string, request: Data, scope = ""): Entry {
        this.protect(request);
        const serialized = canonical(request),
            old = this.get(id);
        if (old) {
            if (
                old.scope !== scope ||
                old.kind !== kind ||
                old.route !== route ||
                canonical(old.request) !== serialized
            )
                throw new Error("Journal identity conflict");
            return old;
        }
        this.db
            .prepare(
                "INSERT INTO entries (id,kind,route,request,state,response,scope) VALUES(?,?,?,?, 'prepared',NULL,?)",
            )
            .run(id, kind, route, serialized, scope);
        return this.get(id)!;
    }
    uncertain(id: string): void {
        this.db
            .prepare("UPDATE entries SET state='uncertain' WHERE id=? AND state='prepared'")
            .run(id);
    }
    complete(id: string, response: Data): void {
        this.protect(response);
        const old = this.get(id);
        if (!old) throw new Error("Unknown journal identity");
        if (old.state === "done" && canonical(old.response) !== canonical(response))
            throw new Error("Receipt conflict");
        this.db
            .prepare("UPDATE entries SET state='done',response=? WHERE id=?")
            .run(canonical(response), id);
    }
    close(): void {
        this.db.close();
        this.ownership.close();
    }
}
