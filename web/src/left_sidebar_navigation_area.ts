import $ from "jquery";
import _ from "lodash";

import * as command_palette from "./command_palette.ts";
import * as common from "./common.ts";
import * as drafts from "./drafts.ts";
import type {Filter} from "./filter.ts";
import {$t} from "./i18n.ts";
import * as live_updates from "./live_updates.ts";
import {localstorage} from "./localstorage.ts";
import * as message_reminder from "./message_reminder.ts";
import * as nav_counts from "./nav_counts.ts";
import * as navigation_views from "./navigation_views.ts";
import {page_params} from "./page_params.ts";
import * as people from "./people.ts";
import * as resize from "./resize.ts";
import * as scheduled_messages from "./scheduled_messages.ts";
import * as settings_config from "./settings_config.ts";
import * as sidebar_rooms from "./sidebar_rooms.ts";
import * as sidebar_targets from "./sidebar_targets.ts";
import {type NarrowTerm, current_user} from "./state_data.ts";
import * as ui_util from "./ui_util.ts";
import * as unread from "./unread.ts";
import * as workspace_switcher from "./workspace_switcher.ts";

let last_mention_count = 0;
const ls_key = "left_sidebar_views_state";
const ls = localstorage();

const STATES = {
    EXPANDED: "expanded",
    CONDENSED: "condensed",
};

export function restore_views_state(): void {
    if (page_params.is_spectator) {
        // Spectators should always see the expanded view.
        return;
    }

    const views_state = ls.get(ls_key);
    // Expanded state is default, so we only need to toggle if the state is condensed.
    if (views_state === STATES.CONDENSED) {
        toggle_condensed_navigation_area();
    }
}

function save_state(state: string): void {
    ls.set(ls_key, state);
}

export function update_starred_count(count: number, hidden: boolean): void {
    const $starred_li = $(".top_left_starred_messages");
    ui_util.update_unread_count_in_dom($starred_li, count);

    if (hidden) {
        $starred_li.addClass("hide_starred_message_count");
        return;
    }
    $starred_li.removeClass("hide_starred_message_count");
}

export function update_scheduled_messages_row(): void {
    const $scheduled_li = $(".top_left_scheduled_messages");
    const count = scheduled_messages.get_count();
    $scheduled_li.toggleClass("hidden-by-filters", count === 0);
    ui_util.update_unread_count_in_dom($scheduled_li, count);
}

export function update_reminders_row(): void {
    const $reminders_li = $(".top_left_reminders");
    const count = message_reminder.get_count();
    $reminders_li.toggleClass("hidden-by-filters", count === 0);
    ui_util.update_unread_count_in_dom($reminders_li, count);
}

export let update_dom_with_unread_counts = function (
    counts: unread.FullUnreadCountsData,
    skip_animations: boolean,
): void {
    // Note that direct message counts are handled in pm_list.ts.

    // mentioned/home views have simple integer counts
    const $mentioned_li = $(".top_left_mentions");
    const $home_view_li = $(".selected-home-view");
    const $condensed_view_li = $(".top_left_condensed_unread_marker");

    ui_util.update_unread_count_in_dom($mentioned_li, counts.mentioned_message_count);
    ui_util.update_unread_count_in_dom($home_view_li, counts.home_unread_messages);
    ui_util.update_unread_count_in_dom($condensed_view_li, counts.home_unread_messages);

    if (!skip_animations) {
        animate_unread_changes($mentioned_li, counts.mentioned_message_count, last_mention_count);
    }

    last_mention_count = counts.mentioned_message_count;
};

export function rewire_update_dom_with_unread_counts(
    value: typeof update_dom_with_unread_counts,
): void {
    update_dom_with_unread_counts = value;
}

export let select_top_left_corner_item = function (narrow_to_activate: string): void {
    $(".top-left-active-filter").removeClass("top-left-active-filter");
    if (narrow_to_activate !== "") {
        $(narrow_to_activate).addClass("top-left-active-filter");
    }
};

export function rewire_select_top_left_corner_item(
    func: (narrow_to_activate: string) => void,
): void {
    select_top_left_corner_item = func;
}

