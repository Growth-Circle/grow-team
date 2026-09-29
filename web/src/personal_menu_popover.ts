import $ from "jquery";

import render_navbar_personal_menu_popover from "../templates/popovers/navbar/navbar_personal_menu_popover.hbs";
import render_sidebar_user_card from "../templates/popovers/personal_menu_popover.hbs";

import * as channel from "./channel.ts";
import * as common from "./common.ts";
import * as feedback_widget from "./feedback_widget.ts";
import {$t} from "./i18n.ts";
import * as information_density from "./information_density.ts";
import * as live_updates from "./live_updates.ts";
import * as message_view from "./message_view.ts";
import * as people from "./people.ts";
import * as popover_menus from "./popover_menus.ts";
import * as popover_menus_data from "./popover_menus_data.ts";
import * as popovers from "./popovers.ts";
import * as sidebar_targets from "./sidebar_targets.ts";
import {current_user, realm} from "./state_data.ts";
import {parse_html} from "./ui_util.ts";
import {user_settings} from "./user_settings.ts";
import * as user_status from "./user_status.ts";
import * as workspace_switcher from "./workspace_switcher.ts";

export function initialize(): void {
    initialize_sidebar_user_card();
    popover_menus.register_popover_menu("#personal-menu", {
        theme: "popover-menu",
        placement: "bottom",
        offset: popover_menus.NAVBAR_POPOVER_OFFSET,
        // The strategy: "fixed"; and eventlisteners modifier option
        // ensure that the personal menu does not modify its position
        // or disappear when user zooms the page.
        popperOptions: {
            strategy: "fixed",
            modifiers: [
                {
                    name: "eventListeners",
                    options: {
                        scroll: false,
                    },
                },
            ],
        },
        onMount(instance) {
            const $popper = $(instance.popper);
            popover_menus.popover_instances.personal_menu = instance;

            $popper.on("change", "input[name='theme-select']", (e) => {
                const new_theme_code = $(e.currentTarget).attr("data-theme-code");
                channel.patch({
                    url: "/json/settings",
                    data: {color_scheme: new_theme_code},
                    error() {
                        // NOTE: The additional delay allows us to visually communicate
                        // that an error occurred due to which we are reverting back
                        // to the previously used value.
                        setTimeout(() => {
                            const prev_theme_code = user_settings.color_scheme;
                            $(e.currentTarget)
                                .parent()
                                .find(`input[data-theme-code="${prev_theme_code}"]`)
                                .prop("checked", true);
                        }, 500);
                    },
                });
            });

            $popper.one("click", ".personal-menu-clear-status", (e) => {
                e.preventDefault();
                user_status.server_update_status({
                    status_text: "",
                    emoji_name: "",
                    emoji_code: "",
                    success() {
                        popover_menus.hide_current_popover_if_visible(instance);
                    },
                });
            });

            $popper.one("click", ".narrow-self-direct-message", (e) => {
                const user_id = current_user.user_id;
                message_view.show(
                    [
                        {
                            operator: "dm",
                            operand: [user_id],
                        },
                    ],
                    {trigger: "personal menu"},
                );
                popovers.hide_all();
                e.preventDefault();
            });

            $popper.one("click", ".narrow-messages-sent", (e) => {
                const user_id = current_user.user_id;
                message_view.show(
                    [
                        {
                            operator: "sender",
                            operand: user_id,
                        },
                    ],
                    {trigger: "personal menu"},
                );
                popovers.hide_all();
                e.preventDefault();
            });

            $popper.one("click", ".open-profile-settings", function (this: HTMLElement, e) {
                this.click();
                popovers.hide_all();
                e.preventDefault();
            });

            $popper.on("click", ".info-density-controls button", function (this: HTMLElement, e) {
                const changed_property =
                    information_density.information_density_properties_schema.parse(
                        $(this).closest(".button-group").attr("data-property"),
                    );
                const new_setting_value = information_density.update_information_density_settings(
                    $(this),
                    changed_property,
                );
                const data = {[changed_property]: new_setting_value};
                information_density.enable_or_disable_control_buttons($popper);

                if (changed_property === "web_font_size_px") {
                    // We do not want to display the arrow once font size is changed
                    // because popover will be detached from the user avatar as we
                    // do not change the font size in popover.
                    $("#personal-menu-dropdown").closest(".tippy-box").find(".tippy-arrow").hide();
                }

                void channel.patch({
                    url: "/json/settings",
                    data,
                    // We don't declare success or error
                    // handlers. We've already locally echoed the
                    // change, and the thinking for this component is
                    // that right answer for error handling is to do
                    // nothing. For the offline case, just letting you
                    // adjust the font size locally is great, and it's
                    // not obvious what good error handling is here
                    // for the server being down other than "try again
                    // later", which might as well be your next
                    // session.
                    //
                    // This strategy also avoids unpleasant races
                    // involving the button being clicked several
                    // times in quick succession.
                });
                e.preventDefault();
            });

            information_density.enable_or_disable_control_buttons($popper);
            void instance.popperInstance?.update();

            // We do not want font size of the popover to change when changing
            // font size using the buttons in popover, so that the buttons do
            // not shift.
            const font_size =
                popover_menus.POPOVER_FONT_SIZE_IN_EM * user_settings.web_font_size_px;
            $("#personal-menu-dropdown")
                .closest(".tippy-box")
                .css("font-size", font_size + "px");
        },
        onShow(instance) {
            const args = popover_menus_data.get_personal_menu_content_context();
            instance.setContent(parse_html(render_navbar_personal_menu_popover(args)));
            $("#personal-menu").addClass("active-navbar-menu");
        },
        onHidden(instance) {
            instance.destroy();
            popover_menus.popover_instances.personal_menu = null;
            $("#personal-menu").removeClass("active-navbar-menu");
        },
    });
}

