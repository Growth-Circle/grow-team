import $ from "jquery";

import render_compose_quote_context from "../templates/compose_quote_context.hbs";
import render_compose_quote_modal from "../templates/compose_quote_modal.hbs";

import * as browser_history from "./browser_history.ts";
import * as compose_quote_cards from "./compose_quote_cards.ts";
import * as dialog_widget from "./dialog_widget.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import type {Message} from "./message_store.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as timerender from "./timerender.ts";

function plain_text(html: string): string {
    return $("<div>").html(html).text().replaceAll(/\s+/g, " ").trim();
}

function render(): void {
    const cards = compose_quote_cards.get_cards();
    const $tray = $("#compose-quote-context");
    $tray.html(
        render_compose_quote_context({
            cards: cards.map((card) => ({
                message_id: card.message.id,
                sender: card.message.sender_full_name,
                preview: plain_text(card.selection ?? card.message.content),
                open_label: $t(
                    {defaultMessage: "Show the message that {name} wrote"},
                    {name: card.message.sender_full_name},
                ),
            })),
        }),
    );
    $tray.prop("hidden", cards.length === 0);
}

function go_to_message(message: Message): void {
    const url = hash_util.by_conversation_and_time_url(message);
    browser_history.go_to_location(url.slice(url.indexOf("#")));
}

function show(message_id: number): void {
    const card = compose_quote_cards.get_card(message_id);
    if (!card) {
        return;
    }
    const {message} = card;
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "Quote"}),
        modal_content_html: render_compose_quote_modal({
            sender: message.sender_full_name,
            time: timerender.get_full_datetime(new Date(message.timestamp * 1000)),
            content: message.content,
        }),
        modal_submit_button_text: $t({defaultMessage: "Go to message"}),
        id: "compose-quote-modal",
        close_on_submit: true,
        on_click() {
            go_to_message(message);
        },
        post_render() {
            rendered_markdown.update_elements($("#compose-quote-modal .rendered_markdown"));
        },
    });
}

export function initialize(): void {
    compose_quote_cards.set_on_change(render);
    $("body").on(
        "click",
        "#compose-quote-context .compose-quote-open",
        function (this: HTMLElement) {
            show(Number($(this).attr("data-message-id")));
        },
    );
    $("body").on(
        "click",
        "#compose-quote-context .compose-quote-remove",
        function (this: HTMLElement) {
            compose_quote_cards.remove(Number($(this).attr("data-message-id")));
            $("textarea#compose-textarea").trigger("focus");
        },
    );
}
