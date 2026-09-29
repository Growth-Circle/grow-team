"use strict";

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

let next_response;
let next_failure;
let requested_urls = [];
mock_esm("../src/channel", {
    get({url}) {
        requested_urls.push(url);
        if (next_failure) {
            return Promise.reject(next_failure);
        }
        return Promise.resolve(next_response);
    },
});

mock_esm("../src/browser_history", {
    get_full_url: (hash) => `https://zulip.example/${hash}`,
});

mock_esm("../src/hash_util", {
    by_stream_topic_url: (stream_id, topic) => `#narrow/channel/${stream_id}/topic/${topic}`,
});

mock_esm("../src/util", {
    get_final_topic_display_name: (name) => (name === "" ? "general chat" : name),
});

let replaced_with;
set_global("window", {
    location: {
        replace(url) {
            replaced_with = url;
        },
    },
});

const room_topics_column = zrequire("room_topics_column");

async function flush() {
    // show() starts an async fetch and returns. These turns let the
    // mocked request settle and the code after it run.
    for (let i = 0; i < 5; i += 1) {
        await Promise.resolve();
    }
}

function topics_response() {
    return {
        topics: [
            {name: "alpha", max_id: 30, message_count: 5},
            {name: "Beta", max_id: 10, message_count: 1},
            {name: "", max_id: 5, message_count: 2},
        ],
    };
}

run_test("a bare room goes to its top topic and replaces the history entry", async () => {
    replaced_with = undefined;
    next_response = topics_response();
    room_topics_column.show(1, undefined, true);
    assert.ok($("#room-topics-column").html().includes("sj-skeleton"), "loading placeholder");
    await flush();
    assert.equal(replaced_with, "https://zulip.example/#narrow/channel/1/topic/alpha");
});

run_test("a room opened on a topic lists topics with counts and marks the open one", async () => {
    replaced_with = undefined;
    requested_urls = [];
    next_response = topics_response();
    room_topics_column.show(2, "beta", false);
    await flush();
    assert.equal(replaced_with, undefined);
    assert.deepEqual(requested_urls, ["/json/streams/2/topics"]);
    assert.equal($("#room-topics-column").attr("role"), "navigation");
    const html = $("#room-topics-column").html();
    assert.ok(html.includes("alpha"));
    assert.ok(html.includes("5 messages"));
    assert.ok(html.includes("general chat"), "the empty topic has a display name");
    // The topic matches "Beta" whatever the case, and shows "Open".
    assert.equal(html.split("room-topic-item-active").length - 1, 1);
    assert.ok(html.includes('aria-current="page"'));
    assert.ok(html.includes("Open"));
    assert.ok(html.includes("#narrow/channel/2/topic/alpha"));
});

run_test("a narrow with more terms than the room does not redirect", async () => {
    replaced_with = undefined;
    next_response = topics_response();
    room_topics_column.show(6, undefined, false);
    await flush();
    assert.equal(replaced_with, undefined);
    assert.ok($("#room-topics-column").html().includes("alpha"));
});

run_test("a topic switch draws from the last list, then refreshes it", async () => {
    requested_urls = [];
    next_response = {
        topics: [...topics_response().topics, {name: "new", max_id: 40, message_count: 1}],
    };
    room_topics_column.show(2, "alpha", false);
    // Drawn at once from the list of the previous visit.
    assert.ok(!$("#room-topics-column").html().includes("sj-skeleton"));
    assert.ok(!$("#room-topics-column").html().includes(">new<"));
    await flush();
    assert.deepEqual(requested_urls, ["/json/streams/2/topics"]);
    assert.ok($("#room-topics-column").html().includes(">new<"), "the refreshed list shows");
});

run_test("a bare room with a known list redirects without waiting", () => {
    replaced_with = undefined;
    room_topics_column.show(2, undefined, true);
    assert.equal(replaced_with, "https://zulip.example/#narrow/channel/2/topic/alpha");
});

run_test("a room with no topic stays on the room", async () => {
    replaced_with = undefined;
    next_response = {topics: []};
    room_topics_column.show(3, undefined, true);
    await flush();
    assert.equal(replaced_with, undefined);
    assert.ok(!$("#room-topics-column").html().includes("sj-skeleton"));
});

run_test("a failed load removes the loading placeholder", async () => {
    next_failure = new Error("network");
    room_topics_column.show(5, "x", false);
    await flush();
    next_failure = undefined;
    assert.equal($("#room-topics-column").html(), "");
});

run_test("a list that arrives after the user left the room is not drawn", async () => {
    next_response = topics_response();
    room_topics_column.show(7, "alpha", false);
    room_topics_column.hide();
    await flush();
    assert.equal($("#room-topics-column").html(), "");
});
