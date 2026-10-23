import $ from "jquery";
import * as z from "zod/mini";

import * as channel from "./channel.ts";
import * as confirm_dialog from "./confirm_dialog.ts";
import * as feedback_widget from "./feedback_widget.ts";
import {$t, $t_html} from "./i18n.ts";
import * as inbox_util from "./inbox_util.ts";

// The button that marks the unread messages older than a week as read,
// on the Inbox and Needs you pages. The server counts and marks them,
// because the browser does not know how old an unread message is. It
// only touches the messages of the person who clicks.

const count_schema = z.object({count: z.number(), days: z.number()});
const marked_schema = z.object({count: z.number()});

let stale_count = 0;
let stale_days = 7;

export function get_count(): number {
    return stale_count;
}

export function button_label(): string {
    return $t({defaultMessage: "Mark read older than {days} days"}, {days: stale_days});
}

// Handlebars tests only booleans, so the page template gets the label
// and an empty string when there is nothing to mark.
export function label_if_any(): string {
    return stale_count > 0 ? button_label() : "";
}

// The Inbox draws its button with the rest of the page. The Needs you
// page draws it from `label_if_any`, and this keeps both in step in
// between two drawings.
export function update_buttons(): void {
    const $buttons = $(".mark-stale-read");
    $buttons.toggleClass("hide", stale_count === 0);
    $buttons.find(".mark-stale-read-label").text(button_label());
}

export async function refresh(): Promise<void> {
    try {
        const result = count_schema.parse(await channel.get({url: "/json/messages/stale_unread"}));
        stale_count = result.count;
        stale_days = result.days;
    } catch {
        // The button stays out of the way when the count is unknown.
        stale_count = 0;
    }
    update_buttons();
}

async function mark_as_read(): Promise<void> {
    try {
        const result = marked_schema.parse(
            await channel.post({url: "/json/messages/stale_unread"}),
        );
        stale_count = 0;
        update_buttons();
        feedback_widget.show_toast({
            text: $t(
                {
                    defaultMessage:
                        "{count, plural, one {# message} other {# messages}} marked as read.",
                },
                {count: result.count},
            ),
        });
    } catch {
        feedback_widget.show_toast({
            variant: "error",
            text: $t({defaultMessage: "Could not save. Try again."}),
            on_retry() {
                void mark_as_read();
            },
        });
    }
    // The button is gone or it still works. Focus goes to the page
    // that the person was on, not to the top of the document.
    $(inbox_util.is_visible() ? "#inbox-search" : ".needs-tab-active").trigger("focus");
}

export function confirm_mark_as_read(): void {
    if (stale_count === 0) {
        return;
    }
    confirm_dialog.launch({
        modal_title_text: $t({defaultMessage: "Mark old messages as read?"}),
        modal_content_html: $t_html(
            {
                defaultMessage:
                    "{count, plural, one {# unread message} other {# unread messages}} older than {days} days will be marked as read.",
            },
            {count: stale_count, days: stale_days},
        ),
        modal_submit_button_text: $t({defaultMessage: "Mark as read"}),
        on_click() {
            void mark_as_read();
        },
    });
}

export function initialize(): void {
    $("body").on("click", ".mark-stale-read", (event) => {
        event.preventDefault();
        confirm_mark_as_read();
    });
}
