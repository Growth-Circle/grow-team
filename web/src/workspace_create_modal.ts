import $ from "jquery";
import * as z from "zod/mini";

import render_workspace_create_modal from "../templates/workspace_create_modal.hbs";

import * as channel from "./channel.ts";
import * as dialog_widget from "./dialog_widget.ts";
import * as feedback_widget from "./feedback_widget.ts";
import {$t} from "./i18n.ts";

// The "Create new workspace" modal. A workspace is a separate Zulip
// realm. The server answers with the link that finishes the setup, and
// the browser goes there.

// The order is the color index that the server expects.
const COLORS: readonly {label: () => string; hex: string}[] = [
    {label: () => $t({defaultMessage: "Yellow"}), hex: "#FFD84D"},
    {label: () => $t({defaultMessage: "Green"}), hex: "#16C784"},
    {label: () => $t({defaultMessage: "Purple"}), hex: "#8B74FF"},
    {label: () => $t({defaultMessage: "Orange"}), hex: "#FF6A3D"},
];

// Let the person read the toast before the page changes.
const REDIRECT_DELAY_MS = 1200;

const create_response_schema = z.object({redirect_url: z.string()});

let selected_color = 0;

export function slug_for(name: string): string {
    return name.toLowerCase().replaceAll(/[^a-z0-9]+/g, "");
}

export function slug_preview(name: string, fallback: string): string {
    return `${slug_for(name) || fallback}.sanji.space`;
}

function update_slug_preview(): void {
    const name = $<HTMLInputElement>("#workspace_create_name").val() ?? "";
    $("#workspace_create_slug_preview").text(
        slug_preview(name, $t({defaultMessage: "workspacename"})),
    );
}

function update_color_selection(): void {
    $(".workspace-create-color-pill").each(function (this: HTMLElement) {
        const is_selected = Number($(this).attr("data-color-index")) === selected_color;
        $(this).toggleClass("selected", is_selected).attr("aria-checked", String(is_selected));
    });
}

function submit(): void {
    const name = ($<HTMLInputElement>("#workspace_create_name").val() ?? "").trim();
    if (name === "") {
        dialog_widget.hide_dialog_spinner();
        $("#workspace_create_name").trigger("focus");
        return;
    }

    // A retry from the error toast comes here without a click, so the
    // spinner needs to start here as well. Showing it twice is harmless.
    dialog_widget.show_dialog_spinner();
    void channel.post({
        url: "/json/workspaces",
        data: {name, subdomain: slug_for(name), color: JSON.stringify(selected_color)},
        success(raw_data) {
            const {redirect_url} = create_response_schema.parse(raw_data);
            // The modal fades out with its spinner on (see dialog_widget.ts).
            dialog_widget.close();
            feedback_widget.show_toast({
                text: $t(
                    {
                        defaultMessage:
                            "Workspace {name} created. Pair a runner to start using agents.",
                    },
                    {name},
                ),
            });
            setTimeout(() => {
                window.location.assign(redirect_url);
            }, REDIRECT_DELAY_MS);
        },
        error(xhr) {
            dialog_widget.hide_dialog_spinner();
            feedback_widget.show_toast({
                text: channel.xhr_error_message(
                    $t({defaultMessage: "Could not create the workspace."}),
                    xhr,
                ),
                variant: "error",
                on_retry: submit,
            });
        },
    });
}

export function open(): void {
    selected_color = 0;
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "Create new workspace"}),
        modal_content_html: render_workspace_create_modal({
            colors: COLORS.map((color, index) => ({
                index,
                hex: color.hex,
                label: color.label(),
            })),
        }),
        sub_text: $t({
            defaultMessage:
                "One workspace for one company or client. Rooms, agents, and Drive do not mix.",
        }),
        modal_submit_button_text: $t({defaultMessage: "Create workspace"}),
        id: "workspace_create_modal",
        loading_spinner: true,
        on_click: submit,
        on_shown() {
            $("#workspace_create_name").trigger("focus");
        },
        post_render() {
            $("#workspace_create_name").on("input", update_slug_preview);
            $(".workspace-create-color-pill").on("click", function (this: HTMLElement) {
                selected_color = Number($(this).attr("data-color-index"));
                update_color_selection();
            });
            update_slug_preview();
            update_color_selection();
        },
    });
}
