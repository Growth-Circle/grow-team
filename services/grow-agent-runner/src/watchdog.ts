import {PrivateStore} from "./config.js";
import {
    type Installation,
    type ContainerRecord,
    owned,
    inspect,
    stopContainer,
    sameProcess,
    monotonic,
} from "./containment.js";
export async function watchdogTick(store: PrivateStore, i: Installation): Promise<void> {
    for (const id of await owned(i, true)) {
        const item = await inspect(i, id);
        const orphan: ContainerRecord = {
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
        const current = (): ContainerRecord => {
            const record = store.read<ContainerRecord>(`container-${id}.json`) ?? orphan;
            for (const key of ["id", "installation", "scope", "kind"] as const)
                if (record[key] !== orphan[key])
                    throw new Error("Container journal identity mismatch");
            return record;
        };
        const needsStop = (record: ContainerRecord): boolean =>
            !sameProcess(record.owner) ||
            monotonic() >= record.deadline ||
            monotonic() > record.heartbeat ||
            ["stopped", "revoked", "stop_unconfirmed"].includes(record.state);
        // Discovery and inspection can overlap a launch or heartbeat publication.
        const record = current();
        if (!needsStop(record)) continue;
        let superseded = false;
        const confirmed = await stopContainer(i, record, () => {
            // Inspect and observe both await Docker. Recheck authority before the effect.
            superseded = !needsStop(current());
            return !superseded;
        });
        if (superseded) continue;
        // The watchdog never replaces the supervisor's launch or heartbeat record.
        store.write(`watchdog-stop-${id}.json`, {
            ...record,
            state: confirmed ? "stopped" : "stop_unconfirmed",
            observed_at: monotonic(),
        });
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
