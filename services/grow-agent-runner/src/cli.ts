#!/usr/bin/env node
import {readFileSync} from "node:fs";
import {homedir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {PrivateStore, controlOrigin} from "./config.js";
import {Journal} from "./journal.js";
import {Transport, Connection} from "./transport.js";
import {OwnerRegistry, doctor} from "./owner.js";
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

export async function main(args = process.argv.slice(2)): Promise<void> {
    if (process.versions.node !== "24.18.0") throw new Error("Node 24.18.0 is required");
    if (args[0] === "doctor") {
        console.log(JSON.stringify(await doctor(), null, 2));
        return;
    }
    if (!args[0] || args[0] === "help") {
        console.log(
            "grow-agent connect ORIGIN [--repair]\ngrow-agent status\ngrow-agent rotate\ngrow-agent workspace ALIAS PATH METADATA.json\ngrow-agent catalog CATALOG.json\ngrow-agent secret NAME FILE\ngrow-agent remote CONFIG.json\ngrow-agent titen CONFIG.json\ngrow-agent memory-signal SIGNAL.json\ngrow-agent doctor\ngrow-agent run",
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
