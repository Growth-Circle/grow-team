import $ from "jquery";
import assert from "minimalistic-assert";
import type * as z from "zod/mini";

import render_navigation_tour_video_modal from "../templates/navigation_tour_video_modal.hbs";

import * as browser_history from "./browser_history.ts";
import * as channel from "./channel.ts";
import * as dialog_widget from "./dialog_widget.ts";
import {$t, $t_html} from "./i18n.ts";
import type * as message_view from "./message_view.ts";
import * as people from "./people.ts";
import type {StateData, onboarding_step_schema} from "./state_data.ts";
import * as util from "./util.ts";

export type OnboardingStep = z.output<typeof onboarding_step_schema>;

export const ONE_TIME_NOTICES_TO_DISPLAY = new Set<string>();

const MAX_RETRIES = 5;

export function post_onboarding_step_as_read(
    onboarding_step_name: string,
    schedule_navigation_tour_video_reminder_delay?: number,
    attempt = 1,
): void {
    if (attempt > MAX_RETRIES) {
        return;
    }

    const data: {onboarding_step: string; schedule_navigation_tour_video_reminder_delay?: number} =
        {
            onboarding_step: onboarding_step_name,
        };
    if (schedule_navigation_tour_video_reminder_delay !== undefined) {
        assert(onboarding_step_name === "navigation_tour_video");
        data.schedule_navigation_tour_video_reminder_delay =
            schedule_navigation_tour_video_reminder_delay;
    }
    void channel.post({
        url: "/json/users/me/onboarding_steps",
        data,
        error(xhr) {
            if (xhr.status === 400) {
                // Bad request: Codepath calling this function supplied
                // invalid 'onboarding_step_name' (almost negligible chance
                // because it's not user input) - but no point of retrying
                // in that case.
                return;
            }

            const retry_delay_secs = util.get_retry_backoff_seconds(xhr, attempt);
            setTimeout(() => {
                post_onboarding_step_as_read(
                    onboarding_step_name,
                    schedule_navigation_tour_video_reminder_delay,
                    attempt + 1,
                );
            }, retry_delay_secs * 1000);
        },
    });
}

export function update_onboarding_steps_to_display(onboarding_steps: OnboardingStep[]): void {
    ONE_TIME_NOTICES_TO_DISPLAY.clear();

    for (const onboarding_step of onboarding_steps) {
        if (onboarding_step.type === "one_time_notice") {
            ONE_TIME_NOTICES_TO_DISPLAY.add(onboarding_step.name);
        }
    }
}

function narrow_to_dm_with_welcome_bot_new_user(
    onboarding_steps: OnboardingStep[],
    show_message_view: typeof message_view.show,
): void {
    if (
        onboarding_steps.some(
            (onboarding_step) => onboarding_step.name === "narrow_to_dm_with_welcome_bot_new_user",
        )
    ) {
        post_onboarding_step_as_read("narrow_to_dm_with_welcome_bot_new_user");

        if (!browser_history.is_current_hash_home_view()) {
            // If this account was created with a `next` parameter to
            // take the user to a specific Zulip view, that takes
            // precedence over sending the user to the welcome bot DM
            // view. The user will hopefully still make their way
            // there, as it is still an unread DM conversation.
            return;
        }

        show_message_view(
            [
                {
                    operator: "dm",
                    operand: [people.WELCOME_BOT.user_id],
                },
            ],
            {trigger: "sidebar"},
        );
    }
}

function show_navigation_tour_quickstart(update_recipient_row_attention_level: () => void): void {
    if (ONE_TIME_NOTICES_TO_DISPLAY.has("navigation_tour_video")) {
        const modal_content_html = render_navigation_tour_video_modal();
        dialog_widget.launch({
            modal_title_html: $t_html({defaultMessage: "Welcome to Grow Team!"}),
            modal_content_html,
            on_click() {
                // Do nothing
            },
            modal_submit_button_text: $t({defaultMessage: "Get started"}),
            single_footer_button: true,
            close_on_submit: true,
            id: "navigation-tour-video-modal",
            close_on_overlay_click: false,
            on_hide() {
                // `narrow_to_dm_with_welcome_bot_new_user` triggers a focus change from
                // #compose-channel-recipient to #compose-textarea (see `compose_actions.show_compose_box`
                // with `opts.defer_focus = true`). We start initializing this modal while the
                // focus transition is in progress, resulting in a flaky behaviour of the
                // element that will be in focus when modal is closed.
                //
                // We explicitly set the focus to #compose-textarea to avoid flaky nature.
                $("textarea#compose-textarea").trigger("focus");
                update_recipient_row_attention_level();

                post_onboarding_step_as_read("navigation_tour_video");
            },
        });
    }
}

export function initialize(
    params: StateData["onboarding_steps"],
    {
        show_message_view,
        update_recipient_row_attention_level,
    }: {
        show_message_view: typeof message_view.show;
        update_recipient_row_attention_level: () => void;
    },
): void {
    update_onboarding_steps_to_display(params.onboarding_steps);

    narrow_to_dm_with_welcome_bot_new_user(params.onboarding_steps, show_message_view);
    show_navigation_tour_quickstart(update_recipient_row_attention_level);
}
