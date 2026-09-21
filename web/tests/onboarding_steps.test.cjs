"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");

const dialog_widget = mock_esm("../src/dialog_widget");

const onboarding_steps = zrequire("onboarding_steps");

run_test("navigation tour shows Grow Team quickstart", ({mock_template, override}) => {
    mock_template("navigation_tour_video_modal.hbs", true, (_args, html) => {
        assert.match(html, /Get started with Grow Team/);
        assert.match(html, /href="\/help\/"/);
        assert.doesNotMatch(html, /<video/);
        return html;
    });

    let dialog_options;
    override(dialog_widget, "launch", (options) => {
        dialog_options = options;
    });

    onboarding_steps.initialize(
        {
            onboarding_steps: [
                {
                    name: "navigation_tour_video",
                    type: "one_time_notice",
                },
            ],
            navigation_tour_video_url: null,
        },
        {show_message_view: noop, update_recipient_row_attention_level: noop},
    );

    assert.equal(dialog_options.modal_title_html, "translated: Welcome to Grow Team!");
    assert.equal(dialog_options.modal_submit_button_text, "translated: Get started");
    assert.equal(dialog_options.single_footer_button, true);
});
