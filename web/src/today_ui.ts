import $ from "jquery";

import * as compose_closed_ui from "./compose_closed_ui.ts";
import {$t} from "./i18n.ts";
import * as left_sidebar_navigation_area from "./left_sidebar_navigation_area.ts";
import * as views_util from "./views_util.ts";

// Placeholder for the Today view (#today, which is also the start page).
// The full screen replaces this file and keeps these four exports.

let visible = false;

function set_visible(value: boolean): void {
    visible = value;
}

export function initialize(): void {
    // Nothing to set up before the first show().
}

export function show(_args: string[]): void {
    views_util.show({
        highlight_view_in_left_sidebar() {
            views_util.handle_message_view_deactivated(() => {
                left_sidebar_navigation_area.select_top_left_corner_item("");
            });
        },
        $view: $("#today-view"),
        update_compose: compose_closed_ui.update_buttons,
        is_visible: () => visible,
        set_visible,
        complete_rerender() {
            $("#today-view").text(title());
        },
    });
    // This view has no message feed to reply to, so it hides the
    // closed compose bar. An open compose box stays on screen.
    $("#compose").addClass("hide-closed-compose");
}

export function hide(): void {
    if (!visible) {
        return;
    }
    views_util.hide({$view: $("#today-view"), set_visible});
    $("#compose").removeClass("hide-closed-compose");
}

export function title(): string {
    return $t({defaultMessage: "Today"});
}
