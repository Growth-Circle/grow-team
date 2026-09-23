import {createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import {z} from "zod";

// JSON schema is the structural authority. These records cross that boundary only after parsing.
export type Data = Record<string, any>;
const schemas = JSON.parse(
    readFileSync(new URL("../protocol/protocol-v1.schema.json", import.meta.url), "utf8"),
);
const validators = new Map(
    Object.entries(schemas).map(([name, schema]) => [name, z.fromJSONSchema(schema as any)]),
);
function requireThat(value: unknown, message: string): asserts value {
    if (!value) throw new Error(message);
}
export function canonical(value: unknown): string {
    if (value === null || typeof value !== "object") {
        if (typeof value === "number")
            requireThat(Number.isSafeInteger(value), "Unsafe protocol number");
        const result = JSON.stringify(value);
        requireThat(result !== undefined, "Invalid JSON value");
        return result;
    }
    if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
    return `{${Object.keys(value)
        .sort()
        .map((k) => `${JSON.stringify(k)}:${canonical((value as Data)[k])}`)
        .join(",")}}`;
}
export function digest(value: unknown): string {
    return createHash("sha256").update(canonical(value)).digest("hex");
}
const equal = (a: unknown, b: unknown) => canonical(a) === canonical(b);
export function effectiveConfiguration(d: Data): Data {
    const provider =
        d.provider === null
            ? null
            : Object.fromEntries(
                  [
                      "id",
                      "config_version",
                      "base_url",
                      "api_mode",
                      "model_id",
                      "allowed_models",
                      "credential_ref",
                      "context_window_tokens",
                      "max_output_tokens",
                      "data_scope",
                      "network",
                  ].map((k) => [k, d.provider[k]]),
              );
    const r = d.repository;
    const binding =
        "workspace_binding" in d
            ? d.workspace_binding
            : r
              ? {
                    canonical_origin: r.canonical_origin,
                    repository_id: r.id,
                    workspace_alias: r.workspace_alias,
                    policy_version: r.policy_version,
                    allowed_refs: r.allowed_refs,
                    checks_digest: digest(r.required_checks),
                }
              : null;
    return {
        schema_version: 1,
        runner_id: d.runner_id,
        profile_revision: d.profile_revision,
        adapter: d.adapter,
        provider,
        workspace_binding: binding,
        policy_version: d.policy.version,
        actions: [...new Set(d.policy.actions)].sort(),
        sandbox: d.policy.sandbox,
        network: d.policy.network,
        hard_cost_cap: d.policy.hard_cost_cap,
        budget: d.budget,
    };
}
function narrow(d: Data): void {
    const actual = effectiveConfiguration(d),
        tested = d.tested_configuration;
    for (const k of [
        "runner_id",
        "profile_revision",
        "adapter",
        "provider",
        "policy_version",
        "sandbox",
        "network",
    ])
        requireThat(equal(actual[k], tested[k]), `Untested ${k}`);
    requireThat(
        equal(actual.workspace_binding, tested.workspace_binding) ||
            (["answer", "manage"].includes(d.job_kind) && actual.workspace_binding === null),
        "Untested workspace",
    );
    requireThat(
        actual.actions.every((a: string) => tested.actions.includes(a)),
        "Widened actions",
    );
    requireThat(!tested.hard_cost_cap || actual.hard_cost_cap, "Removed cost cap");
    for (const [k, ceiling] of Object.entries(tested.budget))
        if (ceiling !== null)
            requireThat(
                actual.budget[k] !== null && actual.budget[k] <= (ceiling as number),
                "Widened budget",
            );
}
function semantics(v: any): void {
    if (!v || typeof v !== "object") return;
    if (Array.isArray(v)) {
        v.forEach(semantics);
        return;
    }
    Object.values(v).forEach(semantics);
    if ("code_ready" in v)
        requireThat(
            !v.code_ready ||
                (v.chat_ready && v.tool_calling === "passed" && v.sandbox === "passed"),
            "Missing coding evidence",
        );
    if ("allow_http_private" in v)
        requireThat(!v.allow_http_private || v.allow_private, "Private HTTP scope");
    if ("capability_report" in v && "base_url" in v) {
        const u = new URL(v.base_url);
        requireThat(
            u.hostname && !u.username && !u.password && !u.search && !u.hash,
            "Unsafe provider URL",
        );
        requireThat(
            v.allowed_models.includes(v.model_id) && v.max_output_tokens <= v.context_window_tokens,
            "Invalid model",
        );
        requireThat(
            v.config_version === v.capability_report.config_version,
            "Stale provider probe",
        );
    }
    if ("timeout_seconds" in v && "argv" in v)
        requireThat(
            !/^[\\/]/.test(v.cwd) &&
                !v.cwd.includes(":") &&
                !v.cwd.replaceAll("\\", "/").split("/").includes(".."),
            "Unsafe check path",
        );
    if ("base_ref" in v && "allowed_refs" in v)
        requireThat(v.allowed_refs.includes(v.base_ref), "Base ref denied");
    if ("participant_user_ids" in v) {
        if (v.kind === "stream")
            requireThat(
                v.stream_id !== null && v.participant_user_ids.length === 0,
                "Invalid stream scope",
            );
        if (v.kind === "direct")
            requireThat(
                v.stream_id === null && v.participant_user_ids.length > 0,
                "Invalid direct scope",
            );
        if (v.kind === "selected") requireThat(v.anchor_message_id !== null, "Missing anchor");
    }
    if ("audience_user_ids" in v) {
        requireThat((v.kind === "stream") === (v.stream_id !== null), "Audience kind");
        for (const k of ["invite_only", "is_web_public", "history_public_to_subscribers"])
            requireThat((v.kind === "stream") === (v[k] !== null), "Audience visibility");
        requireThat(
            equal(
                v.audience_user_ids,
                [...new Set(v.audience_user_ids)].sort((a: any, b: any) => a - b),
            ) &&
                v.audience_user_ids.includes(v.requester_user_id) &&
                v.audience_user_ids.includes(v.bot_user_id),
            "Audience principals",
        );
    }
    if ("target_kind" in v && "principal_user_id" in v) {
        requireThat(
            (v.principal_user_id === null) !== (v.principal_group_id === null),
            "One grant principal required",
        );
        requireThat(v[`${v.target_kind}_id`] !== null, "Missing grant target");
        for (const k of ["runner", "provider", "repository", "profile"])
            if (k !== v.target_kind && !(k === "repository" && v.target_kind === "profile"))
                requireThat(v[`${k}_id`] === null, "Foreign grant target");
        const permitted: Data = {
            runner: ["runner.use"],
            provider: ["provider.use"],
            repository: [
                "repository.read",
                "repository.edit",
                "checks.run",
                "shell.run",
                "dependencies.install",
                "git.commit",
                "git.push",
                "git.draft_pr",
            ],
        };
        if (permitted[v.target_kind])
            requireThat(
                v.actions.every((a: string) => permitted[v.target_kind].includes(a)),
                "Grant action mismatch",
            );
    }
    if ("desired_state" in v) {
        requireThat(v.adapter.mode !== "endpoint" || v.provider_id !== null, "Missing provider");
        requireThat(
            v.desired_state !== "enabled" || v.enabled_revision !== null,
            "Missing enabled revision",
        );
        requireThat(
            [v.enabled_revision, v.readiness_revision].every((x) => x === null || x <= v.revision),
            "Future revision",
        );
    }
    if ("validated_at" in v)
        requireThat(
            v[`${v.kind}_id`] !== null &&
                ["message_id", "attachment_id", "repository_id"].filter((k) => v[k] !== null)
                    .length === 1,
            "Context kind",
        );
    if ("tested_configuration" in v) {
        const scope = v.policy.scope,
            a = v.audience;
        requireThat(
            a.profile_id === v.profile_id &&
                (scope.anchor_message_id === null ||
                    scope.anchor_message_id === a.anchor_message_id) &&
                (scope.kind !== "stream" || scope.stream_id === a.stream_id) &&
                (scope.kind !== "direct" ||
                    equal(
                        [...scope.participant_user_ids].sort((a: number, b: number) => a - b),
                        a.audience_user_ids,
                    )),
            "Audience scope mismatch",
        );
        if (v.job_kind === "answer")
            requireThat(
                v.delivery_target === "answer" &&
                    v.policy.actions.every((a: string) =>
                        ["context.read", "repository.read"].includes(a),
                    ),
                "Answer mutation",
            );
        else if (v.job_kind === "manage")
            // A manage job has no repository or workspace (contract 2.5). Its policy
            // stays read-only; team.manage authority is not an ExecutionAction, so it
            // is granted and checked server-side at propose and execute, never here.
            requireThat(
                v.delivery_target === "answer" &&
                    v.repository === null &&
                    v.policy.actions.every((a: string) => a === "context.read"),
                "Manage job scope",
            );
        else
            requireThat(
                v.delivery_target !== "answer" && v.repository !== null,
                "Missing code repository",
            );
        requireThat(v.adapter.mode !== "endpoint" || v.provider !== null, "Missing provider");
        for (const item of [v.provider, v.repository])
            requireThat(item === null || item.runner_id === v.runner_id, "Foreign runner");
        requireThat(
            !v.policy.hard_cost_cap ||
                (v.budget.cost_limit_microunits !== null &&
                    v.provider?.capability_report.usage === "passed"),
            "Unmeasured cost cap",
        );
        narrow(v);
    }
    if ("setup_operation_id" in v) {
        requireThat(v.adapter.mode !== "endpoint" || v.provider !== null, "Missing probe provider");
        requireThat(
            v.grant.runner_id === v.runner_id && v.grant.profile_revision === v.profile_revision,
            "Stale probe grant",
        );
        requireThat(
            v.provider
                ? v.provider.runner_id === v.runner_id &&
                      v.grant.provider_id === v.provider.id &&
                      v.grant.provider_config_version === v.provider.config_version
                : v.grant.provider_id === null && v.grant.provider_config_version === null,
            "Probe provider mismatch",
        );
    }
    if ("finished_at" in v)
        requireThat(
            Date.parse(v.finished_at) >= Date.parse(v.started_at) &&
                !(v.timed_out && v.exit_code === 0),
            "Invalid verification",
        );
    if ("job_kind" in v && "status" in v)
        requireThat(
            (v.job_kind === "answer") === (v.delivery_target === "answer"),
            "Delivery kind",
        );
    if ("attempt" in v && "probe" in v)
        requireThat(v.attempt === null || v.probe === null, "Multiple claim items");
    if ("event_id" in v) eventSemantics(v);
}
function eventSemantics(v: Data): void {
    const p = v.payload;
    const process: Data = {
        "attempt.starting": "starting",
        "attempt.started": "active",
        "attempt.stopped": "stopped",
        "attempt.interrupted": "unknown",
    };
    if ("process_state" in p)
        requireThat(
            p.process_state === process[v.type] &&
                p.stop_confirmed === (v.type === "attempt.stopped"),
            "Process state mismatch",
        );
    const inputs: Data = {
        "input.applied": "applied",
        "input.delivery_uncertain": "delivery_uncertain",
        "input.received": "pending",
    };
    if ("input_sequence" in p)
        requireThat(p.delivery_state === inputs[v.type], "Input state mismatch");
    if ("tool_class" in p) {
        requireThat(
            v.type === "tool.started"
                ? p.status === "started"
                : ["succeeded", "failed", "outcome_unknown", "cancelled"].includes(p.status),
            "Tool state mismatch",
        );
        requireThat(
            v.type !== "tool.started" || (p.exit_code === null && p.artifact_id === null),
            "Premature tool result",
        );
        requireThat(
            p.status !== "succeeded" || p.exit_code === null || p.exit_code === 0,
            "Failed success",
        );
    }
    if ("status" in p && !("tool_class" in p))
        requireThat(
            p.status ===
                (
                    {
                        "job.queued": "queued",
                        "attempt.starting": "running",
                        "attempt.interrupted": "interrupted",
                        "job.completed": "completed",
                        "attempt.stop_requested": "cancel_requested",
                    } as Data
                )[v.type],
            "Authority state mismatch",
        );
    if ("decision" in p)
        requireThat(
            v.type === "approval.requested"
                ? p.decision === "pending"
                : ["approved", "rejected", "expired", "cancelled", "consumed"].includes(p.decision),
            "Approval state mismatch",
        );
    if ("result_message_id" in p)
        requireThat(
            v.type === "result.published"
                ? p.result_message_id !== null
                : p.result_message_id === null && p.reason,
            "Publication state mismatch",
        );
    if (v.attempt_id === null) requireThat(v.lease_epoch === null, "Missing attempt epoch");
    if (v.lease_epoch === null) requireThat(v.attempt_id === null, "Missing attempt identity");
}
export function parse(name: string, value: unknown): Data {
    const validator = validators.get(name);
    requireThat(validator, "Unknown schema");
    const parsed = validator.parse(value) as Data;
    semantics(parsed);
    if (name === "runner_event" || name === "authority_event")
        requireThat(Buffer.byteLength(canonical(value)) <= 65536, "Event exceeds 64 KiB");
    if (name === "runner_event") {
        const payloads: Data = {
            "workspace.prepared": "repository_id",
            "attempt.starting": "process_state",
            "attempt.started": "process_state",
            "attempt.stopped": "process_state",
            "attempt.interrupted": "process_state",
            "tool.started": "tool_class",
            "tool.finished": "tool_class",
            "input.applied": "input_sequence",
            "input.delivery_uncertain": "input_sequence",
            "verification.finished": "check_id",
            "input.requested": "question",
            "result.prepared": "artifact_ids",
        };
        requireThat(payloads[parsed.type] in parsed.payload, "Wrong event payload");
    }
    if (name === "authority_event") {
        const models: Record<string, string> = {
            "job.queued": "JobStatePayload",
            "attempt.starting": "JobStatePayload",
            "attempt.interrupted": "JobStatePayload",
            "job.completed": "JobStatePayload",
            "attempt.stop_requested": "JobStatePayload",
            "input.received": "InputPayload",
            "approval.requested": "ApprovalPayload",
            "approval.resolved": "ApprovalPayload",
            "result.published": "PublicationPayload",
            "publication.blocked": "PublicationPayload",
        };
        const model = models[parsed.type];
        requireThat(model, "Unknown authority event type");
        z.fromJSONSchema({$ref: `#/$defs/${model}`, $defs: schemas.authority_event.$defs}).parse(
            parsed.payload,
        );
    }
    return parsed;
}
export function validateDescriptor(value: unknown, runnerId: string, probe = false): Data {
    const d = parse(probe ? "probe_descriptor" : "attempt_descriptor", value);
    requireThat(d.runner_id === runnerId, "Wrong runner identity");
    requireThat(
        d.configuration_digest ===
            digest(probe ? effectiveConfiguration(d) : d.tested_configuration),
        "Configuration digest mismatch",
    );
    requireThat(
        d.descriptor_digest ===
            digest(
                Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
            ),
        "Descriptor digest mismatch",
    );
    return d;
}

export function parseChecks(value: unknown): Data[] {
    const validator = z.array(z.fromJSONSchema(schemas.repository.$defs.RequiredCheck)).max(64);
    const parsed = validator.parse(value) as Data[];
    semantics(parsed);
    return parsed;
}
