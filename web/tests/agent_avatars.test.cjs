"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let next_response;
const requested_urls = [];
mock_esm("../src/channel", {
    get({url}) {
        requested_urls.push(url);
        return Promise.resolve(next_response);
    },
});

let subscribed = new Set();
mock_esm("../src/peer_data", {
    is_user_loaded_and_subscribed(stream_id, user_id) {
        return subscribed.has(`${stream_id}:${user_id}`);
    },
});

mock_esm("../src/user_settings", {user_settings: {default_language: "en"}});

let rerender_calls = 0;
mock_esm("../src/message_live_update", {
    rerender_messages_view() {
        rerender_calls += 1;
    },
});

const agent_avatars = zrequire("agent_avatars");

function profile(overrides) {
    return {bot_user_id: 1, name: "Kaki", avatar_shape: "circle", avatar_color: "", ...overrides};
}

run_test("initials_for_name", () => {
    assert.equal(agent_avatars.initials_for_name("Kaki"), "KA");
    assert.equal(agent_avatars.initials_for_name("Dita Anggraini"), "DA");
    assert.equal(agent_avatars.initials_for_name("  "), "");
});

// The module fetches the directory once per page, so the tests below
// share this one load.
run_test("agents load once, an unknown shape falls back to a circle", async () => {
    next_response = {
        profiles: [
            profile({bot_user_id: 10, name: "Kaki", avatar_color: "#FF6A3D"}),
            profile({bot_user_id: 11, name: "Odd", avatar_shape: "triangle"}),
            profile({
                bot_user_id: 21,
                name: "Ayame",
                avatar_shape: "ring",
                avatar_color: "#8B74FF",
            }),
            profile({bot_user_id: 22, name: "Matcha", avatar_shape: "box", avatar_color: ""}),
            {bot_user_id: 23, name: "Old server"},
        ],
    };
    assert.equal(agent_avatars.get_agent_for_bot_user_id(10), undefined, "not loaded yet");
    await agent_avatars.ensure_loaded();
    await agent_avatars.ensure_loaded();

    assert.deepEqual(requested_urls, ["/json/agent/profiles?offset=0&limit=50"]);
    assert.equal(rerender_calls, 1, "one re-render for the messages drawn before the load");
    assert.deepEqual(agent_avatars.get_agent_for_bot_user_id(10), {
        name: "Kaki",
        shape: "circle",
        color: "#FF6A3D",
    });
    assert.equal(agent_avatars.get_agent_for_bot_user_id(11).shape, "circle");
    assert.equal(agent_avatars.get_agent_for_bot_user_id(21).shape, "ring");
    assert.equal(agent_avatars.get_agent_for_bot_user_id(22).shape, "box");
    assert.deepEqual(agent_avatars.get_agent_for_bot_user_id(23), {
        name: "Old server",
        shape: "circle",
        color: "",
    });
    assert.equal(agent_avatars.get_agent_for_bot_user_id(999), undefined);
});

run_test("compose_mention_hint names up to three agents of the room with 'or'", () => {
    subscribed = new Set(["1:10", "1:999"]);
    assert.equal(agent_avatars.compose_mention_hint(1), "@Kaki");

    subscribed = new Set(["1:999"]);
    assert.equal(agent_avatars.compose_mention_hint(1), undefined);

    subscribed = new Set(["1:10", "1:21", "1:22"]);
    assert.equal(agent_avatars.compose_mention_hint(1), "@Kaki, @Ayame, or @Matcha");

    subscribed = new Set(["1:10", "1:11", "1:21", "1:22", "1:23"]);
    assert.equal(agent_avatars.compose_mention_hint(1), "@Kaki, @Odd, or @Ayame");

    subscribed = new Set(["2:10"]);
    assert.equal(agent_avatars.compose_mention_hint(1), undefined, "another room's agent");
});

run_test("room_composer_placeholder", () => {
    subscribed = new Set(["1:10", "1:21", "1:22"]);
    assert.equal(
        agent_avatars.room_composer_placeholder(1, "PR #214"),
        "translated: Write in PR #214… type @Kaki, @Ayame, or @Matcha to call an agent",
    );
    subscribed = new Set();
    assert.equal(
        agent_avatars.room_composer_placeholder(1, "PR #214"),
        "translated: Write in PR #214…",
    );
});
