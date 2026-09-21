import type {JournalLog} from "./journal.js";
import {Transport} from "./transport.js";
import {canonical, parse, type Data} from "./protocol.js";

// Trusted host recovery has receipt authority only. Never pass this interface to a runtime channel.
export class OperationRecovery {
    constructor(
        private journal: JournalLog,
        private transport: Transport,
        private identity: Data,
        private assertScope: () => void,
    ) {}
    async list(): Promise<Data> {
        this.assertScope();
        const response = await this.transport.request("/runner/operations", this.identity);
        this.assertScope();
        return response;
    }
    remoteReceipt(receipt: Data): Promise<Data> {
        return this.submit("remote", parse("remote_receipt", receipt));
    }
    localReceipt(receipt: Data): Promise<Data> {
        return this.submit("local", parse("local_operation_receipt", receipt));
    }
    private async submit(kind: "remote" | "local", receipt: Data): Promise<Data> {
        this.assertScope();
        const operationId = receipt.operation_id;
        const consume = this.journal.get(`consume:${operationId}`);
        if (
            !consume ||
            consume.state === "prepared" ||
            ["job_id", "attempt_id", "lease_epoch"].some(
                (key) => consume.request[key] !== this.identity[key],
            )
        )
            throw new Error("No original durable operation identity");
        if (kind === "local" && receipt.argument_digest !== consume.request.operation_hash)
            throw new Error("Operation receipt hash conflict");
        const id = `${kind}:${operationId}`,
            previous = this.journal.get(id);
        if (previous && canonical(previous.request.receipt) !== canonical(receipt))
            throw new Error("Operation receipt conflict");
        const resolution = this.journal.prepare(
            "operation_resolution",
            `operation_resolution:${operationId}`,
            "local",
            {kind, receipt},
        );
        const response = await this.transport.mutate(
            `${kind}_receipt`,
            id,
            kind === "remote"
                ? "/runner/operations/reconcile"
                : "/runner/operations/reconcile-local",
            previous?.request ?? {
                ...this.identity,
                ...(kind === "remote" ? {operation_id: operationId} : {}),
                receipt,
            },
        );
        this.assertScope();
        const expected =
            kind === "remote"
                ? "succeeded"
                : receipt.outcome === "no_effect"
                  ? "cancelled"
                  : receipt.outcome;
        if (
            response.operation.operation_id !== operationId ||
            response.operation.operation_hash !== consume.request.operation_hash ||
            response.operation.status !== expected
        )
            throw new Error("Invalid operation recovery response");
        this.journal.complete(resolution.id, response);
        const effect = this.journal.get(`effect:${operationId}`);
        if (effect && effect.state !== "done")
            this.journal.complete(effect.id, {receipt, reconciled: true});
        return response;
    }
}
