"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

let draft_changes = 0;
mock_esm("../src/agent_send_intent", {
    change_draft() {
        draft_changes += 1;
    },
});
const locations = [];
mock_esm("../src/browser_history", {
    go_to_location(hash) {
        locations.push(hash);
    },
});
let dialog;
mock_esm("../src/dialog_widget", {
    launch(conf) {
        dialog = conf;
    },
});
mock_esm("../src/hash_util", {
    by_conversation_and_time_url: (message) =>
        `https://realm.test/#narrow/channel/3-general/topic/greetings/near/${message.id}`,
});
const updated = [];
mock_esm("../src/rendered_markdown", {
    update_elements($content) {
        updated.push($content);
    },
});
mock_esm("../src/timerender", {
    get_full_datetime: () => "Tuesday, 22 September, 10:00",
});

const compose_quote_cards = zrequire("compose_quote_cards");
const compose_quote_context = zrequire("compose_quote_context");

const first = {
    id: 36,
    sender_full_name: "Rama Aditya",
    content: "<p>ide apa</p>",
    timestamp: 1790000000,
};
const second = {
    id: 37,
    sender_full_name: "Ari Eko",
    content: "<p>coba   pakai\nagent</p>",
    timestamp: 1790000060,
};

run_test("quote cards join the typed text only when compose reads it", () => {
    compose_quote_context.initialize();
    compose_quote_cards.clear();
    assert.equal(compose_quote_cards.with_quotes("typed"), "typed");
    assert.ok(!compose_quote_cards.has_cards());

    draft_changes = 0;
    compose_quote_cards.add(first, "quote one");
    compose_quote_cards.add(second, "quote two");
    assert.ok(compose_quote_cards.has_cards());
    assert.equal(draft_changes, 2);
    assert.equal(compose_quote_cards.with_quotes("typed"), "quote one\n\nquote two\n\ntyped");
    assert.equal(compose_quote_cards.with_quotes(""), "quote one\n\nquote two");
    assert.ok(!$("#compose-quote-context").prop("hidden"));

    // Quoting the same message again replaces its card instead of adding one.
    compose_quote_cards.add(first, "quote one again");
    assert.equal(compose_quote_cards.with_quotes(""), "quote one again\n\nquote two");

    // The raw markdown from the server replaces the content on hand.
    draft_changes = 0;
    compose_quote_cards.update_markdown(37, "quote two raw");
    compose_quote_cards.update_markdown(37, "quote two raw");
    compose_quote_cards.update_markdown(99, "not a card");
    assert.equal(draft_changes, 1);
    assert.equal(compose_quote_cards.with_quotes(""), "quote one again\n\nquote two raw");

    compose_quote_cards.remove(36);
    assert.equal(compose_quote_cards.with_quotes("typed"), "quote two raw\n\ntyped");

    compose_quote_cards.clear();
    assert.ok(!compose_quote_cards.has_cards());
    assert.ok($("#compose-quote-context").prop("hidden"));
});

run_test("a card opens the quoted message and can go to it", () => {
    compose_quote_context.initialize();
    compose_quote_cards.clear();
    compose_quote_cards.add(first, "quote one", "ide");

    const open_handler = $("body").get_on_handler(
        "click",
        "#compose-quote-context .compose-quote-open",
    );
    const $open = $.create("open-button-stub");
    $open.attr("data-message-id", "36");
    open_handler.call({to_$: () => $open});
    assert.equal(dialog.modal_title_text, "translated: Quote");
    assert.equal(dialog.modal_submit_button_text, "translated: Go to message");
    assert.ok(dialog.close_on_submit);
    assert.match(dialog.modal_content_html, /Rama Aditya/);
    assert.match(dialog.modal_content_html, /ide apa/);
    dialog.post_render();
    assert.equal(updated.length, 1);
    dialog.on_click();
    assert.deepEqual(locations, ["#narrow/channel/3-general/topic/greetings/near/36"]);

    // A card that is gone opens nothing.
    dialog = undefined;
    const $missing = $.create("missing-button-stub");
    $missing.attr("data-message-id", "99");
    open_handler.call({to_$: () => $missing});
    assert.equal(dialog, undefined);

    const remove_handler = $("body").get_on_handler(
        "click",
        "#compose-quote-context .compose-quote-remove",
    );
    const $remove = $.create("remove-button-stub");
    $remove.attr("data-message-id", "36");
    remove_handler.call({to_$: () => $remove});
    assert.ok(!compose_quote_cards.has_cards());
});
