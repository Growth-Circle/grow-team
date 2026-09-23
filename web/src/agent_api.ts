/* eslint-disable @typescript-eslint/explicit-function-return-type, unicorn/no-object-as-default-parameter -- Zod infers the exact response types for these small endpoint wrappers. */
import * as z from "zod/mini";

import * as channel from "./channel.ts";

const identity = z.object({id: z.number(), name: z.string()});
const runner_schema = z.object({
    id: z.string(),
    name: z.string(),
    owner_id: z.number(),
    host_kind: z.enum(["workstation", "server", "unknown"]),
    status: z.string(),
    observed_presence: z.string(),
    observed_at: z.nullable(z.string()),
    metadata_revision: z.number(),
    revision: z.number(),
    catalog_revision: z.number(),
    catalog: z.nullable(z.unknown()),
    catalog_summary: z.object({
        revision: z.number(),
        reported_at: z.nullable(z.string()),
        adapters: z.array(z.object({id: z.string(), version: z.string(), auth_state: z.string()})),
        sandboxes: z.array(z.object({alias: z.string()})),
    }),
    allowed_actions: z.array(z.string()),
});
const provider_schema = z.object({
    id: z.string(),
    name: z.string(),
    owner_id: z.number(),
    runner_id: z.string(),
    model_id: z.string(),
    allowed_models: z.array(z.string()),
    api_mode: z.string(),
    context_window_tokens: z.number(),
    max_output_tokens: z.number(),
    data_scope: z.array(z.string()),
    capabilities: z.unknown(),
    disabled_at: z.nullable(z.string()),
    allowed_actions: z.array(z.string()),
    config_version: z.number(),
    metadata_revision: z.optional(z.number()),
    base_url: z.optional(z.string()),
    network: z.optional(z.unknown()),
    credential: z.optional(z.object({retained: z.boolean(), kind: z.nullable(z.string())})),
});
const repository_schema = z.object({
    id: z.string(),
    workspace_alias: z.string(),
    owner_id: z.number(),
    runner_id: z.string(),
    disabled_at: z.nullable(z.string()),
    allowed_actions: z.array(z.string()),
    policy_version: z.optional(z.number()),
    canonical_origin: z.optional(z.nullable(z.string())),
    allowed_refs: z.optional(z.array(z.string())),
    required_checks: z.optional(z.array(z.unknown())),
});
const profile_schema = z.object({
    id: z.string(),
    name: z.string(),
    description: z.string(),
    runner_id: z.nullable(z.string()),
    provider_id: z.nullable(z.string()),
    repository_id: z.nullable(z.string()),
    state: z.string(),
    desired_state: z.string(),
    readiness_state: z.string(),
    revision: z.number(),
    metadata_revision: z.number(),
    bot_user_id: z.number(),
    default_mode: z.string(),
    capabilities: z.unknown(),
    owner_id: z.number(),
    mode: z.string(),
    adapter_id: z.string(),
    adapter_version: z.string(),
    enabled_revision: z.nullable(z.number()),
    readiness_revision: z.nullable(z.number()),
    allowed_actions: z.array(z.string()),
    // Optional until the team-backend release lane ships it: an older
    // response simply omits it, and the owner-only share panel hides itself.
    shared_with: z.optional(
        z.array(
            z.object({
                principal_kind: z.enum(["user", "group"]),
                principal_id: z.number(),
                complete: z.boolean(),
            }),
        ),
    ),
    owner: identity,
    runner: z.nullable(runner_schema),
    provider: z.nullable(provider_schema),
    repository: z.nullable(repository_schema),
    access: z.object({
        complete: z.boolean(),
        runner: z.boolean(),
        provider: z.boolean(),
        repository: z.boolean(),
    }),
    configuration: z.nullable(
        z.object({
            policy: z.object({
                scope: z.unknown(),
                actions: z.array(z.string()),
                sandbox_alias: z.string(),
                network: z.unknown(),
                hard_cost_cap: z.boolean(),
            }),
            budget: z.unknown(),
            scope_restricted: z.boolean(),
            network_retained: z.boolean(),
        }),
    ),
});
const requirement_schema = z.object({
    code: z.string(),
    surface: z.enum([
        "runner",
        "runner_workspace",
        "adapter",
        "provider",
        "grant",
        "sandbox",
        "diagnostic",
    ]),
    action: z.enum([
        "connect_runner",
        "register_workspace",
        "install_adapter",
        "login_vendor",
        "edit_provider",
        "probe_again",
        "request_grant",
        "configure_sandbox",
        "view_diagnostic",
    ]),
    diagnostic_id: z.nullable(z.string()),
});
const setup_schema = z.object({
    id: z.string(),
    phase: z.string(),
    requirements: z.array(requirement_schema),
    profile_revision: z.number(),
    provider_config_version: z.nullable(z.number()),
    created_at: z.string(),
    finished_at: z.nullable(z.string()),
});
const profile_attachment_schema = z.object({
    stream_id: z.number(),
    name: z.string(),
    bot_member: z.boolean(),
});
const channel_attachment_schema = z.object({
    profile: profile_schema,
    stream_id: z.number(),
    bot_member: z.boolean(),
});
const grant_schema = z.object({
    id: z.string(),
    target_kind: z.string(),
    target_id: z.string(),
    principal: z.unknown(),
    actions: z.array(z.string()),
    scope: z.unknown(),
    scope_restricted: z.boolean(),
    repository_id: z.nullable(z.string()),
    repository_restricted: z.boolean(),
    expires_at: z.nullable(z.string()),
    revision: z.number(),
    revoked: z.boolean(),
    allowed_actions: z.array(z.string()),
});
const job_schema = z.object({
    id: z.string(),
    profile_id: z.string(),
    requester_id: z.number(),
    source_message_id: z.nullable(z.number()),
    status: z.string(),
    phase: z.string(),
    version: z.number(),
    request: z.string(),
    job_kind: z.string(),
    delivery_target: z.string(),
    blocked_reason: z.nullable(z.string()),
    // Optional until the team-backend release lane ships them: an older
    // response omits these, and the panel falls back to its status text.
    reason_code: z.optional(z.nullable(z.string())),
    resume_available: z.optional(z.boolean()),
    resume_unavailable_reason: z.optional(z.nullable(z.string())),
    result: z.unknown(),
    allowed_actions: z.array(z.string()),
});
const attempt_schema = z.object({
    id: z.string(),
    number: z.number(),
    process_state: z.string(),
    active: z.boolean(),
    base_commit: z.nullable(z.string()),
    tree_hash: z.nullable(z.string()),
});
const operation_schema = z.object({
    operation_id: z.string(),
    operation_hash: z.string(),
    version: z.number(),
    status: z.string(),
    attempt_id: z.string(),
    action: z.string(),
    approval_id: z.nullable(z.string()),
    approval_version: z.nullable(z.number()),
    nonce: z.nullable(z.string()),
    can_decide: z.optional(z.boolean()),
    approval_decision: z.optional(z.string()),
});
const artifact_schema = z.object({
    id: z.string(),
    attempt_id: z.string(),
    kind: z.string(),
    filename: z.string(),
    size: z.number(),
    checksum: z.string(),
    media_type: z.string(),
});
const cursor_schema = z.object({
    offset: z.number(),
    next_offset: z.number(),
    truncated: z.boolean(),
});
const check_schema = z.object({
    check_id: z.string(),
    outcome: z.string(),
    attempt_id: z.optional(z.string()),
    tree_hash: z.optional(z.string()),
    command: z.optional(z.array(z.string())),
    cwd: z.optional(z.string()),
    output_artifact_id: z.optional(z.string()),
});
// One server lock serializes agent authority, and a busy server answers with
// 503 and Retry-After. A read has no effect, so it waits for its turn.
const busy_read_retries = 4;
type FailedResponse = {status: number; getResponseHeader: (name: string) => string | null};
function is_failed_response(error: unknown): error is FailedResponse {
    return (
        typeof error === "object" &&
        error !== null &&
        "status" in error &&
        "getResponseHeader" in error &&
        typeof error.getResponseHeader === "function"
    );
}
function busy_retry_delay_ms(error: unknown): number | undefined {
    if (!is_failed_response(error) || error.status !== 503) {
        return undefined;
    }
    const seconds = Number(error.getResponseHeader("Retry-After"));
    return (Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 5) : 1) * 1000;
}
// Zulip's JSON success envelope places payload fields at the top level.
async function get<T extends z.ZodMiniType>(url: string, schema: T): Promise<z.infer<T>> {
    for (let attempt = 0; ; attempt += 1) {
        try {
            const result: unknown = await channel.get({url});
            return schema.parse(result);
        } catch (error) {
            const delay = busy_retry_delay_ms(error);
            if (delay === undefined || attempt >= busy_read_retries) {
                throw error;
            }
            await new Promise((resolve) => setTimeout(resolve, delay));
        }
    }
}
async function mutate<T extends z.ZodMiniType>(
    method: "post" | "patch",
    url: string,
    payload: Record<string, unknown>,
    schema: T,
): Promise<z.infer<T>> {
    const options = {url, data: {payload: JSON.stringify({schema_version: 1, ...payload})}};
    const result: unknown = await (method === "post"
        ? channel.post(options)
        : channel.patch(options));
    return schema.parse(result);
}
const version = {schema_version: z.literal(1)};
export type AgentProfile = z.infer<typeof profile_schema>;
export type AgentSetup = z.infer<typeof setup_schema>;
export type AgentRunner = z.infer<typeof runner_schema>;
export type AgentProvider = z.infer<typeof provider_schema>;
export function profile_network_choice(
    provider_id: string,
    provider: AgentProvider | undefined,
    profile:
        | {
              provider_id: string | null;
              configuration: {network_retained: boolean; policy: {network: unknown}} | null;
          }
        | undefined,
    owner_id: number,
    default_network: unknown,
): {retain_network: true} | {provider_network_version: number} | {network: unknown} {
    if (profile?.provider_id === provider_id && profile.configuration?.network_retained) {
        return {retain_network: true};
    }
    if (provider && provider.owner_id !== owner_id) {
        return {provider_network_version: provider.config_version};
    }
    return {
        network:
            profile?.provider_id === provider_id
                ? (profile.configuration?.policy.network ?? default_network)
                : (provider?.network ?? default_network),
    };
}
export type AgentRepository = z.infer<typeof repository_schema>;
export type AgentJob = z.infer<typeof job_schema>;
export type AgentJobDetail = z.infer<typeof job_detail_schema>;
export type AgentSelectionResolution = z.infer<typeof selection_schema>["selection"];
export type AgentListFilters = {
    offset?: number;
    limit?: number;
    ownership?: "all" | "mine" | "shared";
    access?: "all" | "complete" | "partial";
    host_kind?: "all" | "workstation" | "server" | "unknown";
    search?: string;
};
function query(filters: AgentListFilters): string {
    return new URLSearchParams(
        Object.entries(filters).map(([key, value]) => [key, String(value)]),
    ).toString();
}
export async function list_profiles(filters: AgentListFilters = {offset: 0, limit: 50}) {
    return get(
        `/json/agent/profiles?${query(filters)}`,
        z.object({...version, count: z.number(), profiles: z.array(profile_schema)}),
    );
}
export async function get_profile(id: string) {
    return get(
        `/json/agent/profiles/${id}`,
        z.object({
            ...version,
            profile: profile_schema,
            setup: z.nullable(setup_schema),
            attachments: z.array(profile_attachment_schema),
        }),
    );
}
export async function recover_profile(key: string) {
    return get(
        `/json/agent/profiles/recover?idempotency_key=${encodeURIComponent(key)}`,
        z.object({...version, profile: profile_schema, setup: setup_schema}),
    );
}
export async function create_profile(payload: Record<string, unknown>) {
    return mutate(
        "post",
        "/json/agent/profiles",
        payload,
        z.object({...version, profile: profile_schema, setup_id: z.string()}),
    );
}
export async function update_profile(id: string, payload: Record<string, unknown>) {
    return mutate(
        "patch",
        `/json/agent/profiles/${id}`,
        payload,
        z.object({...version, profile: profile_schema}),
    );
}
export async function profile_action(
    id: string,
    action: "pause" | "archive" | "enable" | "readiness",
    payload: Record<string, unknown>,
) {
    return mutate("post", `/json/agent/profiles/${id}/${action}`, payload, z.object({...version}));
}
export async function share_profile(
    id: string,
    payload: {
        principal_user_id?: number;
        principal_group_id?: number;
        allow_job_control?: boolean;
        allow_job_review?: boolean;
    },
) {
    return mutate(
        "post",
        `/json/agent/profiles/${id}/share`,
        payload,
        z.object({
            ...version,
            grants: z.array(
                z.object({id: z.string(), target_kind: z.string(), revision: z.number()}),
            ),
            skipped: z.array(z.object({target_kind: z.string(), reason: z.string()})),
        }),
    );
}
export async function unshare_profile(
    id: string,
    payload: {principal_user_id?: number; principal_group_id?: number},
) {
    return mutate("post", `/json/agent/profiles/${id}/unshare`, payload, z.object({...version}));
}
export async function attach_channel(id: string, stream_id: number, expected_revision: number) {
    return mutate(
        "post",
        `/json/agent/profiles/${id}/attach-channel`,
        {stream_id, expected_revision},
        z.object({...version}),
    );
}
export async function list_channel_attachments(stream_id: number) {
    return get(
        `/json/agent/channels/${stream_id}/attachments`,
        z.object({...version, attachments: z.array(channel_attachment_schema)}),
    );
}
export async function list_runners(filters: AgentListFilters = {offset: 0, limit: 50}) {
    return get(
        `/json/agent/runners?${query(filters)}`,
        z.object({...version, count: z.number(), runners: z.array(runner_schema)}),
    );
}
export async function get_runner(id: string) {
    return get(`/json/agent/runners/${id}`, z.object({...version, runner: runner_schema}));
}
export async function update_runner_metadata(args: {
    runner_id: string;
    expected_metadata_revision: number;
    name: string;
    host_kind: "workstation" | "server" | "unknown";
}) {
    const {runner_id, ...payload} = args;
    return mutate(
        "patch",
        `/json/agent/runners/${runner_id}/metadata`,
        payload,
        z.object({...version, runner: runner_schema}),
    );
}
export async function approve_pairing(pairing_id: string, user_code: string) {
    return mutate(
        "post",
        "/json/agent/pairings/approve",
        {pairing_id, user_code},
        z.object({...version, pairing: z.object({id: z.string(), state: z.string()})}),
    );
}
export async function revoke_runner(id: string, expected_revision: number) {
    return mutate(
        "post",
        `/json/agent/runners/${id}/revoke`,
        {expected_revision},
        z.object({...version}),
    );
}
export async function list_providers(filters: AgentListFilters = {offset: 0, limit: 50}) {
    return get(
        `/json/agent/providers?${query(filters)}`,
        z.object({...version, count: z.number(), providers: z.array(provider_schema)}),
    );
}
export async function get_provider(id: string) {
    return get(`/json/agent/providers/${id}`, z.object({...version, provider: provider_schema}));
}
export async function create_provider(payload: Record<string, unknown>) {
    return mutate(
        "post",
        "/json/agent/providers",
        payload,
        z.object({
            ...version,
            provider: z.object({
                id: z.string(),
                name: z.string(),
                model_id: z.string(),
                revision: z.number(),
            }),
        }),
    );
}
export async function update_provider(id: string, payload: Record<string, unknown>) {
    return mutate(
        "patch",
        `/json/agent/providers/${id}`,
        payload,
        z.object({...version, provider: provider_schema}),
    );
}
export async function probe_provider(id: string, payload: Record<string, unknown>) {
    return mutate("post", `/json/agent/providers/${id}/probe`, payload, z.object({...version}));
}
export async function list_repositories(filters: AgentListFilters = {offset: 0, limit: 50}) {
    return get(
        `/json/agent/repositories?${query(filters)}`,
        z.object({...version, count: z.number(), repositories: z.array(repository_schema)}),
    );
}
export async function create_repository(payload: Record<string, unknown>) {
    return mutate(
        "post",
        "/json/agent/repositories",
        payload,
        z.object({
            ...version,
            repository: z.object({
                id: z.string(),
                workspace_alias: z.string(),
                revision: z.number(),
            }),
        }),
    );
}
export async function list_grants(target_kind: string, target_id: string, offset = 0) {
    return get(
        `/json/agent/grants?${query({offset, limit: 50})}&target_kind=${encodeURIComponent(target_kind)}&target_id=${encodeURIComponent(target_id)}`,
        z.object({...version, count: z.number(), grants: z.array(grant_schema)}),
    );
}
export async function create_grant(payload: Record<string, unknown>) {
    return mutate(
        "post",
        "/json/agent/grants",
        payload,
        z.object({
            ...version,
            grant: z.object({id: z.string(), target_kind: z.string(), revision: z.number()}),
        }),
    );
}
export async function revoke_grant(id: string, expected_revision: number) {
    return mutate(
        "post",
        `/json/agent/grants/${id}/revoke`,
        {expected_revision},
        z.object({...version}),
    );
}
export async function get_team_default() {
    return get(
        "/json/agent/team-default",
        z.object({
            ...version,
            default: z.object({
                has_default: z.boolean(),
                selection_revision: z.optional(z.number()),
                allowed_actions: z.optional(z.array(z.string())),
                profile: z.optional(profile_schema),
            }),
        }),
    );
}
export async function update_team_default(
    expected_selection_revision: number,
    profile_id: string | null,
) {
    return mutate(
        "patch",
        "/json/agent/team-default",
        {expected_selection_revision, profile_id},
        z.object({
            ...version,
            default: z.object({
                has_default: z.boolean(),
                selection_revision: z.optional(z.number()),
                profile: z.optional(profile_schema),
            }),
        }),
    );
}
const selection_schema = z.object({
    ...version,
    selection: z.object({
        selection_source: z.enum(["team_default", "explicit", "none"]),
        profile_id: z.nullable(z.string()),
        profile_revision: z.nullable(z.number()),
        selection_revision: z.nullable(z.number()),
        selection_state: z.enum(["unset", "explicit", "cleared"]),
        eligible: z.boolean(),
        queue_permitted: z.boolean(),
        reason: z.string(),
        // Optional until the team-backend release lane ships it: an older
        // response omits it, and Coding stays disabled with no repository.
        repository: z.optional(
            z.nullable(z.object({id: z.string(), alias: z.string(), base_ref: z.string()})),
        ),
    }),
});
export async function resolve_selection(args: {
    destination?: unknown;
    source_message_id?: number;
    job_kind?: "answer" | "code";
    repository_id?: string;
    explicit_profile_id?: string;
    selection_state: "unset" | "explicit" | "cleared";
}): Promise<AgentSelectionResolution> {
    return (await mutate("post", "/json/agent/selection/resolve", args, selection_schema))
        .selection;
}
export async function preflight_message(profile_ids: string[], destination: unknown) {
    return mutate(
        "post",
        "/json/agent/message-preflight",
        {profile_ids, destination},
        z.object({
            ...version,
            decisions: z.array(z.object({profile_id: z.string(), decision: z.string()})),
        }),
    );
}
export async function message_dispatch(message_id: number) {
    return get(
        `/json/agent/messages/${message_id}/dispatch`,
        z.object({
            ...version,
            source_message_id: z.number(),
            dispatch_receipts: z.array(
                z.object({
                    profile_id: z.string(),
                    decision: z.string(),
                    reason: z.string(),
                    job_id: z.nullable(z.string()),
                    job_status: z.nullable(z.string()),
                }),
            ),
        }),
    );
}
export async function recover_send_intent(client_key: string) {
    return get(
        `/json/agent/send-intents/${client_key}`,
        z.object({...version, source_message_id: z.number(), deleted: z.boolean()}),
    );
}
export async function create_job(payload: Record<string, unknown>) {
    return mutate("post", "/json/agent/jobs", payload, z.object({...version, job: job_schema}));
}
const job_detail_schema = z.object({
    ...version,
    job: job_schema,
    attempts: z.array(attempt_schema),
    operations: z.array(operation_schema),
    artifacts: z.array(artifact_schema),
    required_checks: z.array(check_schema),
    operations_cursor: cursor_schema,
    artifacts_cursor: cursor_schema,
});
export async function list_jobs(offset = 0) {
    return get(
        `/json/agent/jobs?offset=${offset}&limit=50`,
        z.object({...version, count: z.number(), jobs: z.array(job_schema)}),
    );
}
export async function get_job(id: string, operation_offset = 0, artifact_offset = 0) {
    return get(
        `/json/agent/jobs/${id}?operation_offset=${operation_offset}&operation_limit=50&artifact_offset=${artifact_offset}&artifact_limit=50`,
        job_detail_schema,
    );
}
export async function get_job_events(id: string, after = 0) {
    return get(
        `/json/agent/jobs/${id}/events?after=${after}`,
        z.object({
            ...version,
            events: z.array(
                z.object({
                    id: z.string(),
                    sequence: z.number(),
                    attempt_id: z.nullable(z.string()),
                    type: z.string(),
                    payload: z.unknown(),
                    occurred_at: z.string(),
                }),
            ),
            job_version: z.number(),
        }),
    );
}
export async function get_job_inputs(id: string, offset = 0) {
    return get(
        `/json/agent/jobs/${id}/inputs?offset=${offset}&limit=50`,
        z.object({
            ...version,
            count: z.number(),
            inputs: z.array(
                z.object({
                    id: z.string(),
                    client_key: z.string(),
                    sequence: z.number(),
                    input_type: z.string(),
                    text: z.string(),
                    delivery_state: z.string(),
                }),
            ),
        }),
    );
}
export async function job_action(
    id: string,
    action: "cancel" | "resume" | "inputs",
    payload: Record<string, unknown>,
) {
    return mutate("post", `/json/agent/jobs/${id}/${action}`, payload, z.object({...version}));
}
export async function decide_approval(id: string, payload: Record<string, unknown>) {
    return mutate(
        "post",
        `/json/agent/approvals/${id}/decision`,
        payload,
        z.object({...version, approval_id: z.string(), decision: z.string(), version: z.number()}),
    );
}
