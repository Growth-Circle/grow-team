import * as z from "zod/mini";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";

// The channels that nobody has posted in for a while, for the sidebar's
// quiet rooms row. The sidebar draws the row. This module only loads the
// list.

const quiet_channel_schema = z.object({
    stream_id: z.number(),
    last_message_at: z.number(),
});
export type QuietChannel = z.infer<typeof quiet_channel_schema>;

const response_schema = z.object({
    threshold_days: z.number(),
    quiet_channels: z.array(quiet_channel_schema),
});

let quiet_channels: QuietChannel[] = [];
let threshold_days = 30;
const change_listeners = new Set<() => void>();

export function get_quiet_channels(): QuietChannel[] {
    return quiet_channels;
}

export function get_threshold_days(): number {
    return threshold_days;
}

export function on_change(cb: () => void): void {
    change_listeners.add(cb);
}

export function refresh(): void {
    channel.get({
        url: "/json/channels/quiet",
        success(raw_data) {
            const data = response_schema.parse(raw_data);
            quiet_channels = data.quiet_channels;
            threshold_days = data.threshold_days;
            for (const cb of change_listeners) {
                cb();
            }
        },
        error(xhr) {
            blueslip.warn("quiet_rooms: could not load /json/channels/quiet.", {
                status: xhr.status,
            });
        },
    });
}
