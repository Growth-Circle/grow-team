#!/usr/bin/env node
import {readFileSync} from "node:fs";
import {homedir} from "node:os";
import {join} from "node:path";
import {PrivateStore, controlOrigin} from "./config.js";
import {Journal} from "./journal.js";
import {Transport, Connection} from "./transport.js";
import {OwnerRegistry, doctor} from "./owner.js";
import {RuntimeSupervisor} from "./runtime-supervisor.js";
import {Coordinator, runService} from "./supervisor.js";

export async function main(args = process.argv.slice(2)): Promise<void> {
    if (process.versions.node !== "24.18.0") throw new Error("Node 24.18.0 is required");
    if (args[0] === "doctor") {
        console.log(JSON.stringify(await doctor(), null, 2));
        return;
    }
    if (!args[0] || args[0] === "help") {
        console.log(
            "grow-agent connect ORIGIN [--repair]\ngrow-agent status\ngrow-agent rotate\ngrow-agent workspace ALIAS PATH METADATA.json\ngrow-agent catalog CATALOG.json\ngrow-agent secret NAME FILE\ngrow-agent doctor\ngrow-agent run",
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
            case "run": {
                const runtime = await RuntimeSupervisor.open(store, journal, registry);
                const c = new Coordinator(journal, transport, runtime, saved!.runner_id, registry);
                const abort = new AbortController();
                const stop = () => abort.abort();
                process.once("SIGTERM", stop);
                process.once("SIGINT", stop);
                try {
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
main().catch(() => {
    console.error(
        "Runner command failed. Check connection state, owner configuration, and doctor.",
    );
    process.exitCode = 1;
});
