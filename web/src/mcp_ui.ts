import $ from "jquery";
import * as z from "zod/mini";

import render_mcp from "../templates/mcp.hbs";
import render_mcp_activity from "../templates/mcp_activity.hbs";
import render_mcp_tools_panel from "../templates/mcp_tools_panel.hbs";

import * as channel from "./channel.ts";
import * as compose_closed_ui from "./compose_closed_ui.ts";
import {$t} from "./i18n.ts";
import * as left_sidebar_navigation_area from "./left_sidebar_navigation_area.ts";
import * as mcp_data from "./mcp_data.ts";
import type {AuditEntry, Policy, Tool, ToolsData} from "./mcp_data.ts";
import * as people from "./people.ts";
import * as sanji_format from "./sanji_format.ts";
import * as views_util from "./views_util.ts";

const connection_schema = z.object({
    id: z.string(),
    name: z.string(),
    method: z.string(),
    scopes: z.array(z.string()),
    expires_at: z.string(),
    last_used_at: z.nullable(z.string()),
    revoked: z.boolean(),
    expired: z.boolean(),
});
const access_schema = z.object({url: z.string(), connections: z.array(connection_schema)});
const token_schema = z.object({id: z.string(), token: z.string(), expires_at: z.string()});

let visible = false;
let visit = 0;
let initialized = false;

// The connections whose tool list the person opened, and what the server
// last said about each one.
const open_tool_panels = new Set<string>();
const tools_by_grant = new Map<string, ToolsData>();
// A connection whose change is on its way: its controls wait.
const busy_grants = new Set<string>();
let audit_entries: AuditEntry[] = [];
let audit_has_more = false;
let audit_failed = false;

function set_visible(value: boolean): void {
    visible = value;
}

function status(text: string): void {
    $("#mcp-status").text(text);
}

function hide_token(): void {
    $("#mcp-created-token").val("");
    $("#mcp-token-result").prop("hidden", true);
}

// ----- Tool permissions -----

function policy_label(policy: Policy): string {
    const labels: Record<Policy, string> = {
        allow: $t({defaultMessage: "Auto"}),
        approve: $t({defaultMessage: "Ask first"}),
        block: $t({defaultMessage: "Off"}),
    };
    return labels[policy];
}

function kind_label(kind: Tool["kind"]): string {
    const labels: Record<Tool["kind"], string> = {
        read: $t({defaultMessage: "Reads"}),
        write: $t({defaultMessage: "Writes"}),
        destructive: $t({defaultMessage: "Can delete or overwrite"}),
    };
    return labels[kind];
}

function panel_note(data: ToolsData): string {
    if (mcp_data.is_legacy(data)) {
        return $t({
            defaultMessage:
                "This connection was made before tool permissions. It can use everything its access allows until you change a tool.",
        });
    }
    if (!data.enforced) {
        return $t({
            defaultMessage:
                "Tool permissions are not turned on for this workspace yet. Your choices are saved and apply when they are.",
        });
    }
    return "";
}

function tools_panel_context(data: ToolsData, busy: boolean): Record<string, unknown> {
    return {
        note: panel_note(data),
        busy,
        tools: data.tools.map((tool) => ({
            name: tool.name,
            description: tool.description,
            kind: tool.kind,
            kind_label: kind_label(tool.kind),
            legend: $t({defaultMessage: "Permission for {tool}"}, {tool: tool.name}),
            radio_name: `mcp-policy-${data.grant.id}-${tool.name}`,
            options: mcp_data.policy_options(tool).map((value) => ({
                value,
                label: policy_label(value),
                checked: value === tool.policy,
            })),
        })),
    };
}

function $panel_for(grant_id: string): JQuery {
    return $(`.mcp-tools-panel[data-grant="${CSS.escape(grant_id)}"]`);
}

function render_tools_panel(grant_id: string, focus?: {tool: string; value: string}): void {
    const data = tools_by_grant.get(grant_id);
    if (data === undefined) {
        return;
    }
    const $panel = $panel_for(grant_id);
    $panel.html(render_mcp_tools_panel(tools_panel_context(data, busy_grants.has(grant_id))));
    if (focus !== undefined) {
        $panel
            .find(
                `.mcp-policy-input[data-tool="${CSS.escape(focus.tool)}"][value="${CSS.escape(focus.value)}"]`,
            )
            .trigger("focus");
    }
}

