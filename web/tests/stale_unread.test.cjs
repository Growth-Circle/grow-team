"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

let get_response;
let post_response;
let requests = [];
mock_esm("../src/channel", {
    get({url}) {
        requests.push(["GET", url]);
        return get_response instanceof Error
            ? Promise.reject(get_response)
            : Promise.resolve(get_response);
    },
    post({url}) {
        requests.push(["POST", url]);
        return post_response instanceof Error
            ? Promise.reject(post_response)
            : Promise.resolve(post_response);
    },
});

let toasts = [];
mock_esm("../src/feedback_widget", {
    show_toast(opts) {
        toasts.push(opts);
    },
});

mock_esm("../src/inbox_util", {is_visible: () => false});

let dialogs = [];
mock_esm("../src/confirm_dialog", {
    launch(conf) {
        dialogs.push(conf);
        return "modal-id";
    },
});

const stale_unread = zrequire("stale_unread");

function reset() {
    requests = [];
    toasts = [];
    dialogs = [];
}

async function flush() {
    for (let i = 0; i < 8; i += 1) {
        await Promise.resolve();
    }
}

function make_button() {
    const $button = $(".mark-stale-read");
    const $label = $.create("mark-stale-read-label");
    $button.set_find_results(".mark-stale-read-label", $label);
    return {$button, $label};
}

run_test("the button is hidden when nothing is older than 7 days", async () => {
    reset();
    const {$button} = make_button();
    get_response = {count: 0, days: 7};

    await stale_unread.refresh();

    assert.equal(stale_unread.get_count(), 0);
    assert.equal(stale_unread.label_if_any(), "");
    assert.ok($button.hasClass("hide"));
    stale_unread.confirm_mark_as_read();
    assert.equal(dialogs.length, 0);
});

run_test("the button shows once there is a backlog", async () => {
    reset();
    const {$button, $label} = make_button();
    get_response = {count: 12, days: 7};

    await stale_unread.refresh();

    assert.deepEqual(requests, [["GET", "/json/messages/stale_unread"]]);
    assert.equal(stale_unread.get_count(), 12);
    assert.ok(!$button.hasClass("hide"));
    assert.equal($label.text(), "translated: Mark read older than 7 days");
    assert.equal(stale_unread.label_if_any(), "translated: Mark read older than 7 days");
});

run_test("a failed count keeps the button hidden", async () => {
    reset();
    const {$button} = make_button();
    get_response = new Error("offline");

    await stale_unread.refresh();

    assert.equal(stale_unread.get_count(), 0);
    assert.ok($button.hasClass("hide"));
});

run_test("the dialog gives the count, and confirming marks them and shows a toast", async () => {
    reset();
    const {$button} = make_button();
    get_response = {count: 12, days: 7};
    await stale_unread.refresh();
    post_response = {count: 12};

    stale_unread.confirm_mark_as_read();

    assert.equal(dialogs.length, 1);
    assert.ok(dialogs[0].modal_content_html.includes("12"));
    assert.deepEqual(requests, [["GET", "/json/messages/stale_unread"]]);

    dialogs[0].on_click();
    await flush();

    assert.deepEqual(requests.at(-1), ["POST", "/json/messages/stale_unread"]);
    assert.equal(toasts.length, 1);
    assert.ok(toasts[0].text.includes("12"));
    assert.equal(stale_unread.get_count(), 0);
    assert.ok($button.hasClass("hide"));
});

run_test("a failed request shows an error toast with a retry", async () => {
    reset();
    make_button();
    get_response = {count: 3, days: 7};
    await stale_unread.refresh();
    post_response = new Error("500");

    stale_unread.confirm_mark_as_read();
    dialogs[0].on_click();
    await flush();

    assert.equal(toasts.length, 1);
    assert.equal(toasts[0].variant, "error");
    assert.equal(typeof toasts[0].on_retry, "function");
    assert.equal(stale_unread.get_count(), 3);
});
