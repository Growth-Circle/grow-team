/* eslint-disable no-jquery/variable-pattern, no-jquery/no-parse-html-literal -- This controller uses static jQuery markup and fenced delegated callbacks. */
import $ from "jquery";

import * as api from "./agent_api.ts";
import {presence_label} from "./agent_settings_labels.ts";
import {$t} from "./i18n.ts";
import {
    announce,
    current,
    line,
    option,
    render_grants,
    runner_label,
    value,
    visit,
} from "./settings_agents_core.ts";
import {current_user} from "./state_data.ts";

let default_draft_revision = 0;
let default_dirty = false;
let default_expected_revision: number | undefined;
// The revision the team instructions editor last loaded, sent back as
// expected_revision so a concurrent edit is caught as a stale save.
let team_instructions_revision = 0;
let default_request = 0;

// Called from settings_agents.ts's show_tab() on every tab switch, so a
// stale draft from a previous visit never lingers into a new one.
export function clear_default_draft(): void {
    default_dirty = false;
    default_expected_revision = undefined;
}

export function reset_state(): void {
    clear_default_draft();
    team_instructions_revision = 0;
}

// Pure-ish: toggles the edit form versus the read-only text under $panel
// (the settings page's own "Team default" section) from already-fetched
// data, using $panel.find() instead of a document-wide lookup so a caller
// that nests the same two ids under its own container still works.
export function render_team_instructions(
    $panel: JQuery,
    data: {text: string; revision: number; allowed_actions: string[]} | undefined,
): void {
    const can_edit = data?.allowed_actions.includes("edit") ?? false;
    team_instructions_revision = data?.revision ?? 0;
    $panel.find("#agent-team-instructions-form").prop("hidden", !can_edit);
    $panel.find("#agent-team-instructions-readonly").prop("hidden", can_edit || !data);
    if (can_edit) {
        $panel.find("#agent-team-instructions").val(data?.text ?? "");
    } else if (data) {
        $panel
            .find("#agent-team-instructions-readonly-text")
            .text(data.text || $t({defaultMessage: "No team instructions are set."}));
    }
}
export async function load_default(): Promise<void> {
    const token = visit;
    default_request += 1;
    const request = default_request;
    try {
        const [setting, candidates, instructions] = await Promise.all([
            api.get_team_default(),
            api.list_profiles({offset: 0, limit: 100, access: "complete"}),
            // A member always has read access (contract 7.2); a failure here
            // is a transient status, not a reason to fail the whole panel.
            api.get_team_instructions().catch(() => undefined),
        ]);
        if (!current(token) || request !== default_request) {
            return;
        }
        render_team_instructions($("#agent-default-panel"), instructions?.team_instructions);
        const data = setting.default;
        const status = $("#agent-team-default").empty();
        if (data.profile) {
            if (data.profile.desired_state === "archived") {
                // Enable rejects an archived profile, so the admin needs a
                // replacement, not an instruction to turn this one back on.
                line(
                    status,
                    $t({defaultMessage: "Selection"}),
                    $t({
                        defaultMessage:
                            "The saved default agent is archived and cannot run tasks. Choose a replacement below.",
                    }),
                );
            } else if (
                data.profile.desired_state === "paused" ||
                data.profile.readiness_state !== "ready"
            ) {
                line(
                    status,
                    $t({defaultMessage: "Selection"}),
                    $t({
                        defaultMessage:
                            "The saved default agent is paused or needs a new check. It cannot take new tasks. Choose a replacement below.",
                    }),
                );
            }
            line(
                status,
                $t({defaultMessage: "Selected profile"}),
                `${data.profile.name} · ${data.profile.owner.name}`,
            );
            line(
                status,
                $t({defaultMessage: "Runner presence"}),
                presence_label(data.profile.runner?.observed_presence ?? "unknown"),
            );
            line(
                status,
                $t({defaultMessage: "Audience"}),
                $t({
                    defaultMessage:
                        "Recorded grants below show only identities you may view. Each member still needs all resource grants.",
                }),
            );
            if (data.profile.runner?.observed_presence === "offline") {
                line(
                    status,
                    $t({defaultMessage: "Availability"}),
                    $t({defaultMessage: "Valid default; work may queue while runner is offline."}),
                );
            }
        } else {
            line(
                status,
                $t({defaultMessage: "Selection"}),
                data.has_default && current_user.is_admin
                    ? $t({
                          defaultMessage:
                              "The team default agent is not visible to you. You can clear it.",
                      })
                    : $t({defaultMessage: "No team default agent is available to you."}),
            );
        }
        const form = $("#agent-default-form");
        form.prop("hidden", data.selection_revision === undefined);
        if (!default_dirty) {
            default_expected_revision = data.selection_revision;
        } else if (default_expected_revision !== data.selection_revision) {
            line(
                status,
                $t({defaultMessage: "Conflict"}),
                $t({
                    defaultMessage:
                        "The saved selection changed. Keep your choice and refresh before saving.",
                }),
            );
        }
        const chosen = default_dirty ? value("#agent-default-choice") : (data.profile?.id ?? "");
        const select = $("#agent-default-choice").empty();
        option(select, "", $t({defaultMessage: "No default"}));
        for (const profile of candidates.profiles.filter(
            (item) => item.desired_state === "enabled" && item.readiness_state === "ready",
        )) {
            option(
                select,
                profile.id,
                `${profile.name} · ${profile.owner.name} · ${runner_label(profile.runner)}`,
            );
        }
        if (
            chosen &&
            select.find("option").filter((_, item) => item.value === chosen).length === 0
        ) {
            option(
                select,
                chosen,
                $t({
                    defaultMessage:
                        "Your unsaved selection is no longer in the visible candidate list",
                }),
            );
        }
        select.val(chosen);
        $("#agent-default-clear").prop(
            "hidden",
            !data.has_default || !data.allowed_actions?.includes("clear"),
        );
        line(status, $t({defaultMessage: "Visible candidate count"}), candidates.count);
        if (data.profile) {
            try {
                const grants = await api.list_grants("profile", data.profile.id);
                if (current(token) && request === default_request) {
                    const audience = $("<div class='agent-cards'>").appendTo(status);
                    render_grants(audience, grants);
                }
            } catch {
                if (current(token) && request === default_request) {
                    line(
                        status,
                        $t({defaultMessage: "Audience"}),
                        $t({defaultMessage: "Grant details are unavailable or restricted."}),
                    );
                }
            }
        }
    } catch {
        if (current(token) && request === default_request) {
            $("#agent-team-default").text(
                $t({defaultMessage: "Team default status is unknown. Retry this panel."}),
            );
            $("#agent-default-form").prop("hidden", true);
            $("#agent-team-instructions-form, #agent-team-instructions-readonly").prop(
                "hidden",
                true,
            );
        }
    }
}
export async function save_default(profile_id: string | null): Promise<void> {
    const token = visit;
    const revision = default_expected_revision;
    const draft = default_draft_revision;
    if (!revision) {
        return;
    }
    try {
        await api.update_team_default(revision, profile_id);
        if (!current(token)) {
            return;
        }
        if (draft === default_draft_revision) {
            default_dirty = false;
            announce($t({defaultMessage: "Team default saved. Resource access is unchanged."}));
        } else {
            announce(
                $t({
                    defaultMessage: "Earlier default saved. Your newer selection remains unsaved.",
                }),
            );
        }
        await load_default();
    } catch (error) {
        if (current(token) && draft === default_draft_revision) {
            announce(
                api.agent_error_code(error) === "audience_grant_required"
                    ? $t({
                          defaultMessage:
                              "Share this agent with a group before you make it the team default.",
                      })
                    : $t({
                          defaultMessage:
                              "Team default changed or is unavailable. Your selection remains. Refresh before trying again.",
                      }),
            );
        }
    }
}
export async function save_team_instructions(): Promise<void> {
    const token = visit;
    const revision = team_instructions_revision;
    const text = value("#agent-team-instructions");
    $("#agent-team-instructions-status").text($t({defaultMessage: "Saving…"}));
    try {
        const result = await api.update_team_instructions({expected_revision: revision, text});
        if (!current(token)) {
            return;
        }
        render_team_instructions($("#agent-default-panel"), result.team_instructions);
        $("#agent-team-instructions-status").text($t({defaultMessage: "Team instructions saved."}));
    } catch (error) {
        if (!current(token)) {
            return;
        }
        const code = api.agent_error_code(error);
        $("#agent-team-instructions-status").text(
            code === "team_instructions_stale"
                ? $t({
                      defaultMessage:
                          "Someone else changed the team instructions. Reload them before you save.",
                  })
                : code === "instructions_rejected"
                  ? $t({
                        defaultMessage:
                            "Remove passwords, tokens, and keys from the instructions, then save again.",
                    })
                  : $t({
                        defaultMessage:
                            "Team instructions were not saved. Check the text and retry.",
                    }),
        );
    }
}

export function bind_handlers(): void {
    const root = $(document);
    root.on("submit", "#agent-default-form", (event) => {
        event.preventDefault();
        void save_default(value("#agent-default-choice") || null);
    });
    root.on("submit", "#agent-team-instructions-form", (event) => {
        event.preventDefault();
        void save_team_instructions();
    });
    root.on("click", "#agent-default-clear", () => {
        $("#agent-default-choice").val("");
        default_draft_revision += 1;
        default_dirty = true;
        void save_default(null);
    });
    root.on("change", "#agent-default-choice", () => {
        default_draft_revision += 1;
        default_dirty = true;
    });
}
