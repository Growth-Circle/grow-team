import $ from "jquery";

import render_new_stream_users from "../templates/stream_settings/new_stream_users.hbs";

import * as add_subscribers_pill from "./add_subscribers_pill.ts";
import {$t} from "./i18n.ts";
import * as people from "./people.ts";
import {current_user} from "./state_data.ts";
import * as stream_create_subscribers_data from "./stream_create_subscribers_data.ts";
import type {CombinedPillContainer} from "./typeahead_helper.ts";
import * as user_groups from "./user_groups.ts";
import * as user_pill from "./user_pill.ts";

export let pill_widget: CombinedPillContainer;

export function get_principals(): number[] {
    return stream_create_subscribers_data.get_principals();
}

function update_member_count(): void {
    $("#create_stream_members_count").text(
        $t(
            {defaultMessage: "{N, plural, one {# person} other {# people}}"},
            {N: stream_create_subscribers_data.get_principals().length},
        ),
    );
}

function add_user_ids(user_ids: number[]): void {
    stream_create_subscribers_data.add_user_ids(user_ids);
    update_member_count();
}

function sync_user_ids(user_ids: number[]): void {
    stream_create_subscribers_data.sync_user_ids(user_ids);
    update_member_count();
}

function build_pill_widget({
    $parent_container,
}: {
    $parent_container: JQuery;
}): CombinedPillContainer {
    const $pill_container = $parent_container.find(".pill-container");
    const get_potential_subscribers = stream_create_subscribers_data.get_potential_subscribers;
    const get_user_groups = user_groups.get_all_realm_user_groups;
    return add_subscribers_pill.create({
        $pill_container,
        get_potential_subscribers,
        get_user_groups,
        with_add_button: false,
        onPillCreateAction: add_user_ids,
        // It is better to sync the current set of user ids in the input
        // instead of removing user_ids from the user_ids_set, otherwise
        // we'll have to have more complex logic of when to remove
        // a user and when not to depending upon their group, channel
        // and individual pills.
        onPillRemoveAction: sync_user_ids,
    });
}

export function add_all_team(): void {
    for (const user of stream_create_subscribers_data.get_team_members_not_yet_added()) {
        user_pill.append_user(user, pill_widget);
    }
}

export function create_handlers($container: JQuery): void {
    $container.on("click", "#create_stream_add_all_team", (e) => {
        e.preventDefault();
        add_all_team();
    });
}

export function build_widgets(): void {
    const $add_people_container = $("#people_to_add");
    $add_people_container.html(render_new_stream_users({}));

    pill_widget = build_pill_widget({$parent_container: $add_people_container});
    // The search box is a contenteditable element with only a
    // placeholder, which screen readers do not announce as a name.
    $add_people_container.find(".pill-container .input").attr({
        role: "textbox",
        "aria-label": $t({defaultMessage: "Members"}),
        "data-placeholder": $t({defaultMessage: "Type a name…"}),
    });

    stream_create_subscribers_data.initialize_with_current_user();
    user_pill.append_user(people.get_by_user_id(current_user.user_id), pill_widget);

    $("#create_stream_add_all_team").text(
        $t(
            {defaultMessage: "+ All team ({N})"},
            {N: stream_create_subscribers_data.get_team_size()},
        ),
    );
    update_member_count();
}

export function add_user_id_to_new_stream(user_id: number): void {
    // This is only used by puppeteer tests.
    user_pill.append_user(people.get_by_user_id(user_id), pill_widget);
}
