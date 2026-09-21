import {randomUUID} from "node:crypto";
import {Journal} from "./journal.js";
import {Transport} from "./transport.js";
import type {OwnerRegistry} from "./owner.js";
import {parse, validateDescriptor, type Data} from "./protocol.js";

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
    probe(descriptor: Data): Promise<{
        state: "ready" | "needs_action" | "failed";
        capabilities: Data;
        requirements: Data[];
    }>;
}
export interface AttemptChannel {
    event(type: string, payload: Data): Promise<void>;
    operations: OperationBoundary;
    lease: () => Data;
}
export class OperationBoundary {
    constructor(
        private journal: Journal,
        private transport: Transport,
        private currentLease: () => Data,
    ) {}
    async propose(lease: Data, id: string, args: Data, extras: Data = {}): Promise<Data> {
        for (const key of Object.keys(extras))
            if (!["tree_hash", "diff_artifact_id"].includes(key))
                throw new Error("Invalid operation metadata");
        parse("operation_arguments", args);
        return (
            await this.transport.mutate(
                "proposal",
                `proposal:${id}`,
                "/runner/operations/propose",
                {...lease, ...extras, operation_id: id, arguments: args},
            )
        ).operation;
    }
    async consume(lease: Data, proposal: Data): Promise<Data> {
        this.currentLease();
        const id = proposal.operation_id;
        const result = await this.transport.mutate(
            "consume",
            `consume:${id}`,
            "/runner/operations/consume",
            {
                ...lease,
                operation_id: id,
                expected_version: proposal.version,
                operation_hash: proposal.operation_hash,
                ...(proposal.nonce ? {nonce: proposal.nonce} : {}),
            },
        );
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
        this.journal.complete(`effect:${operationId}`, receipt);
    }
    async reconcile(lease: Data): Promise<Data> {
        return this.transport.request("/runner/operations", lease);
    }
    async remoteReceipt(lease: Data, receipt: Data): Promise<Data> {
        parse("remote_receipt", receipt);
        return this.transport.mutate(
            "remote_receipt",
            `remote:${receipt.operation_id}`,
            "/runner/operations/reconcile",
            {...lease, operation_id: receipt.operation_id, receipt},
        );
    }
}
export class Coordinator {
    private eventTail: Promise<void> = Promise.resolve();
    private reconciled = false;
    private active: Data | null = null;
    private jobVersion = 0;
    private expiry = 0;
    private watchdog: NodeJS.Timeout | null = null;
    private stopFailure: Error | null = null;
    constructor(
        private journal: Journal,
        private transport: Transport,
        private supervisor: Supervisor,
        private runnerId: string,
        private registry: Pick<OwnerRegistry, "assertRuntime">,
    ) {}
    private lease(): Data {
        if (this.stopFailure) throw this.stopFailure;
        if (!this.active || Date.now() >= this.expiry)
            throw new Error("No current lease authority");
        return {
            schema_version: 1,
            job_id: this.active.job_id,
            attempt_id: this.active.attempt_id,
            lease_epoch: this.active.lease_epoch,
            job_version: this.jobVersion,
        };
    }
    private async stopped(d: Data, cursor?: number): Promise<void> {
        const base = `stop:${d.attempt_id}:${d.lease_epoch}`;
        let id = base;
        let entry = this.journal.get(id);
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
    }
    async recover(): Promise<void> {
        this.reconciled = false;
        // Stop host effects first, even when the control plane is unavailable or credentials were revoked.
        const processes = await this.supervisor.inspect();
        for (const process of processes)
            if (!(await this.supervisor.stop(process)).confirmed)
                throw new Error("Cannot confirm process stop");
        for (const entry of this.journal.list("claim"))
            if (entry.state !== "done") await this.transport.send(entry);
        const response = await this.transport.request("/runner/leases");
        for (const item of response.leases) {
            const d = validateDescriptor(item.descriptor, this.runnerId);
            await this.stopped(d, item.event_cursor);
        }
        const activeIds = new Set(response.leases.map((item: Data) => item.descriptor.attempt_id));
        for (const entry of this.journal.list("stop"))
            if (entry.state !== "done" && !activeIds.has(entry.request.event.attempt_id))
                await this.transport.send(entry);
        // Do not replay stale lease mutations after cleanup.
        this.active = null;
        this.reconciled = true;
    }
    async claim(): Promise<void> {
        if (this.stopFailure) throw this.stopFailure;
        if (!this.reconciled) throw new Error("Must reconcile before claims");
        if (this.active || !this.supervisor.canExecute()) return;
        const pending = this.journal.list("claim").find((e) => e.state !== "done");
        const id = pending?.id ?? `claim:${randomUUID()}`;
        const request = pending?.request ?? {
            schema_version: 1,
            claim_key: id.slice(6),
            capacity: 1,
            runner_version: "0.1.0",
        };
        const response = await this.transport.mutate("claim", id, "/runner/claims", request);
        if (!response.attempt) return;
        const d = validateDescriptor(response.attempt, this.runnerId);
        if (Date.parse(d.lease_expires_at) <= Date.now()) throw new Error("Claim lease expired");
        this.journal.prepare("attempt", `attempt:${d.attempt_id}`, "local", {
            descriptor: d,
            job_version: response.job_version,
        });
        this.registry.assertRuntime(d);
        this.active = d;
        this.expiry = Date.parse(d.lease_expires_at);
        this.jobVersion = response.job_version;
        this.armWatchdog();
        try {
            await this.supervisor.start(d, {
                event: (type, payload) => this.event(type, payload),
                operations: new OperationBoundary(this.journal, this.transport, () => this.lease()),
                lease: () => this.lease(),
            });
        } catch (e) {
            await this.stopActive();
            throw e;
        }
    }
    private newEvent(d: Data, type: string, payload: Data): Data {
        let sequence = 0;
        for (const entry of this.journal.list()) {
            const events =
                entry.request.events ?? (entry.request.event ? [entry.request.event] : []);
            for (const event of events)
                if (event.attempt_id === d.attempt_id)
                    sequence = Math.max(sequence, event.sequence);
        }
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
    event(type: string, payload: Data): Promise<void> {
        this.eventTail = this.eventTail.then(() => this.sendEvent(type, payload));
        return this.eventTail;
    }
    private async sendEvent(type: string, payload: Data): Promise<void> {
        const lease = this.lease(),
            event = this.newEvent(this.active!, type, payload);
        const response = await this.transport.mutate("event", event.event_id, "/runner/events", {
            schema_version: 1,
            job_version: lease.job_version,
            events: [event],
        });
        for (const receipt of response.receipts)
            this.jobVersion = Math.max(this.jobVersion, receipt.job_version);
    }
    async applyInput(d: Data, input: Data): Promise<void> {
        parse("input", input);
        const id = `input:${d.attempt_id}:${input.id}`;
        const entry = this.journal.prepare("input", id, "local", {attempt_id: d.attempt_id, input});
        if (entry.state === "done") return;
        if (entry.state === "uncertain" || input.delivery_state === "delivery_uncertain")
            throw new Error("Input delivery is uncertain; reconcile its receipt");
        this.journal.uncertain(id);
        const receipt = await this.supervisor.applyInput(d, input);
        this.journal.complete(id, receipt);
        if (receipt.outcome === "applied")
            await this.event("input.applied", {
                input_id: input.id,
                input_sequence: input.sequence,
                delivery_state: "applied",
            });
    }
    private armWatchdog(): void {
        if (this.watchdog) clearTimeout(this.watchdog);
        this.watchdog = setTimeout(
            () => {
                void this.stopActive().catch(() => {
                    this.stopFailure = new Error("Lease shutdown requires owner recovery");
                    this.reconciled = false;
                });
            },
            Math.max(0, this.expiry - Date.now()),
        );
        this.watchdog.unref();
    }
    async reconcileInput(
        input: Data,
        receipt: {outcome: "applied" | "not_applied"; receipt_id: string},
    ): Promise<void> {
        const lease = this.lease();
        await this.transport.mutate(
            "input_receipt",
            `input_receipt:${receipt.receipt_id}`,
            "/runner/inputs/reconcile",
            {...lease, input_id: input.id, input_sequence: input.sequence, ...receipt},
        );
    }
    async stopActive(): Promise<void> {
        if (this.watchdog) {
            clearTimeout(this.watchdog);
            this.watchdog = null;
        }
        if (!this.active) return;
        const d = this.active;
        if (!(await this.supervisor.stop({attempt_id: d.attempt_id})).confirmed)
            throw new Error("Cannot confirm process stop");
        this.active = null;
        await this.stopped(d);
    }
    async tick(): Promise<void> {
        if (!this.reconciled) await this.recover();
        if (!this.active) {
            await this.claim();
            return;
        }
        if (Date.now() >= this.expiry) {
            await this.stopActive();
            return;
        }
        try {
            for (const entry of this.journal.list("event"))
                if (
                    entry.state !== "done" &&
                    entry.request.events[0].attempt_id === this.active!.attempt_id
                ) {
                    const response = await this.transport.send(entry);
                    for (const receipt of response.receipts)
                        this.jobVersion = Math.max(this.jobVersion, receipt.job_version);
                }
            const controls = await this.transport.request("/runner/controls");
            const control = controls.controls.find(
                (c: Data) =>
                    c.attempt_id === this.active!.attempt_id &&
                    c.lease_epoch === this.active!.lease_epoch,
            );
            if (!control || control.control === "stop") {
                await this.stopActive();
                return;
            }
            this.jobVersion = control.job_version;
            const response = await this.transport.request("/runner/heartbeat", {
                schema_version: 1,
                leases: [
                    {
                        job_id: this.active.job_id,
                        attempt_id: this.active.attempt_id,
                        lease_epoch: this.active.lease_epoch,
                    },
                ],
            });
            const heartbeat = response.leases.find(
                (c: Data) =>
                    c.attempt_id === this.active!.attempt_id &&
                    c.lease_epoch === this.active!.lease_epoch,
            );
            if (!heartbeat || heartbeat.control === "stop") {
                await this.stopActive();
                return;
            }
            this.jobVersion = heartbeat.job_version;
            this.expiry = Date.parse(heartbeat.lease_expires_at);
            this.armWatchdog();
            const inputs = await this.transport.request("/runner/inputs", this.lease());
            for (const input of inputs.inputs) await this.applyInput(this.active, input);
        } catch (e) {
            await this.stopActive();
            this.reconciled = false;
            throw e;
        }
    }
    async setup(setupId: string): Promise<void> {
        if (!this.reconciled) throw new Error("Must reconcile before setup");
        const previous = this.journal
            .list("setup_claim")
            .find((e) => e.request.setup_id === setupId);
        const id = previous?.id ?? `setup:${randomUUID()}`;
        const claimed = await this.transport.mutate(
            "setup_claim",
            id,
            "/runner/setups/claim",
            previous?.request ?? {schema_version: 1, setup_id: setupId, claim_key: id.slice(6)},
        );
        const d = validateDescriptor(claimed.descriptor, this.runnerId, true);
        this.registry.assertRuntime(d);
        if (
            Date.parse(claimed.lease_expires_at) <= Date.now() ||
            Date.parse(d.grant.expires_at) <= Date.now()
        )
            throw new Error("Setup lease expired");
        const resultId = `setup_result:${setupId}:${claimed.lease_epoch}`;
        const previousResult = this.journal.get(resultId);
        const result = previousResult?.request ?? {
            schema_version: 1,
            setup_id: setupId,
            claim_key: claimed.claim_key,
            lease_epoch: claimed.lease_epoch,
            descriptor_digest: d.descriptor_digest,
            configuration_digest: d.configuration_digest,
            ...(await this.supervisor.probe(d)),
        };
        await this.transport.mutate("setup_result", resultId, "/runner/setups/result", result);
    }
}
