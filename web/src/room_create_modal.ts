import $ from "jquery";
import * as z from "zod/mini";

import render_room_create_modal from "../templates/room_create_modal.hbs";

import * as channel from "./channel.ts";
import * as channel_folders from "./channel_folders.ts";
import * as dialog_widget from "./dialog_widget.ts";
import * as feedback_widget from "./feedback_widget.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as sidebar_targets from "./sidebar_targets.ts";

// The "New room" modal, opened by the "+" next to ROOMS. A room is a
// channel. The type decides which channel folder the room goes into,
// which is the group that the sidebar shows it in.

type RoomType = "project" | "client" | "team";

// The folder names are the ones that the workspace setup creates.
const ROOM_TYPES: readonly {key: RoomType; label: () => string; folder_name: string}[] = [
    {key: "project", label: () => $t({defaultMessage: "Project"}), folder_name: "Proyek"},
    {key: "client", label: () => $t({defaultMessage: "Client"}), folder_name: "Klien"},
    {key: "team", label: () => $t({defaultMessage: "Team"}), folder_name: "Tim"},
];

const stream_id_response_schema = z.object({stream_id: z.number()});

let selected_type: RoomType = "project";

// "Ramadan Campaign" becomes "ramadan-campaign".
export function room_name_for(input: string, fallback: string): string {
    return input.trim().toLowerCase().replaceAll(/\s+/g, "-") || fallback;
}

export function folder_id_for_type(
    type: RoomType,
    folders: readonly {id: number; name: string}[],
): number | undefined {
    const folder_name = ROOM_TYPES.find((room_type) => room_type.key === type)!.folder_name;
    return folders.find((folder) => folder.name === folder_name)?.id;
}

function update_type_selection(): void {
    $(".room-create-type-pill").each(function (this: HTMLElement) {
        const is_selected = $(this).attr("data-room-type") === selected_type;
        $(this).toggleClass("selected", is_selected).attr("aria-checked", String(is_selected));
    });
    $(".room-create-due-date").toggleClass("hidden", selected_type !== "project");
}

// The room exists at this point. Tell the room about its due date and
// let Kaki post the first message, then open the room.
function set_up_room(name: string, due_date: string): void {
    channel.get({
        url: "/json/get_stream_id",
        data: {stream: name},
        success(raw_data) {
            const {stream_id} = stream_id_response_schema.parse(raw_data);
            const data: Record<string, string> = {announce: JSON.stringify(true)};
            if (due_date !== "") {
                data["due_date"] = JSON.stringify(due_date);
            }
            channel.patch({
                url: `/json/streams/${stream_id}/meta`,
                data,
                success() {
                    window.location.hash = hash_util.by_stream_url(stream_id).slice(1);
                },
                error() {
                    feedback_widget.show_toast({
                        text: $t({defaultMessage: "Could not finish setting up the room."}),
                        variant: "error",
                        on_retry() {
                            set_up_room(name, due_date);
                        },
                    });
                },
            });
        },
        error() {
            feedback_widget.show_toast({
                text: $t({defaultMessage: "Could not finish setting up the room."}),
                variant: "error",
                on_retry() {
                    set_up_room(name, due_date);
                },
            });
        },
    });
}

function submit(): void {
    const name = room_name_for(
        $<HTMLInputElement>("#room_create_name").val() ?? "",
        $t({defaultMessage: "new-room"}),
    );
    const due_date =
        selected_type === "project"
            ? ($<HTMLInputElement>("#room_create_due_date").val() ?? "")
            : "";
    const folder_id = folder_id_for_type(selected_type, channel_folders.get_channel_folders());

    const data: Record<string, string> = {
        subscriptions: JSON.stringify([{name}]),
        invite_only: JSON.stringify(false),
        is_web_public: JSON.stringify(false),
        history_public_to_subscribers: JSON.stringify(true),
    };
    if (folder_id !== undefined) {
        data["folder_id"] = JSON.stringify(folder_id);
    }

    // A retry from the error toast comes here without a click, so the
    // spinner needs to start here as well. Showing it twice is harmless.
    dialog_widget.show_dialog_spinner();
    void channel.post({
        url: "/json/users/me/subscriptions",
        data,
        success() {
            // The modal fades out with its spinner on (see dialog_widget.ts).
            dialog_widget.close();
            feedback_widget.show_toast({
                text: $t({defaultMessage: "Room # {name} created."}, {name}),
            });
            set_up_room(name, due_date);
        },
        error(xhr) {
            dialog_widget.hide_dialog_spinner();
            feedback_widget.show_toast({
                text: channel.xhr_error_message(
                    $t({defaultMessage: "Could not create the room."}),
                    xhr,
                ),
                variant: "error",
                on_retry: submit,
            });
        },
    });
}

// The card sends the person to the Today page, where Kaki builds the
// room from a written brief.
function ask_kaki(): void {
    dialog_widget.close();
    const hash = sidebar_targets.get_hash("home");
    if (hash !== undefined) {
        window.location.hash = hash.slice(1);
    }
}

export function open(): void {
    selected_type = "project";
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "New room"}),
        sub_text: $t({
            defaultMessage:
                "Every room has an owner. Kaki reminds the owner to archive a project room after its due date.",
        }),
        modal_content_html: render_room_create_modal({
            show_kaki_card: sidebar_targets.is_built("home"),
            room_types: ROOM_TYPES.map((room_type) => ({
                key: room_type.key,
                label: room_type.label(),
            })),
        }),
        modal_submit_button_text: $t({defaultMessage: "Create room"}),
        id: "room_create_modal",
        loading_spinner: true,
        on_click: submit,
        on_shown() {
            $("#room_create_name").trigger("focus");
        },
        post_render() {
            $(".room-create-kaki-card").on("click", ask_kaki);
            $(".room-create-type-pill").on("click", function (this: HTMLElement) {
                const room_type = ROOM_TYPES.find(
                    (type) => type.key === $(this).attr("data-room-type"),
                );
                if (room_type !== undefined) {
                    selected_type = room_type.key;
                    update_type_selection();
                }
            });
            update_type_selection();
        },
    });
}
