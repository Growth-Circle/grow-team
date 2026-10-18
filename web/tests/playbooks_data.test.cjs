"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let next_response;
let last_call;
mock_esm("../src/channel", {
    get(options) {
        last_call = {method: "GET", ...options};
        return Promise.resolve(next_response);
    },
    post(options) {
        last_call = {method: "POST", ...options};
        return Promise.resolve(next_response);
    },
    patch(options) {
        last_call = {method: "PATCH", ...options};
        return Promise.resolve(next_response);
    },
});

const playbooks_data = zrequire("playbooks_data");

function make_playbook(overrides) {
    return {
        id: 1,
        title: "Evaluasi 4 jam",
        slug: "evaluasi-4-jam",
        scope: "shared",
        owner_id: 10,
        archived: false,
        date_created: 1_760_000_000,
        latest_revision: {number: 1, author_id: 10, date_created: 1_760_000_000, summary: ""},
        ...overrides,
    };
}

run_test("parse_route", () => {
    assert.deepEqual(playbooks_data.parse_route([]), {kind: "list", tab: "shared"});
    assert.deepEqual(playbooks_data.parse_route(["mine"]), {kind: "list", tab: "mine"});
    assert.deepEqual(playbooks_data.parse_route(["new"]), {kind: "new", scope: "shared"});
    assert.deepEqual(playbooks_data.parse_route(["new", "mine"]), {kind: "new", scope: "personal"});
    assert.deepEqual(playbooks_data.parse_route(["12"]), {
        kind: "detail",
        id: 12,
        panel: "view",
        revision: undefined,
    });
    assert.deepEqual(playbooks_data.parse_route(["12", "edit"]), {
        kind: "detail",
        id: 12,
        panel: "edit",
        revision: undefined,
    });
    assert.deepEqual(playbooks_data.parse_route(["12", "history"]), {
        kind: "detail",
        id: 12,
        panel: "history",
        revision: undefined,
    });
    assert.deepEqual(playbooks_data.parse_route(["12", "history", "3"]), {
        kind: "detail",
        id: 12,
        panel: "history",
        revision: 3,
    });
    // An address that names nothing real opens the list.
    for (const args of [["0"], ["-1"], ["1.5"], ["abc"], ["007"], ["edit"]]) {
        assert.deepEqual(playbooks_data.parse_route(args), {kind: "list", tab: "shared"}, args);
    }
    // A revision that is not a number is dropped, not guessed.
    assert.equal(playbooks_data.parse_route(["12", "history", "x"]).revision, undefined);
});

run_test("route_hash_round_trips", () => {
    for (const hash of [
        "#playbooks",
        "#playbooks/mine",
        "#playbooks/new",
        "#playbooks/new/mine",
        "#playbooks/12",
        "#playbooks/12/edit",
        "#playbooks/12/history",
        "#playbooks/12/history/3",
    ]) {
        const args = hash.split("/").slice(1);
        assert.equal(playbooks_data.route_hash(playbooks_data.parse_route(args)), hash);
    }
    assert.equal(playbooks_data.detail_hash(7, "history", 2), "#playbooks/7/history/2");
    assert.equal(playbooks_data.detail_hash(7), "#playbooks/7");
});

run_test("tabs_and_order", () => {
    const shared_old = make_playbook({id: 1});
    const shared_new = make_playbook({
        id: 2,
        latest_revision: {number: 2, author_id: 10, date_created: 1_760_100_000, summary: ""},
    });
    const mine = make_playbook({id: 3, scope: "personal"});
    const same_time = make_playbook({id: 4});

    assert.equal(playbooks_data.tab_of(shared_old), "shared");
    assert.equal(playbooks_data.tab_of(mine), "mine");
    assert.deepEqual(
        playbooks_data.playbooks_in_tab([shared_old, mine, shared_new], "mine").map((p) => p.id),
        [3],
    );
    // The most recently changed come first, and a tie goes to the newer playbook.
    assert.deepEqual(
        playbooks_data
            .sort_playbooks([shared_old, same_time, shared_new, mine])
            .map((playbook) => playbook.id),
        [2, 4, 3, 1],
    );
});

