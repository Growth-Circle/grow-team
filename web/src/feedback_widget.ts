import $ from "jquery";

import render_feedback_container from "../templates/feedback_container.hbs";

import * as blueslip from "./blueslip.ts";
import * as drawer from "./drawer.ts";
import {$t} from "./i18n.ts";
import * as modals from "./modals.ts";

/*

This code lets you show something like this:

    +-----
    | TOPIC MUTES [undo] [x]
    |
    | You muted stream Foo, topic Bar.
    +-----

And then you configure the undo behavior, and
everything else is controlled by the widget.

Code-wise it's a singleton widget that controls the DOM inside
#feedback_container, which gets served up by server.

*/

type FeedbackWidgetMeta = {
    hide_me_time: number | null;
    alert_hover_state: boolean;
    $container: JQuery | null;
    opened: boolean;
    handlers_set?: boolean;
    undo: (() => void) | undefined;
};

type FeedbackWidgetOptions = {
    populate: (element: JQuery) => void;
    title_text: string;
    undo_button_text?: string;
    on_undo?: () => void;
    hide_delay?: number;
};

const meta: FeedbackWidgetMeta = {
    hide_me_time: null,
    alert_hover_state: false,
    $container: null,
    opened: false,
    undo: undefined,
};

const animate = {
    maybe_close() {
        if (!meta.opened) {
            return;
        }

        if ((meta.hide_me_time ?? 0) < Date.now() && !meta.alert_hover_state) {
            animate.fadeOut();
            return;
        }

        setTimeout(() => {
            animate.maybe_close();
        }, 100);
    },
    fadeOut() {
        if (!meta.opened) {
            return;
        }

        if (meta.$container) {
            meta.$container.addClass("slide-out-feedback-container");
            // Delay setting `display: none` enough that the hide animation starts.
            setTimeout(
                () =>
                    meta.$container?.removeClass([
                        "show-feedback-container",
                        "slide-out-feedback-container",
                    ]),
                50,
            );
            meta.opened = false;
            meta.alert_hover_state = false;
        }
    },
    fadeIn() {
        if (meta.opened) {
            return;
        }

        if (meta.$container) {
            meta.$container.addClass("show-feedback-container");
            meta.opened = true;
            setTimeout(() => {
                animate.maybe_close();
            }, 100);
        }
    },
};

function set_up_handlers(): void {
    if (meta.handlers_set) {
        return;
    }

    if (!meta.$container) {
        blueslip.error("$container not found for feedback widget.");
        return;
    }

    meta.handlers_set = true;

    // if the user mouses over the notification, don't hide it.
    meta.$container.on("mouseenter", () => {
        if (!meta.opened) {
            return;
        }

        meta.alert_hover_state = true;
    });

    // once the user's mouse leaves the notification, restart the countdown.
    meta.$container.on("mouseleave", () => {
        if (!meta.opened) {
            return;
        }

        meta.alert_hover_state = false;
        // add at least 2000ms but if more than that exists just keep the
        // current amount.
        meta.hide_me_time = Math.max(meta.hide_me_time ?? 0, Date.now() + 2000);
    });

    meta.$container.on("click", ".exit-me", () => {
        animate.fadeOut();
    });

    meta.$container.on("click", ".feedback_undo", () => {
        if (meta.undo) {
            meta.undo();
        }
        animate.fadeOut();
    });
}

export function is_open(): boolean {
    return meta.opened;
}

export function dismiss(): void {
    animate.fadeOut();
}

export function show(opts: FeedbackWidgetOptions): void {
    if (!opts.populate) {
        blueslip.error("programmer needs to supply populate callback.");
        return;
    }

    meta.$container = $("#feedback_container");

    let has_undo_button = true;
    if (opts.on_undo === undefined) {
        has_undo_button = false;
    }
    const html = render_feedback_container({has_undo_button});
    meta.$container.html(html);

    set_up_handlers();

    meta.undo = opts.on_undo;

    // add a four second delay before closing up.
    meta.hide_me_time = Date.now() + (opts.hide_delay ?? 4000);

    meta.$container.find(".feedback_title").text(opts.title_text);
    meta.$container.find(".feedback_undo").text(opts.undo_button_text ?? "");
    opts.populate(meta.$container.find(".feedback_content"));

    animate.fadeIn();
}

/*

show_toast shows the Sanji toast. It is separate from show() and
dismiss() above. It makes its own elements and it shows one toast at
a time, thus the callers of show() do not change.

The toast has two layouts:
- Global: fixed at the bottom center of the window, above all pages.
  show_toast uses it when there is no visible inline_container, when
  the variant is not "success", or when a drawer or a modal is open.
- Inline: a green card in inline_container. Give an empty slot, for
  example a slot below the brief box of the Today page. show_toast
  removes all content of the slot. The inline card shows only
  "success", because a green card must not show a failure.

*/

export type ToastVariant = "success" | "info" | "error";

