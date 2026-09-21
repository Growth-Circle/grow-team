import {spawn} from "node:child_process";
import {readFileSync, existsSync, readdirSync} from "node:fs";
import {PrivateStore} from "./config.js";
import type {Data} from "./protocol.js";
export interface ProcessIdentity {
    pid: number;
    start: string;
}
export function processIdentity(pid: number): ProcessIdentity {
    const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
    return {pid, start: stat.slice(stat.lastIndexOf(")") + 2).split(" ")[19]!};
}
export function sameProcess(p: ProcessIdentity): boolean {
    try {
        return processIdentity(p.pid).start === p.start;
    } catch {
        return false;
    }
}
export function monotonic(): number {
    return Number(readFileSync("/proc/uptime", "utf8").split(" ")[0]) * 1000;
}
export interface Installation {
    id: string;
    endpoint: string;
    docker: string;
    images: string[];
}
export interface ContainerRecord {
    id: string;
    installation: string;
    scope: string;
    kind: "attempt" | "probe";
    owner: ProcessIdentity;
    deadline: number;
    heartbeat: number;
    processes: ProcessIdentity[];
    cgroups: string[];
    state: string;
}
export interface CommandResult {
    code: number;
    stdout: Buffer;
    stderr: Buffer;
    overflow: boolean;
}
export function hostEnvironment(): Record<string, string> {
    return {
        PATH: "/usr/bin:/bin",
        HOME: "/nonexistent",
        LANG: "C.UTF-8",
        XDG_RUNTIME_DIR: `/run/user/${process.getuid!()}`,
        DBUS_SESSION_BUS_ADDRESS: `unix:path=/run/user/${process.getuid!()}/bus`,
    };
}
export async function command(
    binary: string,
    args: string[],
    limit = 65536,
    timeout = 10000,
    signal?: AbortSignal,
): Promise<CommandResult> {
    return new Promise((resolve, reject) => {
        const p = spawn(binary, args, {env: hostEnvironment(), stdio: ["ignore", "pipe", "pipe"]});
        const out: Buffer[] = [],
            err: Buffer[] = [];
        let size = 0,
            overflow = false;
        const stop = () => {
            p.kill("SIGKILL");
        };
        const timer = setTimeout(stop, timeout);
        signal?.addEventListener("abort", stop, {once: true});
        if (signal?.aborted) stop();
        const chunk = (target: Buffer[], b: Buffer) => {
            const remaining = limit - size;
            if (remaining > 0) target.push(b.subarray(0, remaining));
            size += b.length;
            if (size > limit) {
                overflow = true;
                stop();
            }
        };
        p.stdout.on("data", (b) => chunk(out, b));
        p.stderr.on("data", (b) => chunk(err, b));
        p.once("error", (e) => {
            clearTimeout(timer);
            signal?.removeEventListener("abort", stop);
            reject(e);
        });
        p.once("close", (code) => {
            clearTimeout(timer);
            signal?.removeEventListener("abort", stop);
            resolve({
                code: code ?? 137,
                stdout: Buffer.concat(out),
                stderr: Buffer.concat(err),
                overflow,
            });
        });
    });
}
export async function docker(
    i: Installation,
    args: string[],
    limit = 65536,
    timeout = 10000,
    signal?: AbortSignal,
): Promise<CommandResult> {
    return command(i.docker, ["--host", i.endpoint, ...args], limit, timeout, signal);
}
export async function inspect(i: Installation, id: string): Promise<Data> {
    if (!/^[a-f0-9]{64}$/.test(id)) throw new Error("Immutable container ID required");
    const r = await docker(i, ["inspect", id]);
    if (r.code !== 0) throw new Error("Container inspection failed");
    const item = JSON.parse(r.stdout.toString())[0];
    if (item.Id !== id || item.Config.Labels?.["digital.cadis.grow.installation"] !== i.id)
        throw new Error("Foreign container ownership");
    return item;
}
export async function owned(i: Installation, activeOnly = false): Promise<string[]> {
    const r = await docker(i, [
        "ps",
        activeOnly ? "-q" : "-aq",
        "--no-trunc",
        "--filter",
        `label=digital.cadis.grow.installation=${i.id}`,
    ]);
    if (r.code !== 0) throw new Error("Cannot discover containment");
    return r.stdout.toString().trim().split("\n").filter(Boolean);
}
export async function observe(i: Installation, r: ContainerRecord): Promise<void> {
    const item = await inspect(i, r.id);
    if (!item.State.Running) return;
    const top = await docker(i, ["top", r.id, "-eo", "pid"]);
    if (top.code !== 0) throw new Error("Cannot observe process set");
    for (const line of top.stdout.toString().split("\n").slice(1)) {
        const pid = Number(line.trim());
        if (!pid) continue;
        try {
            const p = processIdentity(pid);
            if (!r.processes.some((x) => x.pid === p.pid && x.start === p.start))
                r.processes.push(p);
            const cg = readFileSync(`/proc/${pid}/cgroup`, "utf8")
                .split("\n")
                .find((x) => x.startsWith("0::"))
                ?.slice(3);
            if (cg && !cg.includes("..")) {
                const path = `/sys/fs/cgroup${cg}/cgroup.events`;
                if (!r.cgroups.includes(path)) r.cgroups.push(path);
            }
        } catch {}
    }
}
export async function stopContainer(
    i: Installation,
    r: ContainerRecord,
    authorizeStop: () => boolean = () => true,
): Promise<boolean> {
    let item = await inspect(i, r.id);
    if (
        item.Config.Labels["digital.cadis.grow.scope"] !== r.scope ||
        item.Config.Labels["digital.cadis.grow.kind"] !== r.kind
    )
        throw new Error("Container scope mismatch");
    if (item.State.Running) {
        await observe(i, r).catch(() => {});
        if (!authorizeStop()) return false;
        if (item.State.Paused) {
            await docker(i, ["unpause", r.id]);
            if (!authorizeStop()) return false;
        }
        await docker(i, ["kill", "--signal=KILL", r.id]);
    }
    for (let n = 0; n < 30; n++) {
        item = await inspect(i, r.id);
        const gone = r.processes.every((p) => !sameProcess(p));
        const empty = r.cgroups.every(
            (path) => !existsSync(path) || /^populated 0$/m.test(readFileSync(path, "utf8")),
        );
        if (!item.State.Running && !item.State.Paused && item.State.Pid === 0 && gone && empty)
            return true;
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return false;
}
export function readRecords(store: PrivateStore): ContainerRecord[] {
    return readdirSync(store.root)
        .filter((x) => /^container-[a-f0-9]{64}\.json$/.test(x))
        .map((x) => store.read<ContainerRecord>(x)!);
}
