#!/usr/bin/env node
import {randomUUID} from "node:crypto";
import {readFileSync} from "node:fs";
import {homedir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {PrivateStore, controlOrigin} from "./config.js";
import {Journal} from "./journal.js";
import {Transport, Connection, TransportError} from "./transport.js";
import {OwnerRegistry, doctor} from "./owner.js";
import type {Data} from "./protocol.js";
import {RuntimeSupervisor} from "./runtime-supervisor.js";
import {Coordinator, runService} from "./supervisor.js";
import {
    RemoteOperationBroker,
    type RemotePublicationConfiguration,
} from "./remote-operation-broker.js";
import {
    BoundedTitenContext,
    OwnerMemorySignals,
    type TitenContextConnection,
} from "./titen-context.js";

export function createRuntimeExtensions(
    store: PrivateStore,
    journal: Journal,
    registry: OwnerRegistry,
    remote = new RemoteOperationBroker(
        (descriptor) =>
            registry.publicationFor(descriptor) as RemotePublicationConfiguration | null,
        undefined,
        journal.partition(journal.identityScope()),
        (value) => registry.publicationForRemote(value) as RemotePublicationConfiguration | null,
    ),
) {
    const log = journal.partition(journal.identityScope());
    const context = new BoundedTitenContext(
        log,
        (descriptor) => registry.titenFor(descriptor) as TitenContextConnection | null,
        journal.identityScope(),
    );
    return {
        context: async (descriptor: any, channel: any) => {
            channel.lease();
            const result = await context.enrich(descriptor);
            channel.lease();
            return result;
        },
        publish: (descriptor: any, broker: any, result: any, channel: any) =>
            remote.publish(descriptor, broker, result, channel),
    };
}

type HostKind = "workstation" | "server" | "unknown";
type MetadataUpdate = {name: string; host_kind: HostKind; expected_metadata_revision: number};
type RunnerMetadata = {name: string; host_kind: HostKind; metadata_revision: number};

function safeName(value: string): boolean {
    return value.length > 0 && value.length <= 200 && !/[/\\\x00-\x1f\x7f]/.test(value);
}

function metadataFrom(value: Data): RunnerMetadata {
    const metadata = value.metadata;
    if (
        !metadata ||
        typeof metadata !== "object" ||
        Array.isArray(metadata) ||
        typeof metadata.name !== "string" ||
        !safeName(metadata.name) ||
        !["workstation", "server", "unknown"].includes(metadata.host_kind) ||
        !Number.isSafeInteger(metadata.metadata_revision) ||
        metadata.metadata_revision < 1
    )
        throw new TransportError("protocol", "invalid_metadata");
    return {
        name: metadata.name,
        host_kind: metadata.host_kind as HostKind,
        metadata_revision: metadata.metadata_revision,
    };
}

export function parseMetadataCommand(args: string[]): MetadataUpdate | null {
    if (args.length === 1 && args[0] === "metadata") return null;
    if (args.length !== 5 || args[0] !== "metadata" || args[1] !== "set")
        throw new Error("Use metadata or metadata set NAME CATEGORY EXPECTED_REVISION");
    const name = args[2] ?? "",
        host_kind = args[3] ?? "",
        revision = args[4] ?? "";
    if (
        !safeName(name) ||
        !["workstation", "server", "unknown"].includes(host_kind) ||
        !/^[1-9][0-9]*$/.test(revision)
    )
        throw new Error("Invalid runner metadata arguments");
    const expected_metadata_revision = Number(revision);
    if (!Number.isSafeInteger(expected_metadata_revision))
        throw new Error("Invalid runner metadata revision");
    return {name, host_kind: host_kind as HostKind, expected_metadata_revision};
}

export async function readRunnerMetadata(transport: Transport): Promise<RunnerMetadata> {
    return metadataFrom(await transport.request("/runner/metadata"));
}

export async function setRunnerMetadata(
    transport: Transport,
    update: MetadataUpdate,
): Promise<Data> {
    try {
        const result = await transport.mutate(
            "runner_metadata",
            `metadata:${randomUUID()}`,
            "/runner/metadata",
            update,
        );
        return {outcome: "updated", metadata: metadataFrom(result)};
    } catch (error) {
        if (
            !(error instanceof TransportError) ||
            (error.kind !== "transient" &&
                !(error.kind === "protocol" && error.code === "invalid_response"))
        )
            throw error;
        const observed = await readRunnerMetadata(transport);
        return {
            outcome: "observed_after_uncertain_update",
            metadata: observed,
            requested_matches:
                observed.metadata_revision === update.expected_metadata_revision + 1 &&
                observed.name === update.name &&
                observed.host_kind === update.host_kind,
        };
    }
}

export async function main(args = process.argv.slice(2)): Promise<void> {
    if (process.versions.node !== "24.18.0") throw new Error("Node 24.18.0 is required");
    if (args[0] === "doctor") {
        console.log(JSON.stringify(await doctor(), null, 2));
        return;
    }
    if (!args[0] || args[0] === "help") {
        console.log(
            "grow-agent connect ORIGIN [--repair]\ngrow-agent status\ngrow-agent rotate\ngrow-agent metadata\ngrow-agent metadata set NAME CATEGORY EXPECTED_REVISION\ngrow-agent workspace ALIAS PATH METADATA.json\ngrow-agent catalog CATALOG.json\ngrow-agent secret NAME FILE\ngrow-agent remote CONFIG.json\ngrow-agent titen CONFIG.json\ngrow-agent memory-signal SIGNAL.json\ngrow-agent doctor\ngrow-agent run",
        );
        return;
    }
    if (process.platform !== "linux" || process.arch !== "x64")
        throw new Error("The foundation distribution supports Linux x64");
    process.umask(0o077);
    const store = new PrivateStore(
        process.env.GROW_AGENT_STATE ?? join(homedir(), ".local/state/grow-agent"),
    );
    const saved = store.read("connection.json");
    const origin = args[0] === "connect" ? controlOrigin(args[1] ?? "") : saved?.origin;
    if (!origin) throw new Error("Connect this device first");
    const journal = new Journal(store.root);
    try {
        const transport = new Transport(origin, journal, () => {
            const c = store.read("connection.json");
            return c?.token ?? null;
        });
        const connection = new Connection(store, transport),
            registry = new OwnerRegistry(store, transport);
        switch (args[0]) {
            case "connect":
                console.log(
                    JSON.stringify(
                        await connection.start(
                            process.env.GROW_AGENT_NAME ?? "Grow Agent",
                            args.includes("--repair"),
                        ),
                    ),
                );
                break;
            case "status":
                console.log(JSON.stringify(await connection.poll()));
                break;
            case "rotate":
                await connection.rotate();
                console.log("Credential rotated.");
                break;
            case "metadata": {
                const update = parseMetadataCommand(args);
                await connection.access();
                console.log(
                    JSON.stringify(
                        update === null
                            ? {metadata: await readRunnerMetadata(transport)}
                            : await setRunnerMetadata(transport, update),
                    ),
                );
                break;
            }
            case "workspace":
                await connection.access();
                await registry.workspace(
                    args[1] ?? "",
                    args[2] ?? "",
                    JSON.parse(readFileSync(args[3] ?? "", "utf8")),
                );
                console.log("Workspace registered.");
                break;
            case "catalog":
                await connection.access();
                await registry.catalog(JSON.parse(readFileSync(args[1] ?? "", "utf8")));
                console.log("Catalog reported.");
                break;
            case "secret":
                registry.secret(args[1] ?? "", args[2] ?? "");
                console.log("Local secret reference registered.");
                break;
            case "remote":
                registry.configureRemoteOperations(JSON.parse(readFileSync(args[1] ?? "", "utf8")));
                console.log("Owner remote operation configuration registered.");
                break;
            case "titen":
                registry.configureTiten(JSON.parse(readFileSync(args[1] ?? "", "utf8")));
                console.log("Owner Titen configuration registered.");
                break;
            case "memory-signal": {
                const signal = JSON.parse(readFileSync(args[1] ?? "", "utf8"));
                await new OwnerMemorySignals(
                    store,
                    (item) =>
                        registry.titenFor({
                            audience: item.audience,
                            repository: {canonical_origin: "memory-signal"},
                        }) as TitenContextConnection | null,
                    () => registry.memoryProtectedSecrets(),
                ).record(signal);
                console.log("Verified owner memory signal recorded.");
                break;
            }
            case "run": {
                const remote = new RemoteOperationBroker(
                    (descriptor) =>
                        registry.publicationFor(
                            descriptor,
                        ) as RemotePublicationConfiguration | null,
                    undefined,
                    journal.partition(journal.identityScope()),
                    (value) =>
                        registry.publicationForRemote(
                            value,
                        ) as RemotePublicationConfiguration | null,
                );
                const runtime = await RuntimeSupervisor.open(
                    store,
                    journal,
                    registry,
                    createRuntimeExtensions(store, journal, registry, remote),
                );
                const c = new Coordinator(journal, transport, runtime, saved!.runner_id, registry);
                const abort = new AbortController();
                const stop = () => abort.abort();
                process.once("SIGTERM", stop);
                process.once("SIGINT", stop);
                try {
                    await remote.recover((attemptId) => c.operationRecovery(attemptId));
                    await runService(c, connection, transport, abort.signal, {setups: true});
                } finally {
                    await c.stopActive();
                    process.removeListener("SIGTERM", stop);
                    process.removeListener("SIGINT", stop);
                }
                break;
            }
            default:
                throw new Error("Unknown command; use help");
        }
    } finally {
        journal.close();
    }
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1])
    main().catch(() => {
        console.error(
            "Runner command failed. Check connection state, owner configuration, and doctor.",
        );
        process.exitCode = 1;
    });
