import type {ProbeAuthority} from "./sandbox.js";
import {randomUUID} from "node:crypto";
import {setTimeout as sleep} from "node:timers/promises";
import {Journal, type JournalLog} from "./journal.js";
import {Transport, TransportError} from "./transport.js";
import {OperationRecovery} from "./operation-recovery.js";
import type {OwnerRegistry} from "./owner.js";
import {parse, validateDescriptor, type Data} from "./protocol.js";

// The server answers a busy authority check with Retry-After: 1.
const PROBE_CONTENTION_RETRIES = 2;

export interface ProcessHandle {
    attempt_id: string;
    [key: string]: unknown;
}
export interface Supervisor {
    // Inspect all owned processes and containers, including those absent from the journal.
    inspect(): Promise<ProcessHandle[]>;
    stop(handle: ProcessHandle): Promise<{confirmed: boolean}>;
    canExecute(): boolean;
    start(descriptor: Data, authority: AttemptChannel): Promise<void>;
    applyInput(
        descriptor: Data,
        input: Data,
    ): Promise<{outcome: "applied" | "not_applied"; receipt_id: string}>;
    probe(
        descriptor: Data,
        authority?: ProbeChannel,
    ): Promise<{
        state: "ready" | "needs_action" | "failed";
        capabilities: Data;
        requirements: Data[];
    }>;
}
export interface ProbeChannel extends ProbeAuthority {
    validate(): Promise<void>;
    request(route: string, extra?: Data): Promise<Data>;
}
export interface AttemptChannel {
    event(type: string, payload: Data): Promise<void>;
    pollInputs?(): Promise<void>;
    hasPendingInput?(): boolean;
    inputApplied?(
        input: Data,
        receipt: {outcome: "applied" | "not_applied"; receipt_id: string},
    ): Promise<void>;
    operations: OperationBoundary;
    lease: () => Data;
    request?(route: string, extra?: Data): Promise<Data>;
    upload?(payload: Data, bytes: Buffer): Promise<Data>;
    download?(referenceId: string): Promise<Buffer>;
    runtimeTerminated?(): void;
}
export class OperationBoundary {
    private identity: Data;
    constructor(
        private journal: JournalLog,
        private transport: Transport,
        private currentLease: () => Data,
        private lane: <T>(action: () => Promise<T>) => Promise<T> = (action) => action(),
    ) {
        this.identity = structuredClone(currentLease());
    }
    private assertLease(lease: Data): void {
        const current = this.currentLease();
        for (const key of ["job_id", "attempt_id", "lease_epoch"])
            if (current[key] !== lease[key])
                throw new Error("Operation belongs to another attempt");
    }
    async propose(lease: Data, id: string, args: Data, extras: Data = {}): Promise<Data> {
        this.assertLease(lease);
        for (const key of Object.keys(extras))
            if (!["tree_hash", "diff_artifact_id"].includes(key))
                throw new Error("Invalid operation metadata");
        parse("operation_arguments", args);
        const result = (
            await this.lane(() =>
                this.transport.mutate("proposal", `proposal:${id}`, "/runner/operations/propose", {
                    ...this.currentLease(),
                    ...extras,
                    operation_id: id,
                    arguments: args,
                }),
            )
        ).operation;
        this.assertLease(lease);
        return result;
    }
    async consume(lease: Data, proposal: Data): Promise<Data> {
        this.assertLease(lease);
        const id = proposal.operation_id;
        const result = await this.lane(() =>
            this.transport.mutate("consume", `consume:${id}`, "/runner/operations/consume", {
                ...this.currentLease(),
                operation_id: id,
                expected_version: proposal.version,
                operation_hash: proposal.operation_hash,
                ...(proposal.nonce ? {nonce: proposal.nonce} : {}),
            }),
        );
        this.assertLease(lease);
        if (
            result.operation.operation_id !== id ||
            result.operation.operation_hash !== proposal.operation_hash ||
            result.operation.status !== "started" ||
            result.operation.version !== proposal.version + 1
        )
            throw new Error("Invalid operation authority");
        this.journal.prepare("authority", `authority:${id}`, "local", {
            lease,
            operation: result.operation,
        });
        return result.operation;
    }
    beginEffect(operationId: string): Data {
        const lease = this.currentLease();
        const authority = this.journal.get(`authority:${operationId}`);
        if (!authority) throw new Error("No durable consume authority");
        for (const k of ["job_id", "attempt_id", "lease_epoch"])
            if (authority.request.lease[k] !== lease[k])
                throw new Error("Operation belongs to an old lease");
        const effect = this.journal.prepare("effect", `effect:${operationId}`, "local", {
            operation: authority.request.operation,
        });
        if (effect.state !== "prepared") throw new Error("Effect already started or uncertain");
        // Commit before calling the broker. A crash after this point requires reconciliation.
        this.journal.uncertain(effect.id);
        return authority.request.operation;
    }
    finishEffect(operationId: string, receipt: Data): void {
        const authority = this.journal.get(`authority:${operationId}`);
        if (
            !authority ||
            ["job_id", "attempt_id", "lease_epoch"].some(
                (k) => authority.request.lease[k] !== this.identity[k],
            )
        )
            throw new Error("Effect receipt belongs to another attempt");
        this.journal.complete(`effect:${operationId}`, receipt);
    }
    async reconcile(lease: Data): Promise<Data> {
        this.assertLease(lease);
        const result = await this.lane(() =>
            this.transport.request("/runner/operations", this.currentLease()),
        );
        this.assertLease(lease);
        return result;
    }
    async remoteReceipt(lease: Data, receipt: Data): Promise<Data> {
        this.assertLease(lease);
        parse("remote_receipt", receipt);
        const response = await this.transport.mutate(
            "remote_receipt",
            `remote:${receipt.operation_id}`,
            "/runner/operations/reconcile",
            {...lease, operation_id: receipt.operation_id, receipt},
        );
        this.assertLease(lease);
        return response;
    }
}
interface Session {
    descriptor: Data;
    valid: boolean;
    version: number;
    expiry: number;
    queue: Promise<void>;
    watchdog: NodeJS.Timeout | null;
}
export class Coordinator {
    private journal: JournalLog;
    private scope: string;
    private active: Session | null = null;
    private reconciled = false;
    private generation = 0;
    private inputFlights = new Map<string, Promise<void>>();
    private claiming: Promise<void> | null = null;
    private stopping: Promise<void> | null = null;
    private stopFailure: Error | null = null;
    private probeAbort: AbortController | null = null;
    private probing: Promise<void> | null = null;
    private probeError: Error | null = null;
    constructor(
        journal: Journal,
        private transport: Transport,
        private supervisor: Supervisor,
        private runnerId: string,
        private registry: Pick<OwnerRegistry, "assertRuntime">,
    ) {
        this.scope = journal.identityScope();
        this.journal = journal.partition(this.scope);
    }
    operationRecovery(attemptId: string): OperationRecovery {
        this.assertScope();
        const attempt = this.journal.get(`attempt:${attemptId}`);
        if (!attempt || attempt.request.descriptor.runner_id !== this.runnerId)
            throw new Error("No original durable attempt identity");
        const d = attempt.request.descriptor;
        return new OperationRecovery(
            this.journal,
            this.transport,
            {
                schema_version: 1,
                job_id: d.job_id,
                attempt_id: d.attempt_id,
                lease_epoch: d.lease_epoch,
                job_version: attempt.request.job_version,
            },
            () => this.assertScope(),
        );
    }
    private assertScope(): void {
        if ((this.transport.currentScope?.() ?? "") !== this.scope)
            throw new Error("Coordinator belongs to a retired runner scope");
    }
    private lease(session: Session): Data {
        this.assertScope();
        if (this.stopFailure) throw this.stopFailure;
        if (!session.valid || this.active !== session)
            throw new Error("Attempt channel is retired");
        if (Date.now() >= session.expiry) throw new Error("No current lease authority");
        const d = session.descriptor;
        return {
            schema_version: 1,
            job_id: d.job_id,
            attempt_id: d.attempt_id,
            lease_epoch: d.lease_epoch,
            job_version: session.version,
        };
    }
    private sessionFor(d: Data): Session {
        const session = this.active;
        if (
            !session ||
            ["job_id", "attempt_id", "lease_epoch"].some((k) => d[k] !== session.descriptor[k])
        )
            throw new Error("Attempt channel is retired");
        this.lease(session);
        return session;
    }
    private retire(session: Session): void {
        session.valid = false;
        if (session.watchdog) clearTimeout(session.watchdog);
        session.watchdog = null;
        if (this.active === session) this.active = null;
    }
    // Containment does not require credentials or control-plane availability.
    async contain(): Promise<void> {
        this.generation++;
        this.probeAbort?.abort();
        await this.probing?.catch(() => {});
        this.reconciled = false;
        const active = this.active;
        if (active) {
            this.retire(active);
            if (!(await this.supervisor.stop({attempt_id: active.descriptor.attempt_id})).confirmed)
                throw new Error("Cannot confirm process stop");
        }
        for (const process of await this.supervisor.inspect())
            if (
                process.attempt_id !== active?.descriptor.attempt_id &&
                !(await this.supervisor.stop(process)).confirmed
            )
                throw new Error("Cannot confirm process stop");
    }
    private newEvent(d: Data, type: string, payload: Data): Data {
        let sequence = 0;
        for (const entry of this.journal.list())
            for (const event of entry.request.events ??
                (entry.request.event ? [entry.request.event] : []))
                if (event.attempt_id === d.attempt_id && event.lease_epoch === d.lease_epoch)
                    sequence = Math.max(sequence, event.sequence);
        return parse("runner_event", {
            schema_version: 1,
            job_id: d.job_id,
            attempt_id: d.attempt_id,
            lease_epoch: d.lease_epoch,
            event_id: randomUUID(),
            sequence: sequence + 1,
            type,
            occurred_at: new Date().toISOString(),
            payload,
        });
    }
    private async stopped(d: Data, cursor?: number): Promise<void> {
        this.assertScope();
        const base = `stop:${d.attempt_id}:${d.lease_epoch}`;
        let id = base,
            entry = this.journal.get(id);
        if (
            entry &&
            entry.state !== "done" &&
            cursor !== undefined &&
            entry.request.event.sequence !== cursor + 1
        ) {
            id = `${base}:${cursor + 1}`;
            this.journal.complete(base, {superseded_by: id, server_event_cursor: cursor});
            entry = this.journal.get(id);
        }
        if (!entry) {
            const event = this.newEvent(d, "attempt.stopped", {
                process_state: "stopped",
                adapter_session_ref: null,
                stop_confirmed: true,
                summary: "",
            });
            if (cursor !== undefined) event.sequence = cursor + 1;
            entry = this.journal.prepare("stop", id, "/runner/stop-evidence", {
                schema_version: 1,
                event,
            });
        }
        await this.transport.mutate("stop", id, "/runner/stop-evidence", entry.request);
        this.assertScope();
    }
    private runtimeTerminated(session: Session): void {
        if (!session.valid || this.active !== session) return;
        this.retire(session);
        this.generation++;
        // The RuntimeSupervisor calls this from its active task. Defer the control-plane
        // report so it cannot await RuntimeSupervisor.stop through the same task.
        const report = new Promise<void>((resolve) => setImmediate(resolve)).then(() =>
            this.stopped(session.descriptor),
        );
        this.stopping = report;
        void report
            .catch(() => {
                this.stopFailure = new Error("Runtime termination requires owner recovery");
                this.reconciled = false;
            })
            .finally(() => {
                if (this.stopping === report) this.stopping = null;
            });
    }
    async recover(): Promise<void> {
        await this.contain();
        this.assertScope();
        for (const entry of this.journal.list("claim"))
            if (entry.state !== "done") {
                await this.transport.send(entry);
                this.assertScope();
            }
        const response = await this.transport.request("/runner/leases");
        this.assertScope();
        for (const item of response.leases)
            await this.stopped(
                validateDescriptor(item.descriptor, this.runnerId),
                item.event_cursor,
            );
        const activeIds = new Set(response.leases.map((item: Data) => item.descriptor.attempt_id));
        for (const entry of this.journal.list("stop"))
            if (entry.state !== "done" && !activeIds.has(entry.request.event.attempt_id))
                await this.transport.send(entry);
        this.assertScope();
        this.reconciled = true;
    }
    claim(): Promise<void> {
        if (this.claiming) return this.claiming;
        const result = this.claimOnce();
        this.claiming = result;
        void result
            .finally(() => {
                if (this.claiming === result) this.claiming = null;
            })
            .catch(() => {});
        return result;
    }
    private async claimOnce(): Promise<void> {
        this.assertScope();
        if (this.stopFailure) throw this.stopFailure;
        if (!this.reconciled) throw new Error("Must reconcile before claims");
        if (this.active || this.probing || this.stopping || !this.supervisor.canExecute()) return;
        const generation = this.generation;
        const pending = this.journal.list("claim").find((e) => e.state !== "done"),
            id = pending?.id ?? `claim:${randomUUID()}`;
        const response = await this.transport.mutate(
            "claim",
            id,
            "/runner/claims",
            pending?.request ?? {
                schema_version: 1,
                claim_key: id.slice(6),
                capacity: 1,
                runner_version: "0.1.0",
            },
        );
        this.assertScope();
        if (!response.attempt) return;
        const d = validateDescriptor(response.attempt, this.runnerId);
        this.journal.prepare("attempt", `attempt:${d.attempt_id}`, "local", {
            descriptor: d,
            job_version: response.job_version,
        });
        if (generation !== this.generation) {
            await this.stopped(d);
            return;
        }
        if (Date.parse(d.lease_expires_at) <= Date.now()) throw new Error("Claim lease expired");
        this.registry.assertRuntime(d);
        const session: Session = {
            descriptor: structuredClone(d),
            valid: true,
            version: response.job_version,
            expiry: Date.parse(d.lease_expires_at),
            queue: Promise.resolve(),
            watchdog: null,
        };
        this.active = session;
        this.armWatchdog(session);
        try {
            await this.supervisor.start(structuredClone(d), {
                event: (type, payload) => this.enqueue(session, type, payload),
                pollInputs: () => this.collectInputs(session),
                inputApplied: (input, receipt) => this.finishInput(session, input, receipt),
                operations: new OperationBoundary(
                    this.journal,
                    this.transport,
                    () => this.lease(session),
                    (action) => this.serial(session, action),
                ),
                lease: () => this.lease(session),
                request: (route, extra = {}) =>
                    this.serial(session, async () => {
                        const response = await this.transport.request(
                            route,
                            route === "/runner/controls"
                                ? undefined
                                : {...extra, ...this.lease(session)},
                        );
                        this.lease(session);
                        return response;
                    }),
                upload: (payload, bytes) =>
                    this.serial(session, async () => {
                        const response = await this.transport.binary(
                            "/runner/artifacts",
                            {...payload, ...this.lease(session)},
                            bytes,
                        );
                        this.lease(session);
                        return response as Data;
                    }),
                download: (referenceId) =>
                    this.serial(session, async () => {
                        const response = await this.transport.binary("/runner/context-file", {
                            ...this.lease(session),
                            reference_id: referenceId,
                        });
                        this.lease(session);
                        return response as Buffer;
                    }),
                runtimeTerminated: () => this.runtimeTerminated(session),
            });
            this.lease(session);
        } catch (error) {
            if (session.valid) await this.stopSession(session);
            throw error;
        }
    }
    private armWatchdog(session: Session): void {
        if (session.watchdog) clearTimeout(session.watchdog);
        session.watchdog = setTimeout(
            () => {
                void this.stopSession(session).catch(() => {
                    this.stopFailure = new Error("Lease shutdown requires owner recovery");
                    this.reconciled = false;
                });
            },
            Math.max(0, session.expiry - Date.now()),
        );
        session.watchdog.unref();
    }
    private async refreshVersion(session: Session): Promise<void> {
        const response = await this.transport.request("/runner/controls");
        const d = session.descriptor;
        const control = response.controls.find(
            (c: Data) => c.attempt_id === d.attempt_id && c.lease_epoch === d.lease_epoch,
        );
        if (!control || control.control !== "continue")
            throw new Error("Attempt authority revoked");
        this.lease(session);
        session.version = Math.max(session.version, control.job_version);
    }
    private serial<T>(session: Session, action: () => Promise<T>): Promise<T> {
        const result = session.queue.then(async () => {
            this.lease(session);
            await this.replayEvents(session);
            await this.refreshVersion(session);
            const value = await action();
            this.lease(session);
            return value;
        });
        session.queue = result.then(
            () => {},
            () => {},
        );
        return result;
    }
    private enqueue(session: Session, type: string, payload: Data): Promise<void> {
        const snapshot = structuredClone(payload);
        const result = session.queue.then(async () => {
            this.lease(session);
            await this.replayEvents(session);
            await this.refreshVersion(session);
            const lease = this.lease(session),
                event = this.newEvent(session.descriptor, type, snapshot);
            const response = await this.transport.mutate(
                "event",
                event.event_id,
                "/runner/events",
                {schema_version: 1, job_version: lease.job_version, events: [event]},
            );
            this.lease(session);
            for (const receipt of response.receipts)
                session.version = Math.max(session.version, receipt.job_version);
        });
        // The caller keeps its failure. Later work first reconciles the durable pending event.
        session.queue = result.catch(() => {});
        return result;
    }
    event(type: string, payload: Data): Promise<void> {
        if (!this.active) return Promise.reject(new Error("Attempt channel is retired"));
        return this.enqueue(this.active, type, payload);
    }
    private async replayEvents(session: Session): Promise<void> {
        this.lease(session);
        for (const entry of this.journal.list("event"))
            if (
                entry.state !== "done" &&
                entry.request.events[0].attempt_id === session.descriptor.attempt_id &&
                entry.request.events[0].lease_epoch === session.descriptor.lease_epoch
            ) {
                this.lease(session);
                const response = await this.transport.send(entry);
                this.lease(session);
                for (const receipt of response.receipts)
                    session.version = Math.max(session.version, receipt.job_version);
            }
    }
    private inputsFor(attemptId: string, inputId: string) {
        return this.journal
            .list("input")
            .filter((e) => e.request.attempt_id === attemptId && e.request.input.id === inputId);
    }
    async applyInput(d: Data, input: Data): Promise<void> {
        const key = `${d.attempt_id}:${input.id}`;
        const prior = this.inputFlights.get(key);
        if (prior) return prior;
        const flight = this.deliverInput(d, input);
        this.inputFlights.set(key, flight);
        try {
            await flight;
        } finally {
            this.inputFlights.delete(key);
        }
    }
    private async finishInput(
        session: Session,
        input: Data,
        receipt: {outcome: "applied" | "not_applied"; receipt_id: string},
    ): Promise<void> {
        const id = `input:${session.descriptor.attempt_id}:${input.id}`;
        this.journal.complete(id, receipt);
        this.lease(session);
        if (receipt.outcome !== "applied") return;
        const event = this.journal
            .list("event")
            .find((entry) =>
                entry.request.events?.some(
                    (e: Data) =>
                        e.attempt_id === session.descriptor.attempt_id &&
                        e.type === "input.applied" &&
                        e.payload.input_id === input.id,
                ),
            );
        if (event) {
            if (event.state !== "done") await this.serial(session, async () => {});
            return;
        }
        await this.enqueue(session, "input.applied", {
            input_id: input.id,
            input_sequence: input.sequence,
            delivery_state: "applied",
        });
    }
    private async deliverInput(d: Data, input: Data): Promise<void> {
        const session = this.sessionFor(d),
            lease = this.lease(session);
        parse("input", input);
        const previous = this.inputsFor(d.attempt_id, input.id).at(-1);
        if (previous?.state === "done" && previous.response?.outcome === "applied") {
            await this.finishInput(
                session,
                input,
                previous.response as {outcome: "applied"; receipt_id: string},
            );
            return;
        }
        if (previous && previous.state !== "done")
            throw new Error("Input delivery is uncertain; reconcile its receipt");
        if (input.delivery_state === "delivery_uncertain")
            throw new Error("Input delivery is uncertain; reconcile its receipt");
        if (previous) {
            await this.stopSession(session);
            throw new Error("Input requires owner recovery and a fresh attempt");
        }
        if (input.delivery_state !== "delivered")
            throw new Error("Input has no delivery authority");
        const id = `input:${d.attempt_id}:${input.id}`;
        this.journal.prepare("input", id, "local", {
            attempt_id: d.attempt_id,
            lease,
            input: structuredClone(input),
        });
        this.journal.uncertain(id);
        const receipt = await this.supervisor.applyInput(
            structuredClone(session.descriptor),
            structuredClone(input),
        );
        await this.finishInput(session, input, receipt);
    }
    async reconcileInput(
        attemptId: string,
        input: Data,
        receipt: {outcome: "applied" | "not_applied"; receipt_id: string},
    ): Promise<void> {
        this.assertScope();
        parse("input", input);
        const priorResolution = this.journal
            .list("input_resolution")
            .find((e) => e.request.receipt.receipt_id === receipt.receipt_id);
        const entry = priorResolution
            ? this.journal.get(priorResolution.request.input_entry_id)
            : this.inputsFor(attemptId, input.id).at(-1);
        if (
            !entry ||
            entry.request.attempt_id !== attemptId ||
            entry.request.input.id !== input.id ||
            entry.request.input.sequence !== input.sequence
        )
            throw new Error("No durable input delivery identity");
        const attempt = this.journal.get(`attempt:${attemptId}`);
        const lease =
            entry.request.lease ??
            (attempt
                ? {
                      schema_version: 1,
                      job_id: attempt.request.descriptor.job_id,
                      attempt_id: attemptId,
                      lease_epoch: attempt.request.descriptor.lease_epoch,
                      job_version: attempt.request.job_version,
                  }
                : null);
        if (!lease || lease.attempt_id !== attemptId)
            throw new Error("No original attempt receipt authority");
        if (
            entry.state === "done" &&
            (entry.response?.outcome !== receipt.outcome ||
                entry.response?.receipt_id !== receipt.receipt_id)
        )
            throw new Error("Input receipt conflicts with local effect evidence");
        const localResolution = this.journal.prepare(
            "input_resolution",
            `${entry.id}:resolution`,
            "local",
            {input_entry_id: entry.id, receipt},
        );
        let version = lease.job_version;
        for (const stopped of this.journal.list("stop"))
            if (
                stopped.state === "done" &&
                stopped.request.event.attempt_id === attemptId &&
                stopped.response?.receipt?.job_version
            )
                version = Math.max(version, stopped.response.receipt.job_version);
        const resolutionId = `input_receipt:${receipt.receipt_id}`;
        const response = await this.transport.mutate(
            "input_receipt",
            resolutionId,
            "/runner/inputs/reconcile",
            this.journal.get(resolutionId)?.request ?? {
                ...lease,
                job_version: version,
                input_id: input.id,
                input_sequence: input.sequence,
                ...receipt,
            },
        );
        this.assertScope();
        if (
            response.input.id !== input.id ||
            response.input.sequence !== input.sequence ||
            response.input.delivery_state !==
                (receipt.outcome === "applied" ? "applied" : "pending")
        )
            throw new Error("Invalid input reconciliation response");
        this.journal.complete(localResolution.id, {input: response.input});
        if (entry.state !== "done")
            this.journal.complete(entry.id, {...receipt, server_confirmed: true});
        else if (entry.response?.outcome !== receipt.outcome)
            throw new Error("Input receipt conflicts with local effect evidence");
    }
    stopActive(): Promise<void> {
        this.generation++;
        if (this.stopping) return this.stopping;
        if (!this.active) return Promise.resolve();
        return this.stopSession(this.active);
    }
    private stopSession(session: Session): Promise<void> {
        if (!session.valid) return this.stopping ?? Promise.resolve();
        this.retire(session);
        this.generation++;
        const result = (async () => {
            if (
                !(await this.supervisor.stop({attempt_id: session.descriptor.attempt_id})).confirmed
            ) {
                this.stopFailure = new Error("Cannot confirm process stop");
                this.reconciled = false;
                throw this.stopFailure;
            }
            try {
                await this.stopped(session.descriptor);
            } catch (error) {
                this.reconciled = false;
                throw error;
            }
        })();
        this.stopping = result;
        void result
            .finally(() => {
                if (this.stopping === result) this.stopping = null;
            })
            .catch(() => {});
        return result;
    }
    async tick(): Promise<void> {
        if (!this.reconciled) await this.recover();
        const session = this.active;
        if (!session) {
            this.assertScope();
            await this.transport.request("/runner/heartbeat", {schema_version: 1, leases: []});
            this.assertScope();
            await this.claim();
            return;
        }
        if (Date.now() >= session.expiry) {
            await this.stopSession(session);
            return;
        }
        try {
            // Use the event queue so replay cannot race an adapter event.
            const replay = session.queue.then(() => this.replayEvents(session));
            session.queue = replay.catch(() => {});
            await replay;
            const controls = await this.serial(session, () =>
                this.transport.request("/runner/controls"),
            );
            this.lease(session);
            const d = session.descriptor,
                control = controls.controls.find(
                    (c: Data) => c.attempt_id === d.attempt_id && c.lease_epoch === d.lease_epoch,
                );
            if (!control || control.control === "stop") {
                await this.stopSession(session);
                return;
            }
            session.version = Math.max(session.version, control.job_version);
            const response = await this.serial(session, () =>
                this.transport.request("/runner/heartbeat", {
                    schema_version: 1,
                    leases: [
                        {job_id: d.job_id, attempt_id: d.attempt_id, lease_epoch: d.lease_epoch},
                    ],
                }),
            );
            this.lease(session);
            const heartbeat = response.leases.find(
                (c: Data) => c.attempt_id === d.attempt_id && c.lease_epoch === d.lease_epoch,
            );
            if (!heartbeat || heartbeat.control === "stop") {
                await this.stopSession(session);
                return;
            }
            session.version = Math.max(session.version, heartbeat.job_version);
            session.expiry = Date.parse(heartbeat.lease_expires_at);
            this.armWatchdog(session);
            await this.collectInputs(session);
        } catch (error) {
            if (session.valid) {
                await this.stopSession(session);
                this.reconciled = false;
            }
            throw error;
        }
    }
    private async collectInputs(session: Session): Promise<void> {
        const d = session.descriptor;
        const inputs = await this.serial(session, () =>
            this.transport.request("/runner/inputs", this.lease(session)),
        );
        this.lease(session);
        for (const input of inputs.inputs) {
            this.lease(session);
            void this.applyInput(d, input).catch(async () => {
                if (session.valid) {
                    this.reconciled = false;
                    await this.stopSession(session).catch(() => {
                        this.stopFailure = new Error(
                            "Input recovery requires confirmed containment",
                        );
                    });
                }
            });
        }
    }
    async pollSetups(): Promise<void> {
        if (this.probeError) throw this.probeError;
        if (this.active || this.probing || !this.reconciled) return;
        const response = await this.transport.request("/runner/setups");
        const next = response.setups[0];
        if (!next) return;
        this.probing = this.setup(next.setup_id)
            .catch((error) => {
                this.probeError = error;
            })
            .finally(() => {
                this.probing = null;
            });
    }
    async setup(setupId: string): Promise<void> {
        const generation = this.generation;
        this.assertScope();
        if (this.active) throw new Error("A job already owns execution capacity");
        if (!this.reconciled) throw new Error("Must reconcile before setup");
        const previous = this.journal
                .list("setup_claim")
                .find((e) => e.request.setup_id === setupId),
            id = previous?.id ?? `setup:${randomUUID()}`;
        const claimed = await this.transport.mutate(
            "setup_claim",
            id,
            "/runner/setups/claim",
            previous?.request ?? {schema_version: 1, setup_id: setupId, claim_key: id.slice(6)},
        );
        this.assertScope();
        if (generation !== this.generation)
            throw new Error("Setup claim belongs to a retired supervisor");
        const d = validateDescriptor(claimed.descriptor, this.runnerId, true);
        this.registry.assertRuntime(d);
        if (
            Date.parse(claimed.lease_expires_at) <= Date.now() ||
            Date.parse(d.grant.expires_at) <= Date.now()
        )
            throw new Error("Setup lease expired");
        const abort = new AbortController();
        this.probeAbort = abort;
        const deadline = Math.min(
            Date.parse(claimed.lease_expires_at),
            Date.parse(d.grant.expires_at),
        );
        const binding: Data = {
            schema_version: 1,
            setup_id: setupId,
            claim_key: claimed.claim_key,
            lease_epoch: claimed.lease_epoch,
            descriptor_digest: d.descriptor_digest,
            configuration_digest: d.configuration_digest,
        };
        let checked = 0;
        const assertCurrent = () => {
            this.assertScope();
            if (generation !== this.generation)
                throw new Error("Probe supervisor generation changed");
            if (abort.signal.aborted || Date.now() >= deadline || Date.now() - checked > 3000)
                throw new Error("Probe authority is not current");
        };
        // The server serializes agent authority with one advisory lock and
        // answers a busy request with a retry. A probe keeps its authority
        // through that answer, so the check waits and asks again.
        const authorityRequest = async (route: string, payload: Data): Promise<Data> => {
            for (let attempt = 0; ; attempt += 1) {
                try {
                    return await this.transport.request(route, payload);
                } catch (error) {
                    if (
                        attempt >= PROBE_CONTENTION_RETRIES ||
                        !(error instanceof TransportError) ||
                        error.kind !== "contention"
                    )
                        throw error;
                    await sleep(Math.max(1, error.retryAfter) * 1000);
                    if (abort.signal.aborted || Date.now() >= deadline)
                        throw new Error("Probe authority expired");
                }
            }
        };
        const validate = async () => {
            if (abort.signal.aborted || Date.now() >= deadline)
                throw new Error("Probe authority expired");
            const response = await authorityRequest("/runner/setup-authority", binding);
            this.assertScope();
            for (const key of [
                "setup_id",
                "claim_key",
                "lease_epoch",
                "descriptor_digest",
                "configuration_digest",
            ])
                if (response[key] !== binding[key]) throw new Error("Probe authority changed");
            if (response.grant_id !== d.grant.id || Date.parse(response.expires_at) < deadline)
                throw new Error("Probe grant changed");
            checked = Date.now();
            assertCurrent();
        };
        const authority: ProbeChannel = {
            setup_operation_id: d.setup_operation_id,
            signal: abort.signal,
            deadline,
            assertCurrent,
            validate,
            request: async (route, extra = {}) => {
                await validate();
                const response = await authorityRequest(route, {...extra, ...binding});
                assertCurrent();
                return response;
            },
        };
        await validate();
        let checking = false;
        const poll = setInterval(() => {
            if (checking) return;
            checking = true;
            void validate()
                .catch(() => abort.abort())
                .finally(() => {
                    checking = false;
                });
        }, 1000);
        const timer = setTimeout(() => abort.abort(), Math.max(1, deadline - Date.now()));
        try {
            const resultId = `setup_result:${setupId}:${claimed.lease_epoch}`,
                previousResult = this.journal.get(resultId);
            const result = previousResult?.request ?? {
                schema_version: 1,
                setup_id: setupId,
                claim_key: claimed.claim_key,
                lease_epoch: claimed.lease_epoch,
                descriptor_digest: d.descriptor_digest,
                configuration_digest: d.configuration_digest,
                ...(await this.supervisor.probe(d, authority)),
            };
            this.assertScope();
            await validate();
            await this.transport.mutate("setup_result", resultId, "/runner/setups/result", result);
        } finally {
            clearInterval(poll);
            clearTimeout(timer);
            abort.abort();
            if (this.probeAbort === abort) this.probeAbort = null;
        }
    }
}
export async function runService(
    coordinator: Coordinator,
    connection: {access(): Promise<void>},
    transport: Pick<Transport, "poll">,
    signal: AbortSignal,
    options: {setups?: boolean} = {},
): Promise<void> {
    await coordinator.contain();
    try {
        await transport.poll(async () => {
            try {
                await connection.access();
                await coordinator.tick();
                if (options.setups) await coordinator.pollSetups();
            } catch (error) {
                // Stop local effects before polling can enter backoff, even without credentials.
                await coordinator.contain();
                throw error;
            }
        }, signal);
    } finally {
        try {
            await coordinator.stopActive();
        } finally {
            await coordinator.contain();
        }
    }
}