export function handle_narrow_activated(filter: Filter): void {
    let terms: NarrowTerm[];
    let filter_name: NarrowTerm["operand"];

    // TODO: handle confused filters like "in:all stream:foo"
    terms = filter.terms_with_operator("in");
    if (terms[0] !== undefined) {
        filter_name = terms[0].operand;
        if (filter_name === "home") {
            highlight_all_messages_view();
            return;
        }
    }
    terms = filter.terms_with_operator("is");
    if (terms[0] !== undefined) {
        filter_name = terms[0].operand;
        if (filter_name === "starred") {
            select_top_left_corner_item(".top_left_starred_messages");
            return;
        } else if (filter_name === "mentioned") {
            select_top_left_corner_item(".top_left_mentions");
            return;
        }
    }
    const term_types = filter.sorted_term_types();
    if (
        _.isEqual(term_types, ["sender", "has-reaction"]) &&
        filter.terms_with_operator("sender")[0]!.operand === people.my_current_user_id()
    ) {
        select_top_left_corner_item(".top_left_my_reactions");
        return;
    }

    // If we don't have a specific handler for this narrow, we just clear all.
    select_top_left_corner_item("");
}

export function expand_views($views_label_container: JQuery, $views_label_icon: JQuery): void {
    $views_label_container.addClass("showing-expanded-navigation");
    $views_label_container.removeClass("showing-condensed-navigation");
    $views_label_icon.addClass("rotate-icon-down");
    $views_label_icon.removeClass("rotate-icon-right");
}

export function collapse_views($views_label_container: JQuery, $views_label_icon: JQuery): void {
    $views_label_container.addClass("showing-condensed-navigation");
    $views_label_container.removeClass("showing-expanded-navigation");
    $views_label_icon.addClass("rotate-icon-right");
    $views_label_icon.removeClass("rotate-icon-down");
}

export function force_expand_views(): void {
    if (page_params.is_spectator) {
        // We don't support collapsing VIEWS for spectators, so exit early.
        return;
    }

    const $views_label_container = $("#views-label-container");
    const $views_label_icon = $("#toggle-top-left-navigation-area-icon");

    if ($views_label_container.hasClass("showing-condensed-navigation")) {
        expand_views($views_label_container, $views_label_icon);
        save_state(STATES.EXPANDED);
        resize.resize_stream_filters_container();
    }
}

export function force_collapse_views(): void {
    if (page_params.is_spectator) {
        // We don't support collapsing VIEWS for spectators, so exit early.
        return;
    }

    const $views_label_container = $("#views-label-container");
    const $views_label_icon = $("#toggle-top-left-navigation-area-icon");

    if ($views_label_container.hasClass("showing-expanded-navigation")) {
        collapse_views($views_label_container, $views_label_icon);
        save_state(STATES.CONDENSED);
        resize.resize_stream_filters_container();
    }
}

function toggle_condensed_navigation_area(): void {
    const $views_label_container = $("#views-label-container");

    if (page_params.is_spectator) {
        // We don't support collapsing VIEWS for spectators, so exit early.
        return;
    }

    if ($views_label_container.hasClass("showing-expanded-navigation")) {
        force_collapse_views();
    } else {
        force_expand_views();
    }
}

export function animate_unread_changes(
    $li: JQuery,
    new_count: number,
    previous_count: number,
): void {
    if (new_count > previous_count) {
        ui_util.do_new_unread_animation($li);
    }
}

// The board opens filtered from the "My tasks" and "Awaiting my review"
// rows, so the row that opened it is the one to highlight.
const TASK_BOARD_ROW_BY_FILTER: Record<string, string> = {
    mine: ".top_left_my_tasks",
    review: ".top_left_awaiting_review",
};

export function highlight_task_board_view(filter = ""): void {
    select_top_left_corner_item(TASK_BOARD_ROW_BY_FILTER[filter] ?? ".top_left_task_board");

    setTimeout(() => {
        resize.resize_stream_filters_container();
    }, 0);
}

export function highlight_inbox_view(): void {
    select_top_left_corner_item(".top_left_inbox");

    setTimeout(() => {
        resize.resize_stream_filters_container();
    }, 0);
}

