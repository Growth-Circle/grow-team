import $ from "jquery";

import render_channel_pins_list from "../templates/channel_pins_list.hbs";

import * as channel_pins from "./channel_pins.ts";
import * as channel_pins_data from "./channel_pins_data.ts";
import * as drawer from "./drawer.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as sub_store from "./sub_store.ts";
import * as timerender from "./timerender.ts";
import * as util from "./util.ts";

/*

The Pins tab of a channel opens this drawer: the messages pinned in the
channel, newest pin first, each with a link that jumps to the message in
its conversation. The list follows `channel_pin` events while it is open.

*/

let open_stream_id: number | undefined;
let load_failed = false;

function time_label(timestamp: number): string {
    return timerender.get_localized_date_or_time_for_format(
        new Date(timestamp * 1000),
        "dayofyear_time",
    );
}

function jump_url(stream_id: number, topic: string, message_id: number): string {
    return hash_util.search_terms_to_hash([
        {operator: "channel", operand: String(stream_id)},
        {operator: "topic", operand: topic},
        {operator: "near", operand: String(message_id)},
    ]);
}

function render($list: JQuery, stream_id: number): void {
    const pins = channel_pins_data.get_pins(stream_id);
    $list.html(
        render_channel_pins_list({
            loading: pins === undefined && !load_failed,
            error: load_failed ? $t({defaultMessage: "Could not load the pinned messages."}) : "",
            has_pins: pins !== undefined && pins.length > 0,
            can_change: channel_pins.can_change_pins(stream_id),
            pins: (pins ?? []).map((pin) => ({
                message_id: pin.message_id,
                sender_name: pin.sender_full_name,
                time_label: time_label(pin.date_sent),
                topic_label: pin.topic === "" ? "" : util.get_final_topic_display_name(pin.topic),
                rendered_content: pin.rendered_content,
                jump_url: jump_url(stream_id, pin.topic, pin.message_id),
            })),
        }),
    );
    $list.find(".sj-pins__content").each(function () {
        rendered_markdown.update_elements($(this));
    });
}

export function open(stream_id: number): void {
    load_failed = false;
    const $list = $("<div>").addClass("sj-pins");
    const stream_name = sub_store.maybe_get_stream_name(stream_id) ?? "";
    open_stream_id = stream_id;

    $list.on("click", ".sj-pins__jump", () => {
        drawer.close();
    });
    $list.on("click", ".sj-pins__unpin", function () {
        channel_pins.unpin_message(Number($(this).attr("data-message-id")));
    });

    const stop_listening = channel_pins.on_change((changed_stream_id) => {
        if (changed_stream_id === stream_id) {
            render($list, stream_id);
        }
    });

    render($list, stream_id);
    drawer.open({
        kind_label: $t({defaultMessage: "PINS · #{stream_name}"}, {stream_name}),
        $body: $list,
        extra_class: "sj-pins-drawer",
        on_close() {
            stop_listening();
            open_stream_id = undefined;
        },
    });
    channel_pins.load(stream_id, () => {
        if (open_stream_id === stream_id) {
            load_failed = true;
            render($list, stream_id);
        }
    });
}
