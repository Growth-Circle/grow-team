import {readFileSync, existsSync} from "node:fs";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {PrivateStore, workspacePath, localSecret} from "./config.js";
import {Transport} from "./transport.js";
import {parse, parseChecks, canonical, digest, type Data} from "./protocol.js";
const exec = promisify(execFile);
export class OwnerRegistry {
    constructor(
        private store: PrivateStore,
        private transport: Transport,
    ) {}
    read(): Data {
        return this.store.read("registry.json") ?? {workspaces: {}, secrets: {}, catalog: null};
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
        const state = this.read(),
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
        const item = this.read().workspaces[repository.workspace_alias];
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
        const state = this.read();
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
    }
    assertRuntime(descriptor: Data): void {
        const catalog = this.read().catalog;
        if (!catalog) throw new Error("Approve a local catalog first");
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
                item = this.read().workspaces[binding.workspace_alias];
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
