import {readFileSync} from "node:fs";
import {randomUUID, createHash} from "node:crypto";
import {PrivateStore} from "../dist/config.js";
import {Journal} from "../dist/journal.js";
import {Transport} from "../dist/transport.js";
import {Coordinator} from "../dist/supervisor.js";
const config = JSON.parse(readFileSync(process.argv[2], "utf8"));
const store = new PrivateStore(config.state);
store.write("connection.json", {
    state: "connected",
    runner_id: config.runner_id,
    origin: config.origin,
});
const journal = new Journal(store.root),
    transport = new Transport(config.origin, journal, () => config.token);
let channel, descriptor;
let stops = 0;
const supervisor = {
    inspect: async () => [],
    stop: async () => {
        stops++;
        return {confirmed: true};
    },
    canExecute: () => true,
    start: async (d, c) => {
        descriptor = d;
        channel = c;
    },
    applyInput: async () => ({outcome: "applied", receipt_id: randomUUID()}),
};
const coordinator = new Coordinator(journal, transport, supervisor, config.runner_id, {
    assertRuntime: () => {},
});
try {
    await coordinator.recover();
    await coordinator.claim();
    const operationId = randomUUID(),
        content = Buffer.from("Synthetic retained artifact");
    const [_, proposal, upload] = await Promise.all([
        channel.event("attempt.started", {
            process_state: "active",
            adapter_session_ref: null,
            stop_confirmed: false,
            summary: "",
        }),
        channel.operations.propose(channel.lease(), operationId, {
            action: "context.read",
            context_ids: descriptor.context_refs.map((r) => r.id),
        }),
        channel.upload(
            {
                kind: "summary",
                filename: "summary.txt",
                media_type: "text/plain",
                checksum: createHash("sha256").update(content).digest("hex"),
            },
            content,
        ),
        coordinator.tick(),
    ]);
    const consumed = await channel.operations.consume(channel.lease(), proposal);
    channel.operations.beginEffect(operationId);
    await channel.event("tool.started", {
        operation_id: operationId,
        tool_class: "context.read",
        argument_digest: consumed.operation_hash,
        status: "started",
        artifact_id: null,
        exit_code: null,
        summary: "",
    });
    const context = await channel.request("/runner/context", {
        reference_ids: descriptor.context_refs.map((r) => r.id),
    });
    if (!context.references.length) throw Error("Expected selected context");
    channel.operations.finishEffect(operationId, {artifact_id: upload.artifact_id});
    await Promise.all([
        channel.event("tool.finished", {
            operation_id: operationId,
            tool_class: "context.read",
            argument_digest: consumed.operation_hash,
            status: "succeeded",
            artifact_id: upload.artifact_id,
            exit_code: 0,
            summary: "",
        }),
        channel.request("/runner/checkpoints", {
            checkpoint: {
                id: randomUUID(),
                source_attempt_id: descriptor.attempt_id,
                base_commit: null,
                tree_hash: null,
                summary: "Synthetic checkpoint",
                context_ref_ids: descriptor.context_refs.map((r) => r.id),
                artifact_ids: [upload.artifact_id],
                remaining_work: [],
                next_step: "Complete the response",
                adapter_session_ref: null,
                input_cursor: 1,
            },
        }),
        coordinator.tick(),
    ]);
    let rejected = false;
    try {
        await transport.request("/runner/authority", {...channel.lease(), job_version: 1});
    } catch {
        rejected = true;
    }
    if (!rejected) throw Error("Stale CAS accepted");
    await channel.event("result.prepared", {
        summary: "Synthetic retained artifact",
        artifact_ids: [upload.artifact_id],
        tree_hash: null,
    });
    const version = channel.lease().job_version;
    try {
        await coordinator.tick();
    } catch (error) {
        if (!String(error.message).includes("authority revoked")) throw error;
    }
    if (stops !== 1) throw Error("Prepared result did not trigger confirmed containment");

    console.log(
        JSON.stringify({
            passed: true,
            artifact_id: upload.artifact_id,
            operation_hash: consumed.operation_hash,
            version,
            completion_stops: stops,
            context_count: context.references.length,
        }),
    );
} finally {
    await coordinator.stopActive();
    journal.close();
}