export type ShowToastOptions = {
    text: string;
    variant?: ToastVariant;
    on_undo?: () => void;
    on_retry?: () => void;
    // "| undefined" lets the Undo button give this value to the
    // "Cancelled." toast as it is.
    inline_container?: JQuery | undefined;
};

// 3.2 seconds, or 5 seconds when the toast has an Undo button.
const TOAST_DURATION_MS = 3200;
const TOAST_DURATION_WITH_UNDO_MS = 5000;
// The time between a change of the screen reader copy and its text.
const STATUS_DELAY_MS = 100;

const toast_state: {
    $toast: JQuery | undefined;
    timer: ReturnType<typeof setTimeout> | undefined;
    status_timer: ReturnType<typeof setTimeout> | undefined;
} = {
    $toast: undefined,
    timer: undefined,
    status_timer: undefined,
};

function clear_toast(): void {
    if (toast_state.timer !== undefined) {
        clearTimeout(toast_state.timer);
        toast_state.timer = undefined;
    }
    if (toast_state.$toast) {
        // Remove the toast, not only hide it. Then the Undo button of
        // an old toast cannot run its callback.
        toast_state.$toast.remove();
        toast_state.$toast = undefined;
    }
}

// Node tests clear the elements of zjquery between run_test() blocks,
// but not the state of this module. This function clears that state
// without a remove() call on an element that is not there.
export function reset_for_testing(): void {
    clearTimeout(toast_state.timer);
    clearTimeout(toast_state.status_timer);
    toast_state.timer = undefined;
    toast_state.status_timer = undefined;
    toast_state.$toast = undefined;
}

function announce(text: string): void {
    let $status = $("#sj-toast-status");
    if ($status.length === 0) {
        $status = $("<div>")
            .attr("id", "sj-toast-status")
            .attr("role", "status")
            .addClass("sj-toast-status");
        $("body").append($status);
    }
    // A screen reader reads only a change of a live region that is
    // already in the page. Thus clear the region, and write the text a
    // short time later. The same text two times is then read again.
    $status.text("");
    clearTimeout(toast_state.status_timer);
    toast_state.status_timer = setTimeout(() => {
        $status.text(text);
    }, STATUS_DELAY_MS);
}

function schedule_clear(duration: number): void {
    toast_state.timer = setTimeout(() => {
        // Keep the toast while the pointer or the focus is on it. Then
        // the timer does not remove a button that the person uses.
        if (toast_state.$toast?.is(":hover, :focus-within")) {
            schedule_clear(duration);
            return;
        }
        clear_toast();
    }, duration);
}

function build_toast_action(label: string, on_click: () => void): JQuery {
    return $("<button>")
        .attr("type", "button")
        .addClass("sj-toast__action")
        .text(label)
        .on("click", () => {
            clear_toast();
            on_click();
        });
}

function build_action(opts: ShowToastOptions, variant: ToastVariant): JQuery | undefined {
    const on_undo = opts.on_undo;
    if (on_undo) {
        return build_toast_action($t({defaultMessage: "Undo"}), () => {
            on_undo();
            // After Undo, a new toast tells the person that the action
            // is cancelled.
            show_toast({
                text: $t({defaultMessage: "Cancelled."}),
                inline_container: opts.inline_container,
            });
        });
    }
    if (variant === "error" && opts.on_retry) {
        return build_toast_action($t({defaultMessage: "Try again"}), opts.on_retry);
    }
    return undefined;
}

function build_toast(opts: ShowToastOptions, variant: ToastVariant, inline: boolean): JQuery {
    const $toast = $("<div>")
        .addClass("sj-toast")
        .addClass(inline ? "sj-toast--inline" : "sj-toast--global")
        .addClass(`sj-toast--${variant}`);

    if (variant === "success") {
        const $icon = $("<i>").addClass("sj-toast__icon").attr("aria-hidden", "true").text("✓");
        $toast.append($icon);
    }
    const $text = $("<span>").addClass("sj-toast__text").text(opts.text);
    $toast.append($text);

    const $action = build_action(opts, variant);
    if ($action) {
        $toast.append($action);
    }
    return $toast;
}

export function show_toast(opts: ShowToastOptions): void {
    clear_toast();

    const variant = opts.variant ?? "success";
    const $slot = opts.inline_container;
    const inline =
        $slot !== undefined &&
        variant === "success" &&
        // The slot must be visible: it has a layout box.
        [...$slot].some((element) => element.getClientRects().length > 0) &&
        !modals.any_active() &&
        !drawer.is_open();

    const $toast = build_toast(opts, variant, inline);
    toast_state.$toast = $toast;
    if (inline) {
        $slot.empty().append($toast);
    } else {
        $("body").append($toast);
    }
    announce(opts.text);

    schedule_clear(opts.on_undo ? TOAST_DURATION_WITH_UNDO_MS : TOAST_DURATION_MS);
}
