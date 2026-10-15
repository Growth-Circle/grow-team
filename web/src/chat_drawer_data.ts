import * as z from "zod/mini";

export const PAGE_SIZE = 30;

export const chat_message_schema = z.object({
    id: z.number(),
    sender_id: z.number(),
    sender_full_name: z.string(),
    // Rendered by the server with the Markdown processor of messages.
    content: z.string(),
    timestamp: z.number(),
});

export const chat_messages_response_schema = z.object({
    messages: z.array(chat_message_schema),
});

export type ChatMessage = z.infer<typeof chat_message_schema>;

export type Conversation = {stream_id: number; topic: string};

export type MessageGroup = {
    sender_id: number;
    sender_full_name: string;
    // The first message of the group sets the time of its header.
    timestamp: number;
    messages: ChatMessage[];
};

// A message can arrive twice for the person who sent it: in the response
// to their request and in the event for the channel. Matching on the id
// keeps one copy.
export function merge_messages(existing: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
    const by_id = new Map<number, ChatMessage>();
    for (const message of [...existing, ...incoming]) {
        by_id.set(message.id, message);
    }
    return [...by_id.values()].toSorted((a, b) => a.id - b.id);
}

export function remove_messages(messages: ChatMessage[], ids: number[]): ChatMessage[] {
    return messages.filter((message) => !ids.includes(message.id));
}

// Consecutive messages of one sender share one header, as in a messaging
// app.
export function group_by_sender(messages: ChatMessage[]): MessageGroup[] {
    const groups: MessageGroup[] = [];
    for (const message of messages) {
        const last = groups.at(-1);
        if (last?.sender_id === message.sender_id) {
            last.messages.push(message);
            continue;
        }
        groups.push({
            sender_id: message.sender_id,
            sender_full_name: message.sender_full_name,
            timestamp: message.timestamp,
            messages: [message],
        });
    }
    return groups;
}

// Topics are case-insensitive, and the empty topic is the only topic of
// a channel without topics.
export function is_in_conversation(
    message: {type: string; stream_id?: number; topic?: string},
    conversation: Conversation,
): boolean {
    return (
        message.type === "stream" &&
        message.stream_id === conversation.stream_id &&
        (message.topic ?? "").toLowerCase() === conversation.topic.toLowerCase()
    );
}

export function narrow_for(conversation: Conversation): string {
    return JSON.stringify([
        {operator: "channel", operand: conversation.stream_id},
        {operator: "topic", operand: conversation.topic},
    ]);
}

// Follows the "Enter sends" setting of the compose box: with it on, Enter
// sends and Shift+Enter starts a new line; with it off, Ctrl or Cmd with
// Enter sends. Enter while an input method composes text only confirms
// the text.
export function should_send_on_enter(opts: {
    enter_sends: boolean;
    shift_key: boolean;
    ctrl_key: boolean;
    meta_key: boolean;
    is_composing: boolean;
}): boolean {
    if (opts.is_composing) {
        return false;
    }
    const cmd_or_ctrl = opts.ctrl_key || opts.meta_key;
    if (opts.enter_sends) {
        return cmd_or_ctrl || !opts.shift_key;
    }
    return cmd_or_ctrl;
}
