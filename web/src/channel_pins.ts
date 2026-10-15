import $ from "jquery";

import * as channel from "./channel.ts";
import * as channel_pins_data from "./channel_pins_data.ts";
import {$t_html} from "./i18n.ts";
import * as message_lists from "./message_lists.ts";
import * as message_store from "./message_store.ts";
import * as stream_data from "./stream_data.ts";
import * as ui_report from "./ui_report.ts";

/*

Pinned messages of a channel. channel_pins_data.ts keeps the list that
the server sent for each channel that a person looked at. This module
loads those lists, sends pin and unpin requests, and keeps the lists
current from `channel_pin` events. A pin shows as a small icon on its
message, so the message rows are drawn again when their pin changes.

*/

type Listener = (stream_id: number) => void;

const listeners = new Set<Listener>();
// The newest request of each channel wins. An older answer that comes
// late would bring back a pin that was removed.
const latest_request = new Map<number, number>();
let request_counter = 0;

// Tells when the pins of a channel change. It returns a function that
// stops the listener.
export function on_change(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
        listeners.delete(listener);
    };
}

function notify(stream_id: number, changed_message_ids: number[]): void {
    const messages = changed_message_ids
        .map((message_id) => message_store.get(message_id))
        .filter((message) => message !== undefined);
    if (messages.length > 0) {
        for (const list of message_lists.all_rendered_message_lists()) {
            list.view.rerender_messages(messages);
        }
    }
    for (const listener of listeners) {
        listener(stream_id);
    }
}

export function load(stream_id: number, on_error?: () => void): void {
    request_counter += 1;
    const request_id = request_counter;
    latest_request.set(stream_id, request_id);
    void channel.get({
        url: `/json/streams/${stream_id}/pins`,
        success(raw_data) {
            if (latest_request.get(stream_id) !== request_id) {
                return;
            }
            const {pins} = channel_pins_data.channel_pins_response_schema.parse(raw_data);
            notify(stream_id, channel_pins_data.set_pins(stream_id, pins));
        },
        error() {
            if (latest_request.get(stream_id) === request_id) {
                on_error?.();
            }
        },
    });
}

export function ensure_loaded(stream_id: number): void {
    if (!channel_pins_data.is_loaded(stream_id) && !latest_request.has(stream_id)) {
        load(stream_id);
    }
}

// Reading is for everyone who reads the channel; the server also
// requires permission to post before it changes a pin.
export function can_change_pins(stream_id: number): boolean {
    const sub = stream_data.get_sub_by_id(stream_id);
    return sub !== undefined && stream_data.can_post_messages_in_stream(sub);
}

function report_error(xhr: JQuery.jqXHR, message_html: string): void {
    ui_report.error(message_html, xhr, $("#home-error"));
}

export function pin_message(message_id: number): void {
    void channel.post({
        url: `/json/messages/${message_id}/pin`,
        error(xhr) {
            report_error(xhr, $t_html({defaultMessage: "Could not pin the message"}));
        },
    });
}

export function unpin_message(message_id: number): void {
    void channel.del({
        url: `/json/messages/${message_id}/pin`,
        error(xhr) {
            report_error(xhr, $t_html({defaultMessage: "Could not unpin the message"}));
        },
    });
}

export function handle_event(event: {op: string; stream_id: number; message_id: number}): void {
    if (event.op === "add") {
        // The event has no message text, so the list comes from the server.
        load(event.stream_id);
        return;
    }
    if (channel_pins_data.remove_pin(event.stream_id, event.message_id)) {
        notify(event.stream_id, [event.message_id]);
    }
}

// The server removes the pin with its message.
export function handle_deleted_messages(message_ids: number[]): void {
    channel_pins_data.remove_pins_of_messages(message_ids);
    for (const stream_id of latest_request.keys()) {
        notify(stream_id, []);
    }
}