run_test("dirty_and_can_save", () => {
    const saved = {title: "Weekly report", content: "Collect the numbers."};
    const same = {title: "Weekly report", content: "Collect the numbers.", summary: ""};

    assert.equal(playbooks_data.is_dirty(saved, same), false);
    // A note alone is not a change.
    assert.equal(playbooks_data.is_dirty(saved, {...same, summary: "Because"}), false);
    // Spaces around the title are not a change either: the server removes them.
    assert.equal(playbooks_data.is_dirty(saved, {...same, title: "  Weekly report "}), false);
    assert.equal(playbooks_data.is_dirty(saved, {...same, title: "Monthly report"}), true);
    assert.equal(
        playbooks_data.is_dirty(saved, {...same, content: "Collect the new numbers."}),
        true,
    );

    assert.equal(playbooks_data.can_save(saved, same), false);
    assert.equal(playbooks_data.can_save(saved, {...same, content: "Changed"}), true);
    // A blank title is never saved, and neither is a text over the limit.
    assert.equal(
        playbooks_data.can_save(saved, {...same, title: "   ", content: "Changed"}),
        false,
    );
    assert.equal(
        playbooks_data.can_save(saved, {
            ...same,
            content: "x".repeat(playbooks_data.MAX_CONTENT_LENGTH + 1),
        }),
        false,
    );
    // A new playbook has nothing saved to compare with, and needs a title and some text.
    assert.equal(playbooks_data.can_save(undefined, {title: "New", content: "x", summary: ""}), true);
    assert.equal(
        playbooks_data.can_save(undefined, {title: "New", content: " \n", summary: ""}),
        false,
    );
    assert.equal(playbooks_data.can_save(undefined, {title: "", content: "x", summary: ""}), false);
});

run_test("parse_diff", () => {
    assert.deepEqual(playbooks_data.parse_diff(""), []);
    assert.deepEqual(
        playbooks_data.parse_diff(
            [
                "--- revision 1",
                "+++ revision 2",
                "@@ -1,3 +1,3 @@",
                " One",
                "-Two",
                "+Two and a half",
                " ",
                "+--- a line that starts with dashes",
            ].join("\n"),
        ),
        [
            {kind: "hunk", text: "@@ -1,3 +1,3 @@"},
            {kind: "context", text: "One"},
            {kind: "removed", text: "Two"},
            {kind: "added", text: "Two and a half"},
            {kind: "context", text: ""},
            {kind: "added", text: "--- a line that starts with dashes"},
        ],
    );
});

run_test("requests", async () => {
    next_response = {result: "success", msg: "", playbooks: [make_playbook({})]};
    const list = await playbooks_data.fetch_playbooks({archived: false, search: "  evaluasi "});
    assert.equal(list.length, 1);
    assert.deepEqual(last_call.data, {archived: "false", search: "evaluasi"});

    await playbooks_data.fetch_playbooks({archived: true, search: "   "});
    assert.deepEqual(last_call.data, {archived: "true"});

    next_response = {
        result: "success",
        msg: "",
        revision: {
            number: 2,
            author_id: 10,
            date_created: 1,
            summary: "",
            content: "# Hi",
            rendered_content: "<h1>Hi</h1>",
        },
    };
    const revision = await playbooks_data.fetch_revision(4, 2);
    assert.equal(last_call.url, "/json/playbooks/4/revisions/2");
    assert.equal(revision.rendered_content, "<h1>Hi</h1>");

    next_response = {
        result: "success",
        msg: "",
        diff: "--- revision 1\n+++ revision 2\n@@ -1 +1 @@\n-a\n+b",
    };
    const lines = await playbooks_data.fetch_diff(4, 1, 2);
    assert.deepEqual(last_call.data, {from_revision: 1, to_revision: 2});
    assert.deepEqual(
        lines.map((line) => line.kind),
        ["hunk", "removed", "added"],
    );

    next_response = {result: "success", msg: "", rendered_content: "<p>x</p>"};
    assert.equal(await playbooks_data.render_preview("x"), "<p>x</p>");
    assert.equal(last_call.url, "/json/playbooks/render");

    // A response that does not match what the server documents is an error.
    next_response = {result: "success", msg: "", playbooks: [{id: "1"}]};
    await assert.rejects(playbooks_data.fetch_playbooks({archived: false, search: ""}));
});
