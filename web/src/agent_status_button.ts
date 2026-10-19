import $ from "jquery";

import render_agent_status_button from "../templates/agent_status_button.hbs";

import * as agent_avatars from "./agent_avatars.ts";
import * as agent_status_data from "./agent_status_data.ts";
import * as agent_status_drawer from "./agent_status_drawer.ts";
import type {Filter} from "./filter.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import {page_params} from "./page_params.ts";

/*

A 1:1 direct message with an agent shows a bar with a Status button, and
the number of items that wait on the user. The button opens the Status
drawer of the agent. A group, a person and a regular bot get no bar. The
bar goes into #room-header, which a channel narrow fills with its own
header instead.

*/

type Shown = {profile_id: string; agent_name: string};

let shown: Shown | undefined;

function button_label(count: number | undefined): string {
    if (count === undefined || count === 0) {
        return $t({defaultMessage: "Status"});
    }
    return $t(
        {defaultMessage: "Status, {count, plural, one {# item} other {# items}} waiting on you"},
        {count},
    );
}

function update_count(profile_id: string): void {
    const count = agent_status_data.waiting_count(profile_id);
    const $button = $("#room-header .sj-agent-bar__status");
    $button.attr("aria-label", button_label(count));
    $button
        .find(".sj-agent-bar__count")
        .text(count ?? 0)
        .toggleClass("hide", count === undefined || count === 0);
}

function render(current: Shown): void {
    const count = agent_status_data.waiting_count(current.profile_id);
    $("#room-header").html(
        render_agent_status_button({
            agent_name: current.agent_name,
            button_label: button_label(count),
            has_count: count !== undefined && count > 0,
            count,
        }),
    );
}

export function hide(): void {
    if (shown === undefined) {
        return;
    }
    shown = undefined;
    agent_status_data.watch(undefined);
    $("#room-header").empty();
}

export function show(filter: Filter | undefined): void {
    const other_user_ids = narrow_state.pm_ids(filter);
    const profile_id = agent_status_data.dm_agent_profile_id(
        other_user_ids,
        agent_avatars.get_profile_id_for_bot_user_id,
    );
    if (profile_id === undefined || other_user_ids === undefined) {
        hide();
        return;
    }
    const agent_name = agent_avatars.get_agent_for_bot_user_id(other_user_ids[0]!)?.name ?? "";
    if (shown?.profile_id === profile_id && $("#room-header .sj-agent-bar").length > 0) {
        return;
    }
    shown = {profile_id, agent_name};
    agent_status_data.watch(profile_id);
    render(shown);
    agent_status_data.load(profile_id);
}

export function initialize(): void {
    // A spectator cannot read the agent directory, so no conversation of
    // theirs has the bar.
    if (page_params.is_spectator) {
        return;
    }
    agent_status_data.initialize();
    agent_status_data.on_change((profile_id) => {
        if (shown?.profile_id === profile_id) {
            update_count(profile_id);
        }
    });
    // The agent directory loads after the first narrow, so the bar of a
    // conversation that was already open comes when the directory does.
    agent_avatars.on_loaded(() => {
        if (narrow_state.is_message_feed_visible()) {
            show(narrow_state.filter());
        }
    });
    $("#room-header").on("click", ".sj-agent-bar__status", () => {
        if (shown !== undefined) {
            agent_status_drawer.open(shown.profile_id, shown.agent_name);
        }
    });
}