export function highlight_recent_view(): void {
    select_top_left_corner_item(".top_left_recent_view");

    setTimeout(() => {
        resize.resize_stream_filters_container();
    }, 0);
}

export function highlight_all_messages_view(): void {
    select_top_left_corner_item(".top_left_all_messages");

    setTimeout(() => {
        resize.resize_stream_filters_container();
    }, 0);
}

export function get_view_rows_by_view_name(view: string): JQuery {
    if (view === settings_config.web_home_view_values.all_messages.code) {
        return $(".top_left_all_messages");
    }

    if (view === settings_config.web_home_view_values.recent.code) {
        return $(".top_left_recent_view");
    }

    return $(".top_left_inbox");
}

// Reorder <li> views elements in the DOM based on the current home_view.
// Called twice: on initial page load and when home_view changes.
export function reorder_left_sidebar_navigation_list(home_view: string): void {
    const $left_sidebar = $("#left-sidebar-navigation-list");
    const $left_sidebar_condensed = $("#left-sidebar-navigation-list-condensed");

    // Put the expanded rows back in the built-in order. Prepending in
    // reverse keeps any custom views after the built-in ones. Only the home
    // view is lifted out of that order, below; the other home-view
    // candidates keep their built-in place.
    for (const view of get_built_in_views().toReversed()) {
        $left_sidebar.children(`.top_left_${view.css_class_suffix}`).prependTo($left_sidebar);
    }

    // The condensed list holds only the home-view candidates, in their
    // default order.
    for (const key of Object.keys(settings_config.web_home_view_values).toReversed()) {
        if (key !== home_view) {
            get_view_rows_by_view_name(key).eq(0).prependTo($left_sidebar_condensed);
        }
    }

    // Lift the selected home view to the top of both lists.
    const $selected_home_view = get_view_rows_by_view_name(home_view);
    $selected_home_view.eq(1).prependTo($left_sidebar);
    $selected_home_view.eq(0).prependTo($left_sidebar_condensed);
}

export function handle_home_view_changed(new_home_view: string): void {
    const $current_home_view = $(".selected-home-view");
    const $new_home_view = get_view_rows_by_view_name(new_home_view);
    const res = unread.get_counts();

    // Remove class from current home view
    $current_home_view.removeClass("selected-home-view");

    // Add the class to the matching home view
    $new_home_view.addClass("selected-home-view");

    reorder_left_sidebar_navigation_list(new_home_view);
    update_dom_with_unread_counts(res, true);
}

export function get_built_in_primary_condensed_views(): navigation_views.BuiltInViewMetadata[] {
    function score(view: navigation_views.BuiltInViewMetadata): number {
        if (view.prioritize_in_condensed_view) {
            return 1;
        }
        return 0;
    }
    // Get the top 5 prioritized views.
    return navigation_views
        .get_built_in_views()
        .toSorted((view1, view2) => score(view2) - score(view1))
        .slice(0, 5);
    // TODO: Think about filtering out scheduled message and reminders views with UI to support less than 5 views.
}

export function get_built_in_popover_condensed_views(): navigation_views.BuiltInViewMetadata[] {
    const visible_condensed_views = get_built_in_primary_condensed_views();
    const all_views = navigation_views.get_built_in_views();
    return all_views.filter((view) => {
        if (view.fragment === "scheduled") {
            const scheduled_message_count = scheduled_messages.get_count();
            if (scheduled_message_count === 0) {
                return false;
            }
            view.unread_count = scheduled_message_count;
            return true;
        }
        if (view.fragment === "reminders") {
            const reminders_count = message_reminder.get_count();
            if (reminders_count === 0) {
                return false;
            }
            view.unread_count = reminders_count;
            return true;
        }
        if (view.fragment === "drafts") {
            view.unread_count = drafts.draft_model.getDraftCount();
        }
        // Remove views that are already visible.
        return !visible_condensed_views.some(
            (visible_view) => visible_view.fragment === view.fragment,
        );
    });
}

