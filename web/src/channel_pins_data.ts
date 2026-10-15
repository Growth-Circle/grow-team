import * as z from "zod/mini";

export const channel_pin_schema = z.object({
    message_id: z.number(),
    stream_id: z.number(),
    pinned_by_id: z.nullable(z.number()),
    date_pinned: z.number(),
    sender_id: z.number(),
    sender_full_name: z.string(),
    topic: z.string(),
    date_sent: z.number(),
    // Rendered by the server with the Markdown processor of messages.
    rendered_content: z.string(),
});

export const channel_pins_response_schema = z.object({
    pins: z.array(channel_pin_schema),
});

export type ChannelPin = z.infer<typeof channel_pin_schema>;

const pins_by_stream_id = new Map<number, ChannelPin[]>();

// The pins of a channel, newest first, or undefined while they are not
// loaded.
export function get_pins(stream_id: number): ChannelPin[] | undefined {
    return pins_by_stream_id.get(stream_id);
}

export function is_loaded(stream_id: number): boolean {
    return pins_by_stream_id.has(stream_id);
}

export function is_pinned(stream_id: number, message_id: number): boolean {
    return (
        pins_by_stream_id.get(stream_id)?.some((pin) => pin.message_id === message_id) ?? false
    );
}

// Sets the pins of a channel from the server, and returns the ids of the
// messages whose pin state changed.
export function set_pins(stream_id: number, pins: ChannelPin[]): number[] {
    const before = new Set((pins_by_stream_id.get(stream_id) ?? []).map((pin) => pin.message_id));
    const after = new Set(pins.map((pin) => pin.message_id));
    pins_by_stream_id.set(stream_id, pins);
    return [...before.symmetricDifference(after)];
}

// Returns whether the pin was in the list, so a caller can skip a redraw
// for a channel whose pins were never loaded.
export function remove_pin(stream_id: number, message_id: number): boolean {
    const pins = pins_by_stream_id.get(stream_id);
    if (pins?.some((pin) => pin.message_id === message_id) !== true) {
        return false;
    }
    pins_by_stream_id.set(
        stream_id,
        pins.filter((pin) => pin.message_id !== message_id),
    );
    return true;
}

export function remove_pins_of_messages(message_ids: number[]): void {
    for (const [stream_id, pins] of pins_by_stream_id) {
        pins_by_stream_id.set(
            stream_id,
            pins.filter((pin) => !message_ids.includes(pin.message_id)),
        );
    }
}

export function clear(): void {
    pins_by_stream_id.clear();
}
