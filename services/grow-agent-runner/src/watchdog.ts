import {PrivateStore} from "./config.js";
import {
    type Installation,
    type ContainerRecord,
    owned,
    inspect,
    stopContainer,
    readRecords,
    sameProcess,
    monotonic,
} from "./containment.js";
export async function watchdogTick(store: PrivateStore, i: Installation): Promise<void> {
    const records = new Map(readRecords(store).map((r) => [r.id, r]));
    for (const id of await owned(i, true)) {
        const item = await inspect(i, id);
        let r = records.get(id);
        if (!r) {
            r = {
                id,
                installation: i.id,
                scope: item.Config.Labels["digital.cadis.grow.scope"],
                kind: item.Config.Labels["digital.cadis.grow.kind"],
                owner: {pid: 0, start: "0"},
                deadline: 0,
                heartbeat: 0,
                processes: [],
                cgroups: [],
                state: "orphan",
            };
            store.write(`container-${id}.json`, r);
        }
        if (r.installation !== i.id) throw new Error("Foreign journal identity");
        if (
            !sameProcess(r.owner) ||
            monotonic() >= r.deadline ||
            monotonic() > r.heartbeat ||
            r.state === "stopped" ||
            r.state === "revoked"
        ) {
            if (await stopContainer(i, r)) {
                r.state = "stopped";
                store.write(`container-${id}.json`, r);
            } else {
                r.state = "stop_unconfirmed";
                store.write(`container-${id}.json`, r);
            }
        }
    }
    store.write("watchdog-ready.json", {at: monotonic(), pid: process.pid});
}
if (process.argv[1]?.endsWith("/watchdog.js")) {
    const store = new PrivateStore(process.argv[2]!);
    const i = store.read<Installation>("installation.json");
    if (!i) throw new Error("Installation missing");
    for (;;) {
        try {
            await watchdogTick(store, i);
        } catch {
            store.write("watchdog-error.json", {
                at: monotonic(),
                reason: "Containment inspection or stop failed",
            });
        }
        await new Promise((r) => setTimeout(r, 200));
    }
}