// The sidebar's expanded Views list only shows a short, curated
// subset of the built-in views. A hidden view keeps its route and
// stays available from the condensed row, its popover, and old
// links; only its row in this expanded list disappears.
const FRAGMENTS_HIDDEN_FROM_EXPANDED_VIEWS_LIST = new Set([
    "feed",
    "narrow/has/reaction/sender/me",
    "drafts",
    "scheduled",
    "reminders",
]);

// Frame 10a orders the expanded list as Inbox, Mentions, Recent
// conversations, Starred messages. Fragments not listed here keep
// their natural order after the ranked ones.
const EXPANDED_VIEWS_LIST_ORDER = ["inbox", "narrow/is/mentioned", "recent", "narrow/is/starred"];

export function get_built_in_views(): navigation_views.BuiltInViewMetadata[] {
    return navigation_views
        .get_built_in_views()
        .filter((view) => !FRAGMENTS_HIDDEN_FROM_EXPANDED_VIEWS_LIST.has(view.fragment))
        .toSorted((view1, view2) => {
            const rank1 = EXPANDED_VIEWS_LIST_ORDER.indexOf(view1.fragment);
            const rank2 = EXPANDED_VIEWS_LIST_ORDER.indexOf(view2.fragment);
            if (rank1 === -1 && rank2 === -1) {
                return 0;
            }
            if (rank1 === -1) {
                return 1;
            }
            if (rank2 === -1) {
                return -1;
            }
            return rank1 - rank2;
        });
}

export function initialize(): void {
    update_reminders_row();
    update_scheduled_messages_row();
    restore_views_state();

    initialize_sanji_sidebar();
}

// The sidebar (map-mockup-a.md section 4.2): workspace switcher, search
// button, the navigation entries, rooms, and the user card.

const NAV_COUNT_IDS: nav_counts.NavCountId[] = ["needs", "tasks", "mcp", "runners"];

const NAV_IDS: sidebar_targets.TargetId[] = [
    "home",
    "needs",
    "tasks",
    "agents",
    "routines",
    "playbooks",
    "mcp",
    "runners",
    "drive",
];

// The pages for setting up agents and what they do on their own. They
// sit in one group that stays closed until the person opens it.
const AUTOMATION_GROUP_ITEM_IDS: sidebar_targets.TargetId[] = [
    "agents",
    "routines",
    "playbooks",
    "mcp",
    "runners",
];
const AUTOMATION_GROUP_COUNT_IDS: nav_counts.NavCountId[] = ["mcp", "runners"];
const automation_group_ls_key = "left_sidebar_automation_group_expanded";

let shown_nav_ids = new Set<sidebar_targets.TargetId>();

export type NavGroupState = {
    visible: boolean;
    expanded: boolean;
    // The count of what waits in the items, for the dot on a closed group.
    attention_count: number;
};

// The group is hidden when none of its items is shown. A page inside the
// group keeps it open, whatever the saved choice is, so the highlighted
// entry is never out of sight.
export function get_nav_group_state(opts: {
    item_ids: readonly sidebar_targets.TargetId[];
    shown_ids: ReadonlySet<sidebar_targets.TargetId>;
    active_id: sidebar_targets.TargetId | undefined;
    saved_expanded: boolean;
    attention_count: number;
}): NavGroupState {
    const visible = opts.item_ids.some((id) => opts.shown_ids.has(id));
    const has_active_page =
        opts.active_id !== undefined &&
        opts.item_ids.includes(opts.active_id) &&
        opts.shown_ids.has(opts.active_id);
    return {
        visible,
        expanded: visible && (has_active_page || opts.saved_expanded),
        attention_count: opts.attention_count,
    };
}

function update_nav_badge(id: nav_counts.NavCountId): void {
    const $li = $(`.sanji-nav-item[data-nav-id="${id}"]`);
    ui_util.update_unread_count_in_dom($li, nav_counts.get_count(id));
}