function set_toggle(grant_id: string, expanded: boolean): void {
    $(`[data-mcp-tools="${CSS.escape(grant_id)}"]`)
        .attr("aria-expanded", String(expanded))
        .text(
            expanded
                ? $t({defaultMessage: "Hide tool permissions"})
                : $t({defaultMessage: "Tool permissions"}),
        );
}

async function open_tools(grant_id: string, current_visit: number): Promise<void> {
    open_tool_panels.add(grant_id);
    set_toggle(grant_id, true);
    const $panel = $panel_for(grant_id).prop("hidden", false);
    if (!tools_by_grant.has(grant_id)) {
        $panel.text($t({defaultMessage: "Loading tools…"}));
    }
    try {
        const data = await mcp_data.fetch_tools(grant_id);
        if (!visible || current_visit !== visit) {
            return;
        }
        tools_by_grant.set(grant_id, data);
        render_tools_panel(grant_id);
    } catch {
        if (visible && current_visit === visit) {
            $panel.text(
                $t({defaultMessage: "Cannot load the tools. Select Refresh to try again."}),
            );
        }
    }
}

function close_tools(grant_id: string): void {
    open_tool_panels.delete(grant_id);
    set_toggle(grant_id, false);
    $panel_for(grant_id).prop("hidden", true);
}

async function save_policies(
    grant_id: string,
    changes: Record<string, Policy>,
    focus?: {tool: string; value: string},
): Promise<void> {
    if (Object.keys(changes).length === 0 || busy_grants.has(grant_id)) {
        return;
    }
    const current_visit = visit;
    busy_grants.add(grant_id);
    render_tools_panel(grant_id, focus);
    try {
        const data = await mcp_data.save_policies(grant_id, changes);
        if (visible && current_visit === visit) {
            tools_by_grant.set(grant_id, data);
            status($t({defaultMessage: "Saved."}));
        }
        void load_audit(true);
    } catch {
        if (visible && current_visit === visit) {
            status($t({defaultMessage: "Cannot save this change. Try again."}));
        }
    } finally {
        busy_grants.delete(grant_id);
        if (visible && current_visit === visit) {
            // Without a saved change this shows what the server still has.
            render_tools_panel(grant_id, focus);
        }
    }
}

// ----- Activity -----

function connection_event_label(tool: string): string {
    switch (tool) {
        case "consent":
            return $t({defaultMessage: "Connection approved"});
        case "token.create":
            return $t({defaultMessage: "Token created"});
        default:
            return $t({defaultMessage: "Access revoked"});
    }
}

function outcome_label(entry: AuditEntry): string {
    switch (entry.outcome) {
        case "success":
            return $t({defaultMessage: "Ran"});
        case "rejected":
            return $t({defaultMessage: "Refused"});
        case "blocked":
            return $t({defaultMessage: "Turned off"});
        case "rate_limited":
            return $t({defaultMessage: "Too many calls"});
        case "asked":
            return $t({defaultMessage: "Waiting for approval"});
        case "approved":
            return $t({defaultMessage: "Approved"});
        case "declined":
            return $t({defaultMessage: "Declined"});
        case "executed":
            return $t({defaultMessage: "Ran after approval"});
        case "policy_changed": {
            const policy = mcp_data.POLICIES.find((value) => value === entry.policy);
            return policy === undefined
                ? $t({defaultMessage: "Permission changed"})
                : $t(
                      {defaultMessage: "Permission changed to {policy}"},
                      {policy: policy_label(policy)},
                  );
        }
        default:
            return entry.outcome;
    }
}

function activity_context(entry: AuditEntry): Record<string, unknown> {
    // An administrator also sees the connections of other people.
    const connection =
        entry.owner === people.my_full_name()
            ? entry.connection
            : $t(
                  {defaultMessage: "{connection} ({owner})"},
                  {connection: entry.connection, owner: entry.owner},
              );
    const meta = [
        entry.actor === null ? "" : $t({defaultMessage: "by {actor}"}, {actor: entry.actor}),
        entry.digest === "" ? "" : $t({defaultMessage: "Digest {digest}"}, {digest: entry.digest}),
    ]
        .filter((part) => part !== "")
        .join(" · ");
    return {
        time: sanji_format.format_date_time(new Date(entry.created_at)),
        what: mcp_data.is_connection_event(entry)
            ? `${connection_event_label(entry.tool)} · ${connection}`
            : `${connection} · ${entry.tool}`,
        outcome: mcp_data.is_connection_event(entry) ? "" : outcome_label(entry),
        meta,
    };
}

