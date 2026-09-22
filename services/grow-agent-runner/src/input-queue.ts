import {randomUUID} from "node:crypto";
import type {Data} from "./protocol.js";
import type {JournalLog} from "./journal.js";
import type {Runtime} from "./runtime.js";
export interface InputReceipt {
    outcome: "applied" | "not_applied";
    receipt_id: string;
}
interface Pending {
    input: Data;
    resolve: (receipt: InputReceipt) => void;
    reject: (error: unknown) => void;
    promise: Promise<InputReceipt>;
}
export class InputQueue {
    private pending = new Map<string, Pending>();
    private wake?: () => void;
    constructor(
        private log: JournalLog,
        private d: Data,
        private signal: AbortSignal,
        public cursor: number,
    ) {
        signal.addEventListener(
            "abort",
            () => {
                for (const p of this.pending.values()) {
                    const id = `runtime-input:${this.d.attempt_id}:${p.input.id}`;
                    if (this.log.get(id)?.state === "prepared") {
                        const receipt: InputReceipt = {
                            outcome: "not_applied",
                            receipt_id: randomUUID(),
                        };
                        this.log.complete(id, {
                            receipt,
                            answer: "",
                            input_sequence: p.input.sequence,
                        });
                        p.resolve(receipt);
                    } else p.reject(new Error("Input cancelled; inspect its durable receipt"));
                }
                this.wake?.();
            },
            {once: true},
        );
    }
    submit(input: Data): Promise<InputReceipt> {
        if (this.signal.aborted) return Promise.reject(new Error("Input attempt is stopped"));
        const id = `runtime-input:${this.d.attempt_id}:${input.id}`;
        const entry = this.log.prepare("runtime-input", id, "local", {
            job_id: this.d.job_id,
            attempt_id: this.d.attempt_id,
            lease_epoch: this.d.lease_epoch,
            input,
        });
        const existing = this.pending.get(input.id);
        if (existing) return existing.promise;
        if (entry.state === "done" && entry.response?.receipt.outcome === "not_applied")
            return Promise.resolve(entry.response.receipt);
        if (entry.state === "uncertain")
            return Promise.reject(new Error("Runtime input outcome is uncertain; do not replay"));
        if (input.sequence <= this.cursor) {
            if (entry.state !== "done")
                return Promise.reject(new Error("Input cursor has no matching receipt"));
            return Promise.resolve(entry.response!.receipt);
        }
        let resolve!: Pending["resolve"], reject!: Pending["reject"];
        const promise = new Promise<InputReceipt>((yes, no) => {
            resolve = yes;
            reject = no;
        });
        this.pending.set(input.id, {input: structuredClone(input), resolve, reject, promise});
        this.wake?.();
        return promise;
    }
    async next(
        runtime: Runtime,
        acknowledge: (input: Data, receipt: InputReceipt) => Promise<void>,
        sanitize: (text: string) => string,
    ): Promise<string | null> {
        if (this.signal.aborted) throw new Error("Input attempt is stopped");
        const p = [...this.pending.values()].sort((a, b) => a.input.sequence - b.input.sequence)[0];
        if (!p) return null;
        const id = `runtime-input:${this.d.attempt_id}:${p.input.id}`;
        try {
            let entry = this.log.get(id)!;
            if (entry.state === "prepared") {
                this.log.uncertain(id);
                const answer = sanitize(await runtime.sendTurn(p.input.text, p.input.id));
                const receipt: InputReceipt = {outcome: "applied", receipt_id: randomUUID()};
                this.log.complete(id, {receipt, answer, input_sequence: p.input.sequence});
                entry = this.log.get(id)!;
            }
            if (entry.state !== "done")
                throw new Error("Runtime input outcome is uncertain; do not replay");
            const receipt = entry.response!.receipt as InputReceipt;
            await acknowledge(p.input, receipt);
            if (this.signal.aborted) throw new Error("Input acknowledgement needs recovery");
            this.cursor = p.input.sequence;
            this.pending.delete(p.input.id);
            p.resolve(receipt);
            return entry.response!.answer;
        } catch (error) {
            p.reject(error);
            throw error;
        }
    }
    hasPending(): boolean {
        return this.pending.size > 0;
    }
    async wait(): Promise<void> {
        if (this.pending.size || this.signal.aborted) return;
        await new Promise<void>((resolve) => {
            this.wake = resolve;
        });
        this.wake = undefined;
    }
}