export function update_automation_group(): void {
    const active_id = sidebar_targets.id_for_hash(window.location.hash);
    const attention_count = AUTOMATION_GROUP_COUNT_IDS.filter((id) => shown_nav_ids.has(id))
        .map((id) => nav_counts.get_count(id))
        .reduce((total, count) => total + count, 0);
    const state = get_nav_group_state({
        item_ids: AUTOMATION_GROUP_ITEM_IDS,
        shown_ids: shown_nav_ids,
        active_id,
        saved_expanded: ls.get(automation_group_ls_key) === true,
        attention_count,
    });
    const $group = $('.sanji-nav-group[data-nav-group="automation"]');
    const show_dot = state.visible && !state.expanded && state.attention_count > 0;
    $group.toggleClass("hidden", !state.visible);
    $group.find(".sanji-nav-group-toggle").attr("aria-expanded", String(state.expanded));
    $group.find(".sanji-nav-group-list").prop("hidden", !state.expanded);
    $group
        .find(".sanji-nav-group-attention")
        .toggleClass("hide", !show_dot)
        .attr(
            "aria-label",
            show_dot
                ? $t({defaultMessage: "Needs attention ({count})"}, {count: state.attention_count})
                : "",
        );
}

// A click saves the choice for the next time the group is closed or
// open by hand. While the page is inside the group, it stays open.
export function toggle_automation_group(): void {
    ls.set(automation_group_ls_key, ls.get(automation_group_ls_key) !== true);
    update_automation_group();
}

export function update_sanji_nav_active_state(): void {
    let active_id = sidebar_targets.id_for_hash(window.location.hash);
    // "My tasks" is a tab of the Tasks page, with no row of its own.
    if (active_id === "my_tasks") {
        active_id = "tasks";
    }
    $(".sanji-nav-item").removeClass("sanji-nav-item-active").removeAttr("aria-current");
    if (active_id !== undefined) {
        $(`.sanji-nav-item[data-nav-id="${active_id}"]`)
            .addClass("sanji-nav-item-active")
            .attr("aria-current", "page");
    }
    update_automation_group();
}

// Shows each entry whose page is built (or has a fallback), and points it
// at that page.
export function update_nav_targets(): void {
    const role = sidebar_targets.role_of(current_user);
    const can_use_playbooks = sidebar_targets.can_use_playbooks(role);
    const can_use_routines = sidebar_targets.can_use_routines(role);
    shown_nav_ids = new Set();
    for (const id of NAV_IDS) {
        const hash = sidebar_targets.get_hash(id);
        const shown =
            hash !== undefined &&
            (id !== "playbooks" || can_use_playbooks) &&
            (id !== "routines" || can_use_routines);
        const $li = $(`.sanji-nav-item[data-nav-id="${id}"]`);
        $li.toggleClass("hidden", !shown);
        if (shown) {
            shown_nav_ids.add(id);
        }
        if (hash !== undefined) {
            $li.find("a").attr("href", hash);
        }
    }
    // The search slot holds the palette button. Until the palette is
    // ready, it holds the filter for rooms instead.
    const palette_is_ready = sidebar_targets.is_shown("search");
    $("#left-sidebar-command-search").toggleClass("hidden", !palette_is_ready);
    $("#left-sidebar-search").toggleClass("hidden", palette_is_ready);
    update_sanji_nav_active_state();
    resize.resize_stream_filters_container();
}

function initialize_sanji_sidebar(): void {
    workspace_switcher.initialize();
    sidebar_rooms.initialize();

    update_nav_targets();
    live_updates.on("realm_permissions", update_nav_targets);

    // A count that no page has set yet must not hide a badge that the
    // task board already filled in.
    for (const id of NAV_COUNT_IDS) {
        if (nav_counts.get_count(id) > 0) {
            update_nav_badge(id);
        }
    }
    nav_counts.on_change((id) => {
        update_nav_badge(id);
        update_automation_group();
    });

    window.addEventListener("hashchange", update_sanji_nav_active_state);

    $("#left-sidebar-command-search-hint").text(
        sidebar_targets.search_shortcut_hint(common.has_mac_keyboard()),
    );
    $("body").on("click", "#left-sidebar-command-search", () => {
        command_palette.open();
    });
    $("body").on("click", ".sanji-nav-group-toggle", toggle_automation_group);
}
