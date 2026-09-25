import $ from "jquery";

import * as command_palette from "./command_palette.ts";
import * as sidebar_ui from "./sidebar_ui.ts";

// Clicks for the app shell's top bar and nav scrim below the shell
// breakpoint. The layout and the drawer motion are pure CSS in
// app_frame.css; sidebar_ui.ts opens and closes the drawer.

export function initialize(): void {
    $("body").on("click", "#app-topbar-menu", () => {
        sidebar_ui.show_streamlist_sidebar();
    });

    $("body").on("click", "#app-nav-scrim", () => {
        sidebar_ui.hide_streamlist_sidebar();
    });

    $("body").on("click", "#app-topbar-search", () => {
        command_palette.open();
    });
}
