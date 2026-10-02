import $ from "jquery";
import * as z from "zod/mini";

import render_mcp from "../templates/mcp.hbs";

import * as channel from "./channel.ts";
import * as compose_closed_ui from "./compose_closed_ui.ts";
import {$t} from "./i18n.ts";
import * as left_sidebar_navigation_area from "./left_sidebar_navigation_area.ts";
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
                    $(document.createElement("button"))
                        .attr("type", "button")
                        .addClass("action-button action-button-subtle-danger")
                        .attr("data-mcp-revoke", connection.id)
                        .text($t({defaultMessage: "Revoke access"}))
                        .appendTo($row);
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
        load();
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
            $("#mcp-view").html(render_mcp());
            load();
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
