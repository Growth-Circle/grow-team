import {readFileSync} from "node:fs";
import {ApprovalRejected, waitForApproval} from "./tool-broker.js";
import {TransportError} from "./transport.js";
import type {AttemptChannel} from "./supervisor.js";
import type {ToolDefinition, ToolCall} from "./codecs.js";
import {parse, type Data} from "./protocol.js";

// The 11 tools of the closed catalog in contract 2.3, in table order. Each protocol
// tool id names its own JSON schema in $defs as PascalCase(id) + "Input" (see
// inputDefName below), so the catalog is read from protocol-v1.schema.json instead
// of hand-duplicating 11 schemas that would drift from the server's own definitions.
const TOOL_IDS = [
    "team.find",
    "channel.create",
    "channel.subscribe",
    "channel.unsubscribe",
    "group.create",
    "group.add_members",
    "group.remove_members",
    "topic.post",
    "topic.add_person",
    "topic.resolve",
    "topic.move",
] as const;
export type TeamToolId = (typeof TOOL_IDS)[number];
const DESCRIPTIONS: Record<TeamToolId, string> = {
    "team.find": "Search people, channels, and groups visible to the commander.",
    "channel.create": "Create a channel and subscribe the given people to it.",
    "channel.subscribe": "Add people to an existing channel.",
    "channel.unsubscribe": "Remove people from a channel.",
    "group.create": "Create a user group with the given members.",
    "group.add_members": "Add people to a user group.",
    "group.remove_members": "Remove people from a user group.",
    "topic.post": "Post the first message in a channel topic.",
    "topic.add_person": "Subscribe a person to the channel and mention them in the topic.",
    "topic.resolve": "Mark a topic resolved or unresolved.",
    "topic.move": "Rename a topic or move it to another channel.",
};
const schemas: Data = JSON.parse(
    readFileSync(new URL("../protocol/protocol-v1.schema.json", import.meta.url), "utf8"),
);
function pascal(word: string): string {
    return word[0]!.toUpperCase() + word.slice(1);
}
function inputDefName(tool: string): string {
    return `${tool.split(/[._]/).map(pascal).join("")}Input`;
}
function catalogName(tool: TeamToolId): string {
    return `grow_${tool.replace(".", "_")}`;
}
function toolFor(callName: string): TeamToolId | undefined {
    const tool = callName.slice(5).replace("_", ".");
    return (TOOL_IDS as readonly string[]).includes(tool) ? (tool as TeamToolId) : undefined;
}
// Only for manage jobs (runtime-supervisor.ts). Every tool is always listed; the
// server is the sole authority on whether the commander may actually run one
// (contract 2.2), so the runner does not pre-filter the catalog by grant or role.
export function teamToolCatalog(): ToolDefinition[] {
    return TOOL_IDS.map((tool) => {
        const def = schemas.operation_arguments.$defs[inputDefName(tool)];
        const {tool: _dropped, ...properties} = def.properties;
        return {
            name: catalogName(tool),
            description: DESCRIPTIONS[tool],
            parameters: {
                type: "object",
                properties,
                required: def.required.filter((field: string) => field !== "tool"),
                additionalProperties: false,
            },
        };
    });
}
// The RuntimeTools validate callback for a manage job. The model never supplies
// "tool" itself (it is derived from which grow_* function it called), so a spoofed
// value in the raw call arguments is always overwritten before validation.
export function validateTeamToolCall(call: ToolCall, raw: Data): Data {
    const tool = toolFor(call.name);
    if (!tool) throw new Error("Tool is outside the team catalog");
    return parse("operation_arguments", {action: "team.manage", input: {...raw, tool}});
}
const REJECTION_TEXT: Record<string, string> = {
    rejected: "The commander rejected this step.",
    expired: "The approval expired before the commander decided.",
    cancelled: "The approval was cancelled.",
};
function receiptText(receipt: Data): string {
    return JSON.stringify(receipt);
}
// The RuntimeTools execute callback for a manage job. It never calls
// OperationBoundary.consume or emits tool.started/tool.finished: the server is the
// only authority that runs the Zulip effect (contract 2.5, "audit"), so this only
// forwards the proposal to execute once the commander has approved it.
export function teamToolExecutor(
    channel: AttemptChannel,
    current: () => Data,
    signal: AbortSignal,
    attemptId: string,
    leaseEpoch: number,
): (id: string, args: Data) => Promise<string> {
    return async (id, args) => {
        const tool = args.input.tool as string;
        let proposal = await channel.operations.propose(current(), id, args);
        try {
            proposal = await waitForApproval(
                channel,
                current,
                signal,
                id,
                proposal,
                attemptId,
                leaseEpoch,
            );
        } catch (error) {
            if (error instanceof ApprovalRejected)
                return receiptText({
                    tool,
                    outcome: "failed",
                    summary: "",
                    objects: {},
                    error: REJECTION_TEXT[error.decision] ?? "The commander did not approve this step.",
                });
            throw error;
        }
        try {
            const operation = await channel.operations.execute(current(), proposal);
            return receiptText(
                operation.server_receipt ?? {
                    tool,
                    outcome: operation.status === "succeeded" ? "succeeded" : "failed",
                    summary: "",
                    objects: {},
                    error: null,
                },
            );
        } catch (error) {
            if (error instanceof TransportError && error.code === "outcome_unknown")
                return receiptText({
                    tool,
                    outcome: "unknown",
                    summary: "",
                    objects: {},
                    error: "This step may have finished. Check the channel before you try again.",
                });
            throw error;
        }
    };
}