function render_activity(): void {
    $("#mcp-activity-list").html(
        render_mcp_activity({
            is_empty: audit_entries.length === 0,
            entries: audit_entries.map((entry) => activity_context(entry)),
        }),
    );
    $("#mcp-activity-more").prop("hidden", !audit_has_more);
    if (audit_failed) {
        $("#mcp-activity-list").text(
            $t({defaultMessage: "Cannot load activity. Select Refresh to try again."}),
        );
    }
}

async function load_audit(reset: boolean): Promise<void> {
    const current_visit = visit;
    const last = audit_entries.at(-1);
    try {
        const page = await mcp_data.fetch_audit(reset || last === undefined ? undefined : last.id);
        if (!visible || current_visit !== visit) {
            return;
        }
        audit_failed = false;
        audit_entries = reset ? page.entries : [...audit_entries, ...page.entries];
        audit_has_more = page.has_more;
    } catch {
        if (!visible || current_visit !== visit) {
            return;
        }
        audit_failed = true;
    }
    render_activity();
}

// ----- Connections -----

function load(): void {
    const current_visit = visit;
    channel.get({
        url: "/json/mcp/access",
        success(data) {
            if (!visible || current_visit !== visit) {
                return;
            }
            const result = access_schema.parse(data);
            $("#mcp-endpoint").text(result.url);
            const $list = $("#mcp-access-list").empty();
            if (result.connections.length === 0) {
                $list.text($t({defaultMessage: "No connections yet."}));
            }
            for (const connection of result.connections) {
                const $row = $(document.createElement("article"))
                    .addClass("mcp-connection")
                    .appendTo($list);
                $(document.createElement("h3")).text(connection.name).appendTo($row);
                $(document.createElement("p"))
                    .text(
                        connection.scopes.includes("team:write")
                            ? $t({defaultMessage: "Read and write access"})
                            : $t({defaultMessage: "Read access"}),
                    )
                    .appendTo($row);
                $(document.createElement("p"))
                    .text(
                        `${connection.method.toUpperCase()} · ${new Date(connection.expires_at).toLocaleDateString()}`,
                    )
                    .appendTo($row);
                if (connection.revoked || connection.expired) {
                    $(document.createElement("p"))
                        .text(
                            connection.revoked
                                ? $t({defaultMessage: "Revoked"})
                                : $t({defaultMessage: "Expired"}),
                        )
                        .appendTo($row);
                } else {
                    const $actions = $(document.createElement("div"))
                        .addClass("mcp-connection-actions")
                        .appendTo($row);
                    $(document.createElement("button"))
                        .attr("type", "button")
                        .addClass("action-button action-button-subtle-neutral")
                        .attr("data-mcp-tools", connection.id)
                        .attr("aria-expanded", "false")
                        .text($t({defaultMessage: "Tool permissions"}))
                        .appendTo($actions);
                    $(document.createElement("button"))
                        .attr("type", "button")
                        .addClass("action-button action-button-subtle-danger")
                        .attr("data-mcp-revoke", connection.id)
                        .text($t({defaultMessage: "Revoke access"}))
                        .appendTo($actions);
                    $(document.createElement("div"))
                        .addClass("mcp-tools-panel")
                        .attr("data-grant", connection.id)
                        .prop("hidden", true)
                        .appendTo($row);
                    if (open_tool_panels.has(connection.id)) {
                        void open_tools(connection.id, current_visit);
                    }
                }
            }
        },
        error() {
            if (visible && current_visit === visit) {
                status(
                    $t({
                        defaultMessage: "Cannot load MCP connections. Select Refresh to try again.",
                    }),
                );
            }
        },
    });
}

