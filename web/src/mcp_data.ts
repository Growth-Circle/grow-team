import * as z from "zod/mini";

import * as channel from "./channel.ts";

// The data side of the MCP connections page (mcp_ui.ts): what the server
// sends about the tools of a connection and about its activity, and the
// small decisions that need no page to test.

export const POLICIES = ["allow", "approve", "block"] as const;
export type Policy = (typeof POLICIES)[number];

const policy_schema = z.enum(POLICIES);

export const tool_schema = z.object({
    name: z.string(),
    description: z.string(),
    kind: z.enum(["read", "write", "destructive"]),
    policy: policy_schema,
    default_policy: policy_schema,
});
export type Tool = z.infer<typeof tool_schema>;

const tools_data_schema = z.object({
    grant: z.object({
        id: z.string(),
        name: z.string(),
        policy_mode: z.enum(["managed", "legacy"]),
    }),
    // False while the workspace has not turned policies on, and for a
    // legacy connection: the choices are saved and not applied yet.
    enforced: z.boolean(),
    tools: z.array(tool_schema),
});
export type ToolsData = z.infer<typeof tools_data_schema>;

const audit_entry_schema = z.object({
    id: z.number(),
    created_at: z.string(),
    connection: z.string(),
    owner: z.string(),
    tool: z.string(),
    outcome: z.string(),
    digest: z.string(),
    actor: z.nullable(z.string()),
    policy: z.nullable(z.string()),
});
export type AuditEntry = z.infer<typeof audit_entry_schema>;

const audit_schema = z.object({entries: z.array(audit_entry_schema), has_more: z.boolean()});
export type AuditPage = z.infer<typeof audit_schema>;

export async function fetch_tools(grant_id: string): Promise<ToolsData> {
    const result: unknown = await channel.get({url: `/json/mcp/access/${grant_id}/tools`});
    return tools_data_schema.parse(result);
}

// The server answers with the tools as they are after the change.
export async function save_policies(
    grant_id: string,
    policies: Record<string, Policy>,
): Promise<ToolsData> {
    const result: unknown = await channel.put({
        url: `/json/mcp/access/${grant_id}/tools`,
        data: {policies: JSON.stringify(policies)},
    });
    return tools_data_schema.parse(result);
}

export async function fetch_audit(before: number | undefined): Promise<AuditPage> {
    const result: unknown = await channel.get({
        url: "/json/mcp/audit",
        data: before === undefined ? {} : {before},
    });
    return audit_schema.parse(result);
}

// A read has nothing to preview, so it can be Auto or Off and never wait
// for approval. Every other tool can wait.
export function policy_options(tool: Tool): Policy[] {
    return tool.kind === "read" ? ["allow", "block"] : [...POLICIES];
}

// The change that sets each read tool to Auto, leaving out the ones that
// already are.
export function read_tools_to_auto(tools: Tool[]): Record<string, Policy> {
    return Object.fromEntries(
        tools
            .filter((tool) => tool.kind === "read" && tool.policy !== "allow")
            .map((tool) => [tool.name, "allow"]),
    );
}

// The change that gives each tool the policy a new connection starts with.
export function recommended_changes(tools: Tool[]): Record<string, Policy> {
    return Object.fromEntries(
        tools
            .filter((tool) => tool.policy !== tool.default_policy)
            .map((tool) => [tool.name, tool.default_policy]),
    );
}

// A connection that was made before policies is "legacy". It can use
// everything its access allows until its owner changes a tool.
export function is_legacy(data: ToolsData): boolean {
    return data.grant.policy_mode === "legacy";
}

// Entries the server writes for the connection itself. They name no tool.
const CONNECTION_EVENTS = new Set(["consent", "token.create", "token.revoke"]);

export function is_connection_event(entry: AuditEntry): boolean {
    return CONNECTION_EVENTS.has(entry.tool);
}
