import $ from "jquery";
import * as z from "zod/mini";

import render_workspace_switcher from "../templates/workspace_switcher.hbs";

import * as app_actions from "./app_actions.ts";
import * as channel from "./channel.ts";
import * as feedback_widget from "./feedback_widget.ts";
import {$t} from "./i18n.ts";
import * as live_updates from "./live_updates.ts";
import * as sidebar_targets from "./sidebar_targets.ts";
import {current_user, realm} from "./state_data.ts";
import * as workspace_create_modal from "./workspace_create_modal.ts";

// The workspace switcher at the top of the sidebar: a button that names
// the current workspace, and the list of every workspace that the
// person belongs to. A click outside the list, Escape, or a choice
// closes the list.

const workspace_schema = z.object({
    realm_id: z.number(),
    name: z.string(),
    url: z.string(),
    role: z.string(),
    brand_color: z.string(),
    initial: z.string(),
    current: z.boolean(),
});
export type Workspace = z.infer<typeof workspace_schema>;

const workspaces_response_schema = z.object({workspaces: z.array(workspace_schema)});
const realm_settings_schema = z.object({can_create_workspace: z.boolean()});

const DEFAULT_AVATAR_COLOR = "#FFD84D";

let workspaces: Workspace[] | undefined;
let load_failed = false;
let can_create_workspace = false;
let is_open = false;

export function hostname_for(url: string): string {
    try {
        return new URL(url).hostname;
    } catch {
        return url;
    }
}

export function role_label(role: string): string {
    switch (role) {
        case "owner":
            return $t({defaultMessage: "Owner"});
        case "admin":
            return $t({defaultMessage: "Admin"});
        case "moderator":
            return $t({defaultMessage: "Moderator"});
        case "guest":
            return $t({defaultMessage: "Guest"});
        default:
            return $t({defaultMessage: "Member"});
    }
}

type WorkspaceRow = {
    realm_id: number;
    name: string;
    initial: string;
    avatar_color: string;
    hostname: string;
    role_label: string;
    current: boolean;
};

export function to_row(workspace: Workspace): WorkspaceRow {
    return {
        realm_id: workspace.realm_id,
        name: workspace.name,
        initial: workspace.initial,
        avatar_color: workspace.brand_color || DEFAULT_AVATAR_COLOR,
        hostname: hostname_for(workspace.url),
        role_label: role_label(workspace.role),
        current: workspace.current,
    };
}

// The button can name the current workspace before the list arrives,
// because the page already knows its name and address.
function current_row(): WorkspaceRow {
    const from_list = workspaces?.find((workspace) => workspace.current);
    if (from_list !== undefined) {
        return to_row(from_list);
    }
    return {
        realm_id: 0,
        name: realm.realm_name,
        initial: (realm.realm_name[0] ?? "?").toUpperCase(),
        avatar_color: DEFAULT_AVATAR_COLOR,
        hostname: hostname_for(realm.realm_url),
        role_label: role_label(sidebar_targets.role_of(current_user)),
        current: true,
    };
}

export function build_view(): {
    current: WorkspaceRow;
    rows: WorkspaceRow[];
    is_loading: boolean;
    load_failed: boolean;
    can_create_workspace: boolean;
    can_manage_workspace: boolean;
} {
    const role = sidebar_targets.role_of(current_user);
    return {
        current: current_row(),
        rows: workspaces === undefined ? [] : workspaces.map((workspace) => to_row(workspace)),
        is_loading: workspaces === undefined && !load_failed,
        load_failed,
        can_create_workspace,
        can_manage_workspace:
            sidebar_targets.can_manage_workspace(role) &&
            sidebar_targets.get_hash("settings", "general") !== undefined,
    };
}

function render(): void {
    $("#left-sidebar-workspace-switcher")
        .html(render_workspace_switcher(build_view()))
        .toggleClass("workspace-switcher-open", is_open);
    $("#left-sidebar-workspace-button").attr("aria-expanded", String(is_open));
}

