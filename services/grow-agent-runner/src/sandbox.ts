import {spawn, type ChildProcessWithoutNullStreams} from "node:child_process";
import {hostEnvironment} from "./containment.js";
import {randomUUID} from "node:crypto";
import {lstatSync, realpathSync} from "node:fs";
import {fileURLToPath} from "node:url";
import {PrivateStore} from "./config.js";
import type {Data} from "./protocol.js";
import {
    assertLease,
    type LeaseGuard,
    type Workspace,
    MAX_TREE_BYTES,
    retainSnapshot,
} from "./workspace.js";
import {
    command,
    docker,
    inspect,
    owned,
    observe,
    stopContainer,
    processIdentity,
    monotonic,
    type Installation,
    type ContainerRecord,
} from "./containment.js";
export {processIdentity, sameProcess} from "./containment.js";
export interface ExecutionGuard {
    lease: LeaseGuard;
    deadline: number;
    signal: AbortSignal;
}
export interface ProbeAuthority {
    assertCurrent(): void;
    deadline: number;
    signal: AbortSignal;
    setup_operation_id: string;
}
export interface SandboxResult {
    exitCode: number;
    output: Buffer;
    overflow: boolean;
    timedOut: boolean;
    containerId: string;
    stopConfirmed: boolean;
}
// EX-15: every approved image is a pinned digest, never a mutable tag, so the same name
// cannot resolve to different bytes later.
export function assertPinnedImages(images: string[]): void {
    if (images.some((x) => !/^sha256:[0-9a-f]{64}$/.test(x)))
        throw new Error("Pinned image ID required");
}
// EX-15: `docker info` must confirm the rootless, seccomp, cgroup v2 engine this sandbox
// design depends on; a root-capable or unconfined engine defeats the container limits below.
export function assertRootlessEngine(data: Data): void {
    if (
        !data.SecurityOptions?.includes("name=rootless") ||
        !data.SecurityOptions.some((x: string) => x.startsWith("name=seccomp")) ||
        data.CgroupVersion !== "2"
    )
        throw new Error("Rootless seccomp cgroup v2 required");
}
export class RootlessSandbox {
    private store: PrivateStore;
    private active = new Map<string, ContainerRecord>();
    private launches = new Map<string, Promise<SandboxResult>>();
    private frozen = new Set<string>();
    private constructor(
        readonly installation: Installation,
        root: string,
    ) {
        this.store = new PrivateStore(root);
    }
    static async open(options: {
        root: string;
        endpoint: string;
        docker: string;
        images: string[];
    }): Promise<RootlessSandbox> {
        const uid = process.getuid!();
        if (options.endpoint !== `unix:///run/user/${uid}/docker.sock`)
            throw new Error("Only the approved local rootless endpoint is supported");
        const socket = lstatSync(options.endpoint.slice(7));
        if (!socket.isSocket() || socket.uid !== uid) throw new Error("Unsafe rootless endpoint");
        assertPinnedImages(options.images);
        const binary = realpathSync(options.docker);
        if (lstatSync(binary).mode & 0o022) throw new Error("Unsafe Docker executable");
        const store = new PrivateStore(options.root);
        const previous = store.read<Installation>("installation.json");
        if (
            previous &&
            (previous.endpoint !== options.endpoint ||
                previous.docker !== binary ||
                JSON.stringify(previous.images) !== JSON.stringify(options.images))
        )
            throw new Error("Installation configuration is frozen");
        const i = previous ?? {
            id: randomUUID(),
            endpoint: options.endpoint,
            docker: binary,
            images: [...options.images],
        };
        store.write("installation.json", i);
        const info = await docker(i, ["info", "--format", "{{json .}}"]);
        if (info.code !== 0) throw new Error("Rootless engine unavailable");
        const data = JSON.parse(info.stdout.toString());
        assertRootlessEngine(data);
        Object.freeze(i.images);
        Object.freeze(i);
        const sandbox = new RootlessSandbox(i, store.root);
        await sandbox.ensureWatchdog();
        return sandbox;
    }
    private async ensureWatchdog(): Promise<void> {
        const unit = `grow-watchdog-${this.installation.id}`;
        const status = await command("/usr/bin/systemctl", ["--user", "is-active", unit]);
        if (status.code !== 0) {
            const r = await command("/usr/bin/systemd-run", [
                "--user",
                `--unit=${unit}`,
                "--property=Restart=always",
                "--property=RestartSec=1",
                "--property=UMask=0077",
                process.execPath,
                fileURLToPath(new URL("./watchdog.js", import.meta.url)),
                this.store.root,
            ]);
            if (r.code !== 0) throw new Error("Independent user watchdog unavailable");
        }
        for (let n = 0; n < 30; n++) {
            const ready = this.store.read("watchdog-ready.json");
            if (ready && monotonic() - ready.at < 1000) return;
            await new Promise((r) => setTimeout(r, 100));
        }
        throw new Error("Independent watchdog did not become ready");
    }
    async inspect(): Promise<Data[]> {
        const result: Data[] = [];
        for (const id of await owned(this.installation)) {
            const item = await inspect(this.installation, id);
            if (item.State.Running || item.State.Status === "created")
                result.push({
                    attempt_id: item.Config.Labels["digital.cadis.grow.scope"],
                    container_id: id,
                    kind: item.Config.Labels["digital.cadis.grow.kind"],
                });
        }
        return result;
    }
    async stopAttempt(d: Data, guard: ExecutionGuard): Promise<{confirmed: boolean}> {
        // Revocation removes execution authority. Containment must remain available after lease loss.
        this.frozen.add(d.attempt_id);
        return this.stopScope(d.attempt_id, "attempt");
    }
    async stopScope(scope: string, kind: "attempt" | "probe"): Promise<{confirmed: boolean}> {
        this.frozen.add(scope);
        await this.launches.get(scope)?.catch(() => {});
        let confirmed = true;
        for (const id of await owned(this.installation)) {
            const item = await inspect(this.installation, id);
            if (
                item.Config.Labels["digital.cadis.grow.scope"] !== scope ||
                item.Config.Labels["digital.cadis.grow.kind"] !== kind
            )
                continue;
            const r = this.store.read<ContainerRecord>(`container-${id}.json`) ?? {
                id,
                installation: this.installation.id,
                scope,
                kind,
                owner: {pid: 0, start: "0"},
                deadline: 0,
                heartbeat: 0,
                processes: [],
                cgroups: [],
                state: "orphan",
            };
            r.state = "revoked";
            this.store.write(`container-${id}.json`, r);
            const stopped = await stopContainer(this.installation, r);
            confirmed = confirmed && stopped;
            r.state = stopped ? "stopped" : "stop_unconfirmed";
            this.store.write(`container-${id}.json`, r);
        }
        return {confirmed};
    }
    async startModel(
        scope: string,
        kind: "attempt" | "probe",
        policy: Data,
        authority: ProbeAuthority,
        image: string,
        socket: string,
        config: Data,
    ): Promise<{child: ChildProcessWithoutNullStreams; close(): Promise<void>}> {
        const check = () => {
            authority.assertCurrent();
            if (
                authority.signal.aborted ||
                Date.now() >= authority.deadline ||
                this.frozen.has(scope)
            )
                throw new Error("Model authority revoked");
        };
        check();
        if (!/^[a-zA-Z0-9-]{1,100}$/.test(scope) || !this.installation.images.includes(image))
            throw new Error("Unapproved model image");
        const health = this.store.read("watchdog-ready.json");
        if (!health || monotonic() - health.at > 3000) throw new Error("Watchdog is unhealthy");
        const socketStat = lstatSync(socket);
        if (!socketStat.isSocket() || socketStat.uid !== process.getuid!() || /[,:]/.test(socket))
            throw new Error("Unsafe model capability socket");
        const args = [
            "create",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--user=65532:0",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--ipc=private",
            "--shm-size=65536",
            "--cgroupns=private",
            "--init",
            `--cpus=${policy.cpu_millicores / 1000}`,
            `--memory=${policy.memory_bytes}`,
            `--memory-swap=${policy.memory_bytes}`,
            `--pids-limit=${policy.pids_limit}`,
            "--ulimit=nofile=256:256",
            "--ulimit=fsize=33554432:33554432",
            "--log-driver=none",
            `--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=${policy.temporary_bytes - 65536},mode=1770,uid=65532,gid=0`,
            "--tmpfs=/workspace:rw,nosuid,nodev,noexec,size=1048576,mode=0770,uid=65532,gid=0",
            `--label=digital.cadis.grow.installation=${this.installation.id}`,
            `--label=digital.cadis.grow.scope=${scope}`,
            `--label=digital.cadis.grow.kind=${kind}`,
            "--label=digital.cadis.grow.role=model",
            "--env=HOME=/tmp/home",
            "--env=LANG=C.UTF-8",
            "--workdir=/workspace",
            "--mount",
            `type=bind,src=${socket},dst=/grow/broker.sock,readonly`,
            "--entrypoint=/usr/local/bin/node",
            image,
            "-e",
            "setInterval(()=>{},1000)",
        ];
        const made = await docker(this.installation, args);
        if (made.code !== 0) throw new Error("Model container creation failed");
        const id = made.stdout.toString().trim();
        const record: ContainerRecord = {
            id,
            installation: this.installation.id,
            scope,
            kind,
            owner: processIdentity(process.pid),
            deadline: monotonic() + Math.max(0, authority.deadline - Date.now()),
            heartbeat: monotonic() + 1000,
            processes: [],
            cgroups: [],
            state: "created",
        };
        this.store.write(`container-${id}.json`, record);
        let pulse: NodeJS.Timeout | undefined;
        let child: ChildProcessWithoutNullStreams | undefined;
        const close = async () => {
            if (pulse) clearInterval(pulse);
            record.state = "revoked";
            this.store.write(`container-${id}.json`, record);
            if (!(await stopContainer(this.installation, record)))
                throw new Error("Cannot confirm model stop");
            child?.kill("SIGKILL");
            record.state = "stopped";
            this.store.write(`container-${id}.json`, record);
        };
        try {
            check();
            if ((await docker(this.installation, ["start", id])).code !== 0)
                throw new Error("Model launch failed");
            check();
            await observe(this.installation, record);
            record.state = "running";
            this.store.write(`container-${id}.json`, record);
            child = spawn(
                this.installation.docker,
                [
                    "--host",
                    this.installation.endpoint,
                    "exec",
                    "-i",
                    "--env",
                    `GROW_NATIVE_CONFIG=${JSON.stringify(config)}`,
                    id,
                    "node",
                    config.mode === "endpoint"
                        ? "/opt/grow/dist/endpoint-child.js"
                        : "/opt/grow/native/launch.mjs",
                ],
                {env: hostEnvironment(), stdio: ["pipe", "pipe", "pipe"]},
            );
            // Do not persist adapter diagnostics. They can contain model reasoning or secrets.
            child.stderr.resume();
            pulse = setInterval(() => {
                try {
                    check();
                    record.heartbeat = monotonic() + 1000;
                    this.store.write(`container-${id}.json`, record);
                } catch {
                    void close().catch(() => {});
                }
            }, 100);
            authority.signal.addEventListener(
                "abort",
                () => {
                    void close().catch(() => {});
                },
                {once: true},
            );
            child.once("exit", () => {
                void close().catch(() => {});
            });
            return {child, close};
        } catch (e) {
            await close();
            throw e;
        }
    }
    async runSandboxedTool(
        d: Data,
        guard: ExecutionGuard,
        w: Workspace,
        argv: string[],
        options: {write: boolean; timeoutMs: number; cwd?: string; outputBytes?: number},
    ): Promise<SandboxResult> {
        if (w.attemptId !== d.attempt_id || w.leaseEpoch !== d.lease_epoch)
            throw new Error("Workspace belongs to another attempt");
        const check = () => {
            assertLease(d, guard.lease);
            if (
                guard.signal.aborted ||
                Date.now() >= guard.deadline ||
                this.frozen.has(d.attempt_id)
            )
                throw new Error("Tool authority revoked");
        };
        return this.run(
            d.attempt_id,
            "attempt",
            d.policy.sandbox,
            check,
            guard.deadline,
            guard.signal,
            w,
            argv,
            {...options, outputBytes: d.budget?.tool_output_bytes ?? 51200},
        );
    }
    async runProbe(
        descriptor: Data,
        authority: ProbeAuthority,
        argv: string[],
        timeoutMs: number,
    ): Promise<SandboxResult> {
        if (authority.setup_operation_id !== descriptor.setup_operation_id)
            throw new Error("Probe grant identity mismatch");
        const scope = `probe-${authority.setup_operation_id}`;
        const check = () => {
            authority.assertCurrent();
            if (
                authority.signal.aborted ||
                Date.now() >= authority.deadline ||
                this.frozen.has(scope)
            )
                throw new Error("Probe authority revoked");
        };
        return this.run(
            scope,
            "probe",
            descriptor.policy.sandbox,
            check,
            authority.deadline,
            authority.signal,
            null,
            argv,
            {write: false, timeoutMs, cwd: "."},
        );
    }
    private run(
        scope: string,
        kind: "attempt" | "probe",
        policy: Data,
        check: () => void,
        deadline: number,
        signal: AbortSignal,
        w: Workspace | null,
        argv: string[],
        options: {write: boolean; timeoutMs: number; cwd?: string; outputBytes?: number},
    ): Promise<SandboxResult> {
        if (this.launches.has(scope)) return Promise.reject(new Error("Concurrent tool denied"));
        const promise = this.runOnce(
            scope,
            kind,
            policy,
            check,
            deadline,
            signal,
            w,
            argv,
            options,
        );
        this.launches.set(scope, promise);
        void promise.finally(() => this.launches.delete(scope)).catch(() => {});
        return promise;
    }
    private async runOnce(
        scope: string,
        kind: "attempt" | "probe",
        policy: Data,
        check: () => void,
        deadline: number,
        signal: AbortSignal,
        w: Workspace | null,
        argv: string[],
        options: {write: boolean; timeoutMs: number; cwd?: string; outputBytes?: number},
    ): Promise<SandboxResult> {
        check();
        const health = this.store.read("watchdog-ready.json");
        if (!health || monotonic() - health.at > 3000)
            throw new Error("Independent watchdog is unhealthy");
        if (this.active.has(scope)) throw new Error("Concurrent tool denied");
        for (const [key, min, max] of [
            ["cpu_millicores", 100, 64000],
            ["memory_bytes", 67108864, 137438953472],
            ["pids_limit", 16, 4096],
            ["temporary_bytes", 1048576, 10737418240],
        ] as const)
            if (!Number.isSafeInteger(policy[key]) || policy[key] < min || policy[key] > max)
                throw new Error("Invalid sandbox resource limit");
        if (!this.installation.images.includes(policy.image_digest))
            throw new Error("Unapproved tool image");
        if (
            !argv.length ||
            argv.length > 64 ||
            argv.some((x) => typeof x !== "string" || !x || x.length > 65536 || x.includes("\0"))
        )
            throw new Error("Invalid tool argv");
        const cwd = options.cwd ?? ".";
        if (
            cwd !== "." &&
            (!/^[a-zA-Z0-9_./-]+$/.test(cwd) ||
                cwd.startsWith("/") ||
                cwd.split("/").includes(".."))
        )
            throw new Error("Unsafe tool cwd");
        if (
            options.outputBytes !== undefined &&
            (!Number.isSafeInteger(options.outputBytes) ||
                options.outputBytes < 1 ||
                options.outputBytes > 51200)
        )
            throw new Error("Invalid output limit");
        if (
            !Number.isSafeInteger(options.timeoutMs) ||
            options.timeoutMs < 1 ||
            options.timeoutMs > 1200000
        )
            throw new Error("Invalid tool timeout");
        const args = [
            "create",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--user=65532:0",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--ipc=private",
            "--shm-size=65536",
            "--cgroupns=private",
            "--init",
            `--cpus=${policy.cpu_millicores / 1000}`,
            `--memory=${policy.memory_bytes}`,
            `--memory-swap=${policy.memory_bytes}`,
            `--pids-limit=${policy.pids_limit}`,
            "--ulimit=nofile=256:256",
            `--ulimit=fsize=${w?.limit ?? MAX_TREE_BYTES}:${w?.limit ?? MAX_TREE_BYTES}`,
            "--log-driver=none",
            `--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=${policy.temporary_bytes - 65536},mode=1770,uid=65532,gid=0`,
            `--label=digital.cadis.grow.installation=${this.installation.id}`,
            `--label=digital.cadis.grow.scope=${scope}`,
            `--label=digital.cadis.grow.kind=${kind}`,
            "--env=HOME=/tmp/home",
            "--env=LANG=C.UTF-8",
            "--env=PATH=/usr/local/bin:/usr/bin:/bin",
            "--workdir=/workspace",
        ];
        if (w) {
            if (/[,:]/.test(w.checkout)) throw new Error("Unsafe mount path");
            args.push(
                "--mount",
                `type=bind,src=${w.checkout},dst=${options.write ? "/seed" : "/workspace"},readonly`,
            );
        }
        if (!w || options.write) {
            const volume = `grow-${this.installation.id}-${randomUUID()}`;
            this.store.write(`volume-${volume}.json`, {
                volume,
                installation: this.installation.id,
                scope,
                kind,
                limit: w?.limit ?? MAX_TREE_BYTES,
            });
            check();
            const made = await docker(this.installation, [
                "volume",
                "create",
                "--driver=local",
                "--opt=type=tmpfs",
                "--opt=device=tmpfs",
                `--opt=o=size=${w?.limit ?? MAX_TREE_BYTES},uid=65532,gid=0,mode=2770,nosuid,nodev`,
                `--label=digital.cadis.grow.installation=${this.installation.id}`,
                `--label=digital.cadis.grow.scope=${scope}`,
                volume,
            ]);
            if (made.code !== 0) throw new Error("Bounded workspace volume unavailable");
            const volumeInfo = await docker(this.installation, ["volume", "inspect", volume]);
            if (volumeInfo.code !== 0) throw new Error("Cannot inspect workspace volume");
            const ownedVolume = JSON.parse(volumeInfo.stdout.toString())[0];
            if (
                ownedVolume.Name !== volume ||
                ownedVolume.Labels?.["digital.cadis.grow.installation"] !== this.installation.id ||
                ownedVolume.Labels?.["digital.cadis.grow.scope"] !== scope ||
                ownedVolume.Driver !== "local" ||
                ownedVolume.Options?.type !== "tmpfs"
            )
                throw new Error("Foreign workspace volume");
            args.push("--mount", `type=volume,src=${volume},dst=/workspace,volume-nocopy`);
        }
        args.push(
            "--entrypoint=/usr/local/bin/node",
            policy.image_digest,
            "-e",
            "process.umask(7);setInterval(()=>{},1000)",
        );
        const created = await docker(this.installation, args);
        if (created.code !== 0)
            throw new Error(
                "Contained process creation failed: " + created.stderr.toString().slice(0, 2000),
            );
        const id = created.stdout.toString().trim();
        const r: ContainerRecord = {
            id,
            installation: this.installation.id,
            scope,
            kind,
            owner: processIdentity(process.pid),
            deadline: monotonic() + Math.max(0, Math.min(deadline - Date.now(), options.timeoutMs)),
            heartbeat: monotonic() + 1000,
            processes: [],
            cgroups: [],
            state: "created",
        };
        this.store.write(`container-${id}.json`, r);
        this.active.set(scope, r);
        let expired = false;
        const abort = new AbortController();
        const pulse = setInterval(() => {
            try {
                check();
                if (monotonic() >= r.deadline) throw new Error("Tool deadline");
                r.heartbeat = monotonic() + 1000;
                this.store.write(`container-${id}.json`, r);
            } catch {
                expired = true;
                abort.abort();
                void this.stopScope(scope, kind);
            }
        }, 100);
        const cancelled = () => {
            expired = true;
            abort.abort();
            void this.stopScope(scope, kind);
        };
        signal.addEventListener("abort", cancelled, {once: true});
        let result: SandboxResult | undefined;
        try {
            check();
            if ((await docker(this.installation, ["start", id])).code !== 0)
                throw new Error("Contained process launch failed");
            r.state = "running";
            await observe(this.installation, r);
            this.store.write(`container-${id}.json`, r);
            if (w && options.write) {
                const seed = await docker(
                    this.installation,
                    ["exec", id, "node", "/grow/worker.mjs", "seed"],
                    65536,
                    10000,
                    abort.signal,
                );
                if (seed.code !== 0) throw new Error("Workspace seed failed");
            }
            check();
            const cmd = await docker(
                this.installation,
                [
                    "exec",
                    "--workdir",
                    `/workspace${cwd === "." ? "" : "/" + cwd}`,
                    id,
                    "/bin/sh",
                    "-c",
                    'umask 007; exec "$@"',
                    "grow",
                    ...argv,
                ],
                options.outputBytes ?? 51200,
                options.timeoutMs,
                abort.signal,
            );
            if (!expired && w && options.write) {
                check();
                if ((await docker(this.installation, ["pause", id])).code !== 0)
                    throw new Error("Cannot freeze final snapshot");
                const snapshot = await docker(
                    this.installation,
                    ["cp", `${id}:/workspace/.`, "-"],
                    w.limit + 20000 * 1024,
                    10000,
                );
                if (snapshot.code !== 0 || snapshot.overflow)
                    throw new Error("Snapshot export failed");
                check();
                retainSnapshot(w, snapshot.stdout);
            }
            result = {
                exitCode: (cmd.overflow || expired) && cmd.code === 0 ? 137 : cmd.code,
                output: Buffer.concat([cmd.stdout, cmd.stderr]),
                overflow: cmd.overflow,
                timedOut: expired,
                containerId: id,
                stopConfirmed: false,
            };
        } finally {
            clearInterval(pulse);
            signal.removeEventListener("abort", cancelled);
            r.state = "revoked";
            this.store.write(`container-${id}.json`, r);
            const confirmed = await stopContainer(this.installation, r);
            r.state = confirmed ? "stopped" : "stop_unconfirmed";
            this.store.write(`container-${id}.json`, r);
            this.active.delete(scope);
            if (!confirmed) throw new Error("Cannot confirm containment stop");
            if (result) result.stopConfirmed = confirmed;
        }
        return result!;
    }
}