export function toggle(): void {
    // NOTE: Since to open personal menu, you need to click on your avatar (which calls
    // tippyjs.hideAll()), or go via gear menu if using hotkeys, we don't need to
    // call tippyjs.hideAll() for it.
    $("#personal-menu").trigger("click");
}

// The user card at the bottom of the sidebar, and the menu that it
// opens. A click outside the menu closes it, through a scrim behind it.

function shortcut_hint(): string {
    return common.has_mac_keyboard() ? "⌘/" : "Ctrl+/";
}

// The key combination of the search button. It is not the key that opens
// the list of shortcuts, so it has its own function.
function search_shortcut_hint(): string {
    return sidebar_targets.search_shortcut_hint(common.has_mac_keyboard());
}

function render_user_card(): void {
    const $container = $("#sidebar-user-card");
    if ($container.length === 0) {
        return;
    }
    const role = sidebar_targets.role_of(current_user);
    $container.html(
        render_sidebar_user_card({
            full_name: current_user.full_name,
            email: current_user.delivery_email,
            avatar_url: people.small_avatar_url_for_person(current_user),
            role_label: workspace_switcher.role_label(role),
            workspace_name: realm.realm_name,
            can_manage_workspace:
                sidebar_targets.can_manage_workspace(role) &&
                sidebar_targets.get_hash("settings", "members") !== undefined,
            shortcut_hint: shortcut_hint(),
        }),
    );
    $("#sidebar-user-card-button").attr("aria-expanded", String(user_card_is_open));
}

let user_card_is_open = false;
let $user_card_scrim: JQuery | undefined;

export function close_user_card(): void {
    if (!user_card_is_open) {
        return;
    }
    user_card_is_open = false;
    $("#sidebar-user-card").removeClass("sidebar-user-card-open");
    $("#sidebar-user-card-button").attr("aria-expanded", "false");
    $user_card_scrim?.remove();
    $user_card_scrim = undefined;
    $(document).off("keydown.sanji-user-card");
}

function open_user_card(): void {
    if (user_card_is_open) {
        return;
    }
    user_card_is_open = true;
    $(document).trigger("sidebar_overlay_opened", ["user-card"]);
    $("#sidebar-user-card").addClass("sidebar-user-card-open");
    $("#sidebar-user-card-button").attr("aria-expanded", "true");
    $user_card_scrim = $("<div>")
        .addClass("sidebar-user-card-scrim")
        .on("click", close_user_card)
        .appendTo("body");
    $(document).on("keydown.sanji-user-card", (e: JQuery.KeyDownEvent) => {
        if (e.key === "Escape") {
            close_user_card();
            $("#sidebar-user-card-button").trigger("focus");
        }
    });
}

function toggle_user_card(): void {
    if (user_card_is_open) {
        close_user_card();
    } else {
        open_user_card();
    }
}

function initialize_sidebar_user_card(): void {
    const $container = $("#sidebar-user-card");
    render_user_card();

    $container.on("click", "#sidebar-user-card-button", (e) => {
        e.stopPropagation();
        toggle_user_card();
    });

    $container.on("click", '.sidebar-user-card-item[data-item="profile"]', () => {
        close_user_card();
        window.location.hash = "settings/profile";
    });
    $container.on("click", '.sidebar-user-card-item[data-item="notifications"]', () => {
        close_user_card();
        window.location.hash = "settings/notifications";
    });
    $container.on("click", '.sidebar-user-card-item[data-item="shortcuts"]', () => {
        close_user_card();
        if (sidebar_targets.is_shown("search")) {
            feedback_widget.show_toast({
                text: $t(
                    {defaultMessage: "{shortcut} search · Esc close · Enter open top result"},
                    {shortcut: search_shortcut_hint()},
                ),
            });
        } else {
            window.location.hash = "keyboard-shortcuts";
        }
    });
    $container.on("click", '.sidebar-user-card-item[data-item="workspace-settings"]', () => {
        close_user_card();
        const hash = sidebar_targets.get_hash("settings", "members");
        if (hash !== undefined) {
            window.location.hash = hash.slice(1);
        }
    });
    // The "Log out" item needs no click handler of its own: it carries
    // the same logout_button class the navbar menu's own item uses,
    // and web/src/click_handlers.ts already submits #logout_form for
    // any element with that class.

    $(document).on("sidebar_overlay_opened", (_event, source: string) => {
        if (source !== "user-card") {
            close_user_card();
        }
    });
    live_updates.on("realm_permissions", render_user_card);
}