function on_document_click(event: JQuery.ClickEvent): void {
    // A click on a part that a redraw has since removed is not an outside click.
    if (
        event.target instanceof Node &&
        event.target.isConnected &&
        $(event.target).closest("#left-sidebar-workspace-switcher").length === 0
    ) {
        close_dropdown();
    }
}

function on_document_keydown(event: JQuery.KeyDownEvent): void {
    if (event.key === "Escape") {
        close_dropdown();
        $("#left-sidebar-workspace-button").trigger("focus");
    }
}

export function close_dropdown(): void {
    if (!is_open) {
        return;
    }
    is_open = false;
    $("#left-sidebar-workspace-switcher").removeClass("workspace-switcher-open");
    $("#left-sidebar-workspace-button").attr("aria-expanded", "false");
    $(document).off("click.workspace-switcher keydown.workspace-switcher");
}

function open_dropdown(): void {
    if (is_open) {
        return;
    }
    is_open = true;
    $(document).trigger("sidebar_overlay_opened", ["workspace-switcher"]);
    $("#left-sidebar-workspace-switcher").addClass("workspace-switcher-open");
    $("#left-sidebar-workspace-button").attr("aria-expanded", "true");
    $(document).on("click.workspace-switcher", on_document_click);
    $(document).on("keydown.workspace-switcher", on_document_keydown);
    if (workspaces === undefined) {
        load_workspaces();
    }
}

function load_workspaces(): void {
    if (load_failed) {
        load_failed = false;
        render();
    }
    channel.get({
        url: "/json/users/me/workspaces",
        success(raw_data) {
            workspaces = workspaces_response_schema.parse(raw_data).workspaces;
            render();
        },
        error() {
            load_failed = true;
            render();
            feedback_widget.show_toast({
                text: $t({defaultMessage: "Could not load your workspaces."}),
                variant: "error",
                on_retry: load_workspaces,
            });
        },
    });
}

function open_create_modal(): void {
    close_dropdown();
    if (can_create_workspace) {
        workspace_create_modal.open();
    }
}

function load_can_create_workspace(): void {
    channel.get({
        url: "/json/agent/realm-settings",
        success(raw_data) {
            can_create_workspace = realm_settings_schema.parse(raw_data).can_create_workspace;
            if (can_create_workspace) {
                app_actions.register({
                    name: "create_workspace",
                    label: $t({defaultMessage: "Create new workspace"}),
                    run: open_create_modal,
                });
            }
            render();
        },
    });
}

export function initialize(): void {
    const $container = $("#left-sidebar-workspace-switcher");
    render();

    $container.on("click", "#left-sidebar-workspace-button", () => {
        if (is_open) {
            close_dropdown();
        } else {
            open_dropdown();
        }
    });
    $container.on("click", ".workspace-switcher-item", function (this: HTMLElement) {
        const url = workspaces?.find(
            (workspace) => workspace.realm_id === Number($(this).attr("data-realm-id")),
        )?.url;
        close_dropdown();
        if (url !== undefined && !$(this).hasClass("workspace-switcher-item-current")) {
            window.location.assign(url);
        }
    });
    $container.on("click", ".workspace-switcher-create", open_create_modal);
    $container.on("click", ".workspace-switcher-settings", () => {
        close_dropdown();
        const hash = sidebar_targets.get_hash("settings", "general");
        if (hash !== undefined) {
            window.location.hash = hash.slice(1);
        }
    });

    $(document).on("sidebar_overlay_opened", (_event, source: string) => {
        if (source !== "workspace-switcher") {
            close_dropdown();
        }
    });
    live_updates.on("realm_permissions", render);
    live_updates.on("agent_realm_settings", load_can_create_workspace);

    if (!current_user.is_guest) {
        load_can_create_workspace();
    }
}
