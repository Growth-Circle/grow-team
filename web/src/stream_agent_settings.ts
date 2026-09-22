/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal, promise/prefer-await-to-then, promise/always-return -- Channel controls use static jQuery markup and section-bound callbacks. */
import $ from "jquery";

import * as api from "./agent_api.ts";

let initialized = false;
function stream_id(section: JQuery): number {
    return Number(section.closest(".subscription_settings").attr("data-stream-id"));
}
function still_open(section: JQuery, id: number): boolean {
    return (
        section.closest(".subscription_settings").attr("data-stream-id") === String(id) &&
        section.closest(".subscription_settings").is(":visible")
    );
}
export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    const overlay = $("#channels_overlay_container");
    overlay.on("click", ".stream-agent-load", function () {
        const section = $(this).closest(".stream-agent-settings");
        const id = stream_id(section);
        if (!id) {
            return;
        }
        section.find(".stream-agent-status").text("Loading channel agents…");
        void Promise.all([
            api.list_channel_attachments(id),
            api.list_profiles({offset: 0, limit: 100, access: "complete"}),
        ])
            .then(([attachments, candidates]) => {
                if (!still_open(section, id)) {
                    return;
                }
                const list = section.find(".stream-agent-list").empty();
                if (attachments.attachments.length === 0) {
                    $("<p>").text("No visible agents are attached.").appendTo(list);
                }
                for (const item of attachments.attachments) {
                    const row = $("<div class='agent-card'>").appendTo(list);
                    $("<strong>")
                        .text(`${item.profile.name} · ${item.profile.owner.name}`)
                        .appendTo(row);
                    $("<p>")
                        .text(
                            `Bot membership: ${item.bot_member ? "yes" : "no"}; profile access: ${item.profile.access.complete ? "complete" : "partial"}`,
                        )
                        .appendTo(row);
                }
                const select = section.find("#stream-agent-profile").empty();
                for (const profile of candidates.profiles.filter((item) =>
                    item.allowed_actions.includes("edit"),
                )) {
                    $("<option>")
                        .val(`${profile.id}:${profile.revision}`)
                        .text(`${profile.name} · ${profile.owner.name}`)
                        .appendTo(select);
                }
                section.find(".stream-agent-attach").prop("hidden", select.children().length === 0);
                section
                    .find(".stream-agent-status")
                    .text(`${attachments.attachments.length} attached agents visible.`);
            })
            .catch(() => {
                if (still_open(section, id)) {
                    section
                        .find(".stream-agent-status")
                        .text("Channel agent status is unavailable.");
                }
            });
    });
    overlay.on("submit", ".stream-agent-attach", function (event) {
        event.preventDefault();
        const section = $(this).closest(".stream-agent-settings");
        const id = stream_id(section);
        const [profile_id, revision] = String(
            section.find("#stream-agent-profile").val() ?? "",
        ).split(":");
        if (!id || !profile_id || !revision) {
            return;
        }
        section.find(".stream-agent-status").text("Attaching agent…");
        void api
            .attach_channel(profile_id, id, Number(revision))
            .then(() => {
                if (still_open(section, id)) {
                    section
                        .find(".stream-agent-status")
                        .text("Channel attachment saved. Existing profile setup remains saved.");
                    section.find(".stream-agent-load").trigger("click");
                }
            })
            .catch(() => {
                if (still_open(section, id)) {
                    section
                        .find(".stream-agent-status")
                        .text("Attachment failed. Profile setup remains saved.");
                }
            });
    });
}
