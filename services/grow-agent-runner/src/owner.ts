import {readFileSync, existsSync} from "node:fs";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {PrivateStore, workspacePath, localSecret, controlOrigin} from "./config.js";
import {Transport} from "./transport.js";
import {parse, parseChecks, canonical, digest, type Data} from "./protocol.js";
const exec = promisify(execFile);

function record(value: unknown): Data {
    if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error("Invalid owner configuration");
    return value as Data;
}
function exact(value: Data, keys: string[]): void {
    if (Object.keys(value).some((key) => !keys.includes(key)))
        throw new Error("Unknown owner configuration field");
}
function mcpEndpoint(value: unknown): string {
    if (typeof value !== "string") throw new Error("Invalid Titen endpoint");
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
        throw new Error("Invalid Titen endpoint");
    return url.toString();
}
export class OwnerRegistry {
    constructor(
        private store: PrivateStore,
        private transport: Transport,
    ) {}
    read(): Data {
        return this.store.read("registry.json") ?? {workspaces: {}, secrets: {}, catalog: null};
    }
    private connected(): Data {
        const scope = this.transport.currentScope?.() ?? "";
        const state = this.read();
        if (state.server_scope !== scope) {
            // Preserve local approvals. Server bindings belong to one runner only.
            state.previous_bindings = [
                ...(state.previous_bindings ?? []),
                {
                    server_scope: state.server_scope ?? null,
                    workspaces: structuredClone(state.workspaces),
                    catalog: state.catalog,
                },
            ];
            for (const item of Object.values(state.workspaces) as Data[]) item.repository = null;
            state.server_scope = scope;
            state.catalog_reported = false;
            this.store.write("registry.json", state);
        }
        return state;
    }
    async workspace(alias: string, path: string, metadata: Data): Promise<void> {
        if (!/^[a-zA-Z0-9_-]{1,80}$/.test(alias)) throw new Error("Invalid workspace alias");
        if (
            Object.keys(metadata).some(
                (k) =>
                    !["canonical_origin", "allowed_refs", "required_checks", "revision"].includes(
                        k,
                    ),
            )
        )
            throw new Error("Unknown workspace metadata");
        if (
            !Array.isArray(metadata.allowed_refs) ||
            metadata.allowed_refs.length < 1 ||
            metadata.allowed_refs.length > 100 ||
            metadata.allowed_refs.some(
                (ref: unknown) => typeof ref !== "string" || !ref || ref.length > 4096,
            )
        )
            throw new Error("Invalid allowed refs");
        const localPath = workspacePath(path),
            body = {
                schema_version: 1,
                workspace_alias: alias,
                ...metadata,
                canonical_origin: metadata.canonical_origin ?? null,
                required_checks: parseChecks(metadata.required_checks ?? []),
            };
        if (!Number.isSafeInteger(metadata.revision) || metadata.revision < 1)
            throw new Error("Invalid workspace revision");
        const state = this.connected(),
            old = state.workspaces[alias];
        if (old && old.path !== localPath && metadata.revision <= old.metadata.revision)
            throw new Error("Workspace mapping requires a new revision");
        // Persist owner approval and the local mapping before reporting metadata.
        state.workspaces[alias] = {
            path: localPath,
            metadata: body,
            repository: old?.repository ?? null,
        };
        this.store.write("registry.json", state);
        const result = await this.transport.mutate(
            "workspace",
            `workspace:${alias}:${metadata.revision}`,
            "/runner/workspaces",
            body,
        );
        state.workspaces[alias].repository = result.repository;
        this.store.write("registry.json", state);
    }
    assertWorkspace(repository: Data): string {
        const item = this.connected().workspaces[repository.workspace_alias];
        if (
            !item ||
            item.repository?.id !== repository.id ||
            item.repository.policy_version !== repository.policy_version ||
            item.metadata.canonical_origin !== repository.canonical_origin ||
            canonical(item.metadata.allowed_refs) !== canonical(repository.allowed_refs) ||
            digest(item.metadata.required_checks ?? []) !== digest(repository.required_checks)
        )
            throw new Error("Unapproved workspace binding");
        if (workspacePath(item.path) !== item.path) throw new Error("Workspace mapping changed");
        return item.path;
    }
    async catalog(value: unknown): Promise<void> {
        const catalog = parse("runner_catalog", value);
        if (catalog.sandboxes.some((s: Data) => s.catalog_revision !== catalog.revision))
            throw new Error("Catalog revision mismatch");
        const state = this.connected();
        if (
            state.catalog &&
            (catalog.revision < state.catalog.revision ||
                (catalog.revision === state.catalog.revision &&
                    canonical(state.catalog) !== canonical(catalog)))
        )
            throw new Error("Catalog revision conflict");
        state.catalog = catalog;
        this.store.write("registry.json", state);
        await this.transport.mutate("catalog", `catalog:${catalog.revision}`, "/runner/catalog", {
            schema_version: 1,
            catalog,
        });
        state.catalog_reported = true;
        this.store.write("registry.json", state);
    }
    // Setup probes read this before assertRuntime (contract 10.2): it never throws, so a
    // probe can turn a missing or unready adapter into a reported requirement.
    catalogState(descriptor: Data): {adapter: Data | undefined; sandboxApproved: boolean} {
        const state = this.connected(),
            catalog = state.catalog;
        if (!catalog || !state.catalog_reported) return {adapter: undefined, sandboxApproved: false};
        return {
            adapter: catalog.adapters.find(
                (a: Data) => a.id === descriptor.adapter.id && a.version === descriptor.adapter.version,
            ),
            sandboxApproved: catalog.sandboxes.some(
                (s: Data) => canonical(s) === canonical(descriptor.policy.sandbox),
            ),
        };
    }
    assertRuntime(descriptor: Data): void {
        const state = this.connected();
        const catalog = state.catalog;
        if (!catalog || !state.catalog_reported) throw new Error("Approve a local catalog first");
        if (
            !catalog.adapters.some(
                (a: Data) =>
                    a.id === descriptor.adapter.id &&
                    a.version === descriptor.adapter.version &&
                    a.auth_state === "ready",
            )
        )
            throw new Error("Adapter is not owner-approved");
        if (
            !catalog.sandboxes.some(
                (s: Data) => canonical(s) === canonical(descriptor.policy.sandbox),
            )
        )
            throw new Error("Sandbox is not owner-approved");
        if (descriptor.repository) this.assertWorkspace(descriptor.repository);
        if (descriptor.workspace_binding) {
            const binding = descriptor.workspace_binding,
                item = state.workspaces[binding.workspace_alias];
            if (
                !item ||
                item.repository?.id !== binding.repository_id ||
                item.repository.policy_version !== binding.policy_version ||
                item.metadata.canonical_origin !== binding.canonical_origin ||
                canonical(item.metadata.allowed_refs) !== canonical(binding.allowed_refs) ||
                digest(item.metadata.required_checks) !== binding.checks_digest
            )
                throw new Error("Unapproved probe workspace binding");
            if (workspacePath(item.path) !== item.path)
                throw new Error("Workspace mapping changed");
        }
    }
    secret(name: string, path: string): void {
        if (!/^[a-zA-Z0-9_-]{1,80}$/.test(name)) throw new Error("Invalid secret name");
        localSecret(path);
        const state = this.read();
        state.secrets[name] = path;
        this.store.write("registry.json", state);
    }
    resolveSecret(reference: Data): string {
        if (reference.kind !== "local")
            throw new Error("Server secret resolution belongs to the authenticated runtime broker");
        const path = this.read().secrets[reference.id];
        if (!path) throw new Error("Unknown owner-local secret reference");
        const value = localSecret(path);
        this.transport.journal.protectSecret(value);
        return value;
    }
    memoryProtectedSecrets(): string[] {
        const state = this.read();
        return Object.values(state.secrets).map((path) => localSecret(path as string));
    }
    configureTiten(value: unknown): void {
        const config = record(value);
        exact(config, ["endpoint", "credential_secret_ref", "subjects"]);
        const credential = config.credential_secret_ref;
        if (
            credential !== null &&
            (typeof credential !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(credential))
        )
            throw new Error("Invalid Titen credential reference");
        const state = this.read();
        if (credential && !state.secrets[credential])
            throw new Error("Unknown owner-local secret reference");
        if (!Array.isArray(config.subjects) || config.subjects.length > 1000)
            throw new Error("Invalid Titen subject mappings");
        const subjects = config.subjects.map((item) => {
            const subject = record(item);
            exact(subject, ["control_origin", "realm_id", "requester_user_id", "subject_id"]);
            const control = controlOrigin(subject.control_origin);
            if (
                !Number.isSafeInteger(subject.realm_id) ||
                subject.realm_id < 1 ||
                !Number.isSafeInteger(subject.requester_user_id) ||
                subject.requester_user_id < 1 ||
                typeof subject.subject_id !== "string" ||
                !/^[a-zA-Z0-9:_-]{1,200}$/.test(subject.subject_id)
            )
                throw new Error("Invalid Titen subject mapping");
            return {
                control_origin: control,
                realm_id: subject.realm_id,
                requester_user_id: subject.requester_user_id,
                subject_id: subject.subject_id,
            };
        });
        const keys = new Set(
            subjects.map(
                (item) => `${item.control_origin}:${item.realm_id}:${item.requester_user_id}`,
            ),
        );
        if (keys.size !== subjects.length) throw new Error("Duplicate Titen subject mapping");
        state.titen = {
            endpoint: mcpEndpoint(config.endpoint),
            credential_secret_ref: credential,
            subjects,
        };
        this.store.write("registry.json", state);
    }
    titenFor(descriptor: Data): Data | null {
        const state = this.read(),
            config = state.titen;
        if (!config || !descriptor.repository?.canonical_origin || !descriptor.audience)
            return null;
        const origin = this.store.read("connection.json")?.origin;
        if (typeof origin !== "string") return null;
        const mapping = config.subjects.find(
            (item: Data) =>
                item.control_origin === controlOrigin(origin) &&
                item.realm_id === descriptor.audience.realm_id &&
                item.requester_user_id === descriptor.audience.requester_user_id,
        );
        if (!mapping) return null;
        const token = config.credential_secret_ref
            ? this.resolveSecret({kind: "local", id: config.credential_secret_ref})
            : null;
        return {endpoint: config.endpoint, token, subject_id: mapping.subject_id};
    }
    configureRemoteOperations(value: unknown): void {
        const config = record(value);
        exact(config, ["git", "github"]);
        if (!Array.isArray(config.git) || !Array.isArray(config.github))
            throw new Error("Invalid remote operation configuration");
        const state = this.read();
        const secret = (value: unknown, required: boolean) => {
            if (value === null && !required) return null;
            if (
                typeof value !== "string" ||
                !/^[a-zA-Z0-9_-]{1,80}$/.test(value) ||
                !state.secrets[value]
            )
                throw new Error("Unknown owner-local secret reference");
            return value;
        };
        const remote = (value: unknown) => {
            if (
                typeof value !== "string" ||
                !value ||
                value.length > 4096 ||
                /[\s\x00-\x1f]/.test(value)
            )
                throw new Error("Invalid owner remote");
            if (
                !/^https:\/\/[a-zA-Z0-9.-]+\/[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+(?:\.git)?$/.test(
                    value,
                )
            )
                throw new Error("Invalid owner remote");
            return value;
        };
        const git = config.git.map((item) => {
            const row = record(item);
            exact(row, ["repository_id", "remote", "credential_secret_ref"]);
            if (
                typeof row.repository_id !== "string" ||
                !/^[a-zA-Z0-9_-]{1,100}$/.test(row.repository_id)
            )
                throw new Error("Invalid publication repository");
            return {
                repository_id: row.repository_id,
                remote: remote(row.remote),
                credential_secret_ref: secret(row.credential_secret_ref, false),
            };
        });
        const github = config.github.map((item) => {
            const row = record(item);
            exact(row, ["remote", "api_base", "credential_secret_ref"]);
            const base = new URL(String(row.api_base));
            if (
                base.username ||
                base.password ||
                base.search ||
                base.hash ||
                (base.protocol !== "https:" &&
                    !(
                        base.protocol === "http:" &&
                        ["127.0.0.1", "[::1]", "localhost"].includes(base.hostname)
                    ))
            )
                throw new Error("Invalid GitHub API endpoint");
            return {
                remote: remote(row.remote),
                api_base: base.toString().replace(/\/$/, ""),
                credential_secret_ref: secret(row.credential_secret_ref, true)!,
            };
        });
        if (
            new Set(git.map((item) => item.repository_id)).size !== git.length ||
            new Set(github.map((item) => item.remote)).size !== github.length
        )
            throw new Error("Duplicate remote operation configuration");
        state.remote_operations = {git, github};
        this.store.write("registry.json", state);
    }
    publicationFor(descriptor: Data): Data | null {
        const repository = descriptor.repository,
            config = this.read().remote_operations;
        if (!repository || !config) return null;
        const git = config.git.find(
            (item: Data) =>
                item.repository_id === repository.id && item.remote === repository.canonical_origin,
        );
        if (!git) return null;
        const provider = config.github.find((item: Data) => item.remote === git.remote) ?? null;
        return {
            remote: git.remote,
            git_credential: git.credential_secret_ref
                ? this.resolveSecret({kind: "local", id: git.credential_secret_ref})
                : null,
            github: provider
                ? {
                      api_base: provider.api_base,
                      credential: this.resolveSecret({
                          kind: "local",
                          id: provider.credential_secret_ref,
                      }),
                  }
                : null,
        };
    }
    publicationForRemote(remote: string): Data | null {
        const config = this.read().remote_operations;
        if (!config) return null;
        const git = config.git.find((item: Data) => item.remote === remote);
        if (!git) return null;
        const provider = config.github.find((item: Data) => item.remote === remote) ?? null;
        return {
            remote,
            git_credential: git.credential_secret_ref
                ? this.resolveSecret({kind: "local", id: git.credential_secret_ref})
                : null,
            github: provider
                ? {
                      api_base: provider.api_base,
                      credential: this.resolveSecret({
                          kind: "local",
                          id: provider.credential_secret_ref,
                      }),
                  }
                : null,
        };
    }
}
export async function doctor(): Promise<Data> {
    const binary = async (command: string, args: string[]) => {
        try {
            const {stdout} = await exec(command, args, {timeout: 5000, maxBuffer: 65536});
            return {available: true, version: stdout.trim().slice(0, 500)};
        } catch {
            return {available: false};
        }
    };
    const [git, docker] = await Promise.all([
        binary("git", ["--version"]),
        binary("docker", ["info", "--format", "{{json .SecurityOptions}}"]),
    ]);
    const dependencies = Object.fromEntries(
        Object.entries({
            "@agentclientprotocol/sdk": "1.5.0",
            "@agentclientprotocol/codex-acp": "1.12.0",
            "@openai/codex": "0.154.0",
            zod: "4.6.5",
        }).map(([name, expected]) => {
            try {
                const actual = JSON.parse(
                    readFileSync(
                        new URL(`../node_modules/${name}/package.json`, import.meta.url),
                        "utf8",
                    ),
                ).version;
                return [name, {expected, actual, passed: actual === expected}];
            } catch {
                return [name, {expected, passed: false}];
            }
        }),
    );
    return {
        dependencies,
        codex_payload_present: existsSync(
            new URL(
                "../node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex",
                import.meta.url,
            ),
        ),
        node: {
            expected: "24.18.0",
            actual: process.versions.node,
            passed: process.versions.node === "24.18.0",
        },
        platform: {
            actual: `${process.platform}-${process.arch}`,
            supported: process.platform === "linux" && process.arch === "x64",
        },
        git,
        docker,
        chat_ready: false,
        code_ready: false,
        certified_modes: [],
        requirements: [
            "Task 6 sandbox certification",
            "Task 7 runtime integration",
            "Task 8 repository harness",
            "Task 11 acceptance gates",
        ],
    };
}
