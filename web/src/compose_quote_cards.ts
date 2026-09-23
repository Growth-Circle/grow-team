import * as agent_send_intent from "./agent_send_intent.ts";
import type {Message} from "./message_store.ts";

// A quote in the compose box is a card that the member cannot edit.
// The card keeps the quote markdown that the old flow typed into the
// textarea, and the message gets that markdown only when compose reads
// its content to send, save, or preview it. A mention of an agent in
// the same message then carries every quoted message as its context.
//
// This module holds only the cards, so that compose_state can read them
// without importing the modal and rendering code.
export type QuoteCard = {
    message: Message;
    markdown: string;
    // The selected part of the message, when the member quoted a selection.
    selection: string | undefined;
};

let cards: QuoteCard[] = [];
let on_change: () => void = () => {
    // compose_quote_context sets the renderer at startup.
};

export function set_on_change(callback: () => void): void {
    on_change = callback;
}

export function get_cards(): readonly QuoteCard[] {
    return cards;
}

export function get_card(message_id: number): QuoteCard | undefined {
    return cards.find((card) => card.message.id === message_id);
}

export function add(message: Message, markdown: string, selection?: string): void {
    const existing = get_card(message.id);
    if (existing) {
        existing.markdown = markdown;
        existing.selection = selection;
    } else {
        cards.push({message, markdown, selection});
    }
    agent_send_intent.change_draft();
    on_change();
}

export function update_markdown(message_id: number, markdown: string): void {
    const card = get_card(message_id);
    if (card && card.markdown !== markdown) {
        card.markdown = markdown;
        agent_send_intent.change_draft();
    }
}

export function remove(message_id: number): void {
    cards = cards.filter((card) => card.message.id !== message_id);
    agent_send_intent.change_draft();
    on_change();
}

export function clear(): void {
    cards = [];
    on_change();
}

export function has_cards(): boolean {
    return cards.length > 0;
}

export function with_quotes(text: string): string {
    if (cards.length === 0) {
        return text;
    }
    return [...cards.map((card) => card.markdown), text].filter(Boolean).join("\n\n");
}