async function copy_token(token: string, current_visit: number): Promise<void> {
    try {
        await navigator.clipboard.writeText(token);
        if (visible && current_visit === visit) {
            status($t({defaultMessage: "Token copied."}));
        }
    } catch {
        if (visible && current_visit === visit) {
            status($t({defaultMessage: "Select the token and copy it manually."}));
        }
    }
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    const $root = $("#mcp-view");
    $root.on("click", "#mcp-refresh", () => {
        status("");
        tools_by_grant.clear();
        load();
        void load_audit(true);
    });
    $root.on("click", "#mcp-activity-more", () => {
        void load_audit(false);
    });
    $root.on("click", "[data-mcp-tools]", function () {
        const grant_id = $(this).attr("data-mcp-tools");
        if (grant_id === undefined) {
            return;
        }
        if (open_tool_panels.has(grant_id)) {
            close_tools(grant_id);
        } else {
            void open_tools(grant_id, visit);
        }
    });
    $root.on("change", ".mcp-policy-input", function () {
        const grant_id = $(this).closest(".mcp-tools-panel").attr("data-grant");
        const tool = $(this).attr("data-tool");
        const policy = mcp_data.POLICIES.find((value) => value === $(this).val());
        if (grant_id !== undefined && tool !== undefined && policy !== undefined) {
            void save_policies(grant_id, {[tool]: policy}, {tool, value: policy});
        }
    });
    $root.on("click", "[data-mcp-bulk]", function () {
        const grant_id = $(this).closest(".mcp-tools-panel").attr("data-grant");
        const data = grant_id === undefined ? undefined : tools_by_grant.get(grant_id);
        if (grant_id === undefined || data === undefined) {
            return;
        }
        const changes =
            $(this).attr("data-mcp-bulk") === "read-auto"
                ? mcp_data.read_tools_to_auto(data.tools)
                : mcp_data.recommended_changes(data.tools);
        void save_policies(grant_id, changes);
    });
    $root.on("click", "#mcp-hide-token", hide_token);
    $root.on("click", "#mcp-copy-token", () => {
        const token = String($("#mcp-created-token").val() ?? "");
        const current_visit = visit;
        void copy_token(token, current_visit);
    });
    $root.on("submit", "#mcp-token-form", (event) => {
        event.preventDefault();
        hide_token();
        const $button = $("#mcp-token-form button").prop("disabled", true);
        const current_visit = visit;
        channel.post({
            url: "/json/mcp/access",
            data: {
                name: String($("#mcp-token-name").val() ?? "").trim(),
                write: $("#mcp-token-write").prop("checked") ? "true" : "false",
            },
            success(data) {
                if (!visible || current_visit !== visit) {
                    return;
                }
                const result = token_schema.parse(data);
                $("#mcp-created-token").val(result.token);
                $("#mcp-token-result").prop("hidden", false);
                $button.prop("disabled", false);
                status($t({defaultMessage: "Token created. Copy it before leaving this page."}));
                load();
                void load_audit(true);
            },
            error() {
                if (visible && current_visit === visit) {
                    $button.prop("disabled", false);
                    status(
                        $t({
                            defaultMessage:
                                "Token creation failed. Refresh the connection list before you try again.",
                        }),
                    );
                }
            },
        });
    });
    $root.on("click", "[data-mcp-revoke]", function () {
        const $button = $(this).prop("disabled", true);
        const id = $button.attr("data-mcp-revoke");
        const current_visit = visit;
        channel.post({
            url: `/json/mcp/access/${id}/revoke`,
            success() {
                if (visible && current_visit === visit) {
                    hide_token();
                    status($t({defaultMessage: "Access revoked."}));
                    load();
                    void load_audit(true);
                }
            },
            error() {
                if (visible && current_visit === visit) {
                    $button.prop("disabled", false);
                    status($t({defaultMessage: "Cannot revoke access. Try again."}));
                }
            },
        });
    });
}

export function show(_args: string[]): void {
    initialize();
    views_util.show({
        highlight_view_in_left_sidebar() {
            views_util.handle_message_view_deactivated(() => {
                left_sidebar_navigation_area.select_top_left_corner_item("");
            });
        },
        $view: $("#mcp-view"),
        update_compose: compose_closed_ui.update_buttons,
        is_visible: () => visible,
        set_visible,
        complete_rerender() {
            visit += 1;
            tools_by_grant.clear();
            $("#mcp-view").html(render_mcp());
            load();
            void load_audit(true);
        },
    });
    $("#compose").addClass("hide-closed-compose");
}

export function hide(): void {
    if (!visible) {
        return;
    }
    visit += 1;
    hide_token();
    views_util.hide({$view: $("#mcp-view"), set_visible});
    $("#compose").removeClass("hide-closed-compose");
}

export function title(): string {
    return $t({defaultMessage: "MCP connections"});
}
