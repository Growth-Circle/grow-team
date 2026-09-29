import * as z from "zod/mini";

/*
    Schema and types only, split out from agent_job_widget.ts to avoid a
    circular dependency with widget_schema.ts (the same reason todo_data.ts,
    poll_data.ts, and zform_data.ts exist as separate tiny modules).

    This mirrors the server's AgentJobCard / AgentJobCardArtifact TypedDicts
    (zerver/actions/agent_jobs.py, spec 13). The first submessage on a
    message wraps this as {widget_type, extra_data}; every later submessage
    sends this same shape unwrapped, as a full fresh snapshot (never a
    diff), so one schema parses both.
*/

export const agent_job_card_artifact_schema = z.object({
    kind: z.string(),
    label: z.string(),
    url: z.nullable(z.string()),
    task_id: z.nullable(z.number()),
});
export type AgentJobCardArtifact = z.infer<typeof agent_job_card_artifact_schema>;

export const agent_job_card_data_schema = z.object({
    job_id: z.string(),
    status: z.string(),
    title: z.string(),
    step_label: z.string(),
    progress: z.number(),
    artifacts: z.array(agent_job_card_artifact_schema),
    reason_code: z.nullable(z.string()),
    can_retry: z.boolean(),
});
export type AgentJobCardData = z.infer<typeof agent_job_card_data_schema>;

export const agent_job_widget_extra_data_schema = agent_job_card_data_schema;
