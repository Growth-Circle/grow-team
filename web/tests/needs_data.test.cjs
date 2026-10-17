"use strict";

const assert = require("node:assert/strict");

const {clock, mock_esm, zrequire} = require("./lib/namespace.cjs");
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
});

const needs_data = zrequire("needs_data");

function make_item(overrides) {
    return {
        id: "approval:1",
        kind: "approval",
        actor: {
            type: "agent",
            id: 7,
            name: "Ramma",
            agent_role: "worker",
            shape: "circle",
            color: "#ff6a3d",
            initials: "",
        },
        stream_id: null,
        topic: null,
        time: "2026-09-27T00:00:00Z",
        text: "Deploy the release.",
        attachment: null,
        actions: ["approve"],
        expires_at: "2026-09-27T01:00:00Z",
        expired: false,
        resolved_at: null,
        resolved_action: null,
        job_id: "job-1",
        approval_version: 3,
        operation_hash: "hash-1",
        nonce: "nonce-1",
        job_version: null,
        task: null,
        broadcast: false,
        ...overrides,
    };
}

const show_all = {show_team_wide: true};

run_test("a tab shows only its own kind, and all shows every kind", () => {
    const items = [
        make_item({id: "approval:1", kind: "approval"}),
        make_item({id: "mention:2", kind: "mention"}),
        make_item({id: "decision:3", kind: "decision"}),
    ];
    assert.deepEqual(
        needs_data.items_for_tab(items, "approval", show_all).map((item) => item.id),
        ["approval:1"],
    );
    assert.deepEqual(
        needs_data.items_for_tab(items, "mentions", show_all).map((item) => item.id),
        ["mention:2"],
    );
    assert.equal(needs_data.items_for_tab(items, "all", show_all).length, 3);
});

run_test("each tab count matches the items that tab shows", () => {
    const items = [
        make_item({id: "approval:1", kind: "approval"}),
        make_item({id: "approval:2", kind: "approval"}),
        make_item({id: "mention:3", kind: "mention"}),
    ];
    assert.equal(needs_data.count_for_tab(items, "approval", show_all), 2);
    assert.equal(needs_data.count_for_tab(items, "mentions", show_all), 1);
    assert.equal(needs_data.count_for_tab(items, "decision", show_all), 0);
    assert.equal(needs_data.count_for_tab(items, "all", show_all), 3);

    // The person handles one item: its tab and the "all" tab go down at once.
    items.splice(0, 1);
    assert.equal(needs_data.count_for_tab(items, "approval", show_all), 1);
    assert.equal(needs_data.count_for_tab(items, "all", show_all), 2);
});

run_test("team-wide mentions are hidden until the person asks, and counted apart", () => {
    const items = [
        make_item({id: "mention:1", kind: "mention"}),
        make_item({id: "mention:2", kind: "mention", broadcast: true}),
        make_item({id: "card_mention:3", kind: "card_mention", broadcast: true}),
        make_item({id: "approval:4", kind: "approval"}),
    ];
    const ids = (tab, options) =>
        needs_data.items_for_tab(items, tab, options).map((item) => item.id);

    assert.deepEqual(ids("mentions", {show_team_wide: false}), ["mention:1"]);
    assert.deepEqual(ids("mentions", {show_team_wide: true}), [
        "mention:1",
        "mention:2",
        "card_mention:3",
    ]);
    // The all tab follows the same switch.
    assert.deepEqual(ids("all", {show_team_wide: false}), ["mention:1", "approval:4"]);
    assert.equal(ids("all", {show_team_wide: true}).length, 4);

    // A tab counts the cards it shows, so the number follows the switch.
    assert.equal(needs_data.count_for_tab(items, "mentions", {show_team_wide: false}), 1);
    assert.equal(needs_data.count_for_tab(items, "mentions", show_all), 3);
    assert.equal(needs_data.count_for_tab(items, "all", {show_team_wide: false}), 2);
    assert.equal(needs_data.count_for_tab(items, "all", show_all), 4);
    // The sidebar number does not follow it.
    assert.equal(needs_data.personal_count(items), 2);
    assert.equal(needs_data.team_wide_count_for_tab(items, "mentions"), 2);
    assert.equal(needs_data.team_wide_count_for_tab(items, "all"), 2);
    assert.equal(needs_data.team_wide_count_for_tab(items, "approval"), 0);
});

run_test("an address from before the tabs merged opens the tab that holds its kind", () => {
    assert.equal(needs_data.tab_for_hash_arg("mention"), "mentions");
    assert.equal(needs_data.tab_for_hash_arg("card_mention"), "mentions");
    assert.equal(needs_data.tab_for_hash_arg("card_assigned"), "assigned");
    assert.equal(needs_data.tab_for_hash_arg("approval"), "approval");
    assert.equal(needs_data.tab_for_hash_arg("decision"), "decision");
    for (const tab of needs_data.NEED_TABS) {
        assert.equal(needs_data.tab_for_hash_arg(tab), tab);
    }
    // An item address is not a tab.
    assert.equal(needs_data.tab_for_hash_arg("mention:42"), undefined);
    assert.equal(needs_data.tab_for_hash_arg("approval:6f1c-77"), undefined);
    assert.equal(needs_data.tab_for_hash_arg(""), undefined);
    assert.equal(needs_data.tab_for_hash_arg(undefined), undefined);
});

run_test("a decision offers the choices the agent gave, and nothing else", () => {
    const with_choices = make_item({kind: "decision", actions: ["Version A", " ", "Version B"]});
    assert.deepEqual(needs_data.decision_options(with_choices), ["Version A", "Version B"]);

    // An open question has no choices. The person answers in the room.
    const open_question = make_item({kind: "decision", actions: []});
    assert.deepEqual(needs_data.decision_options(open_question), []);
});

run_test("the raw id of an item is what follows its kind", () => {
    assert.equal(needs_data.raw_id("mention:42"), "42");
    assert.equal(needs_data.raw_id("approval:6f1c-77"), "6f1c-77");
});

run_test("the open list is fetched from the needs endpoint", async () => {
    next_response = {
        items: [make_item()],
        counts: {approval: 1, mention: 0, decision: 0, card_mention: 0, card_assigned: 0, all: 1},
        broadcast_counts: {
            approval: 0,
            mention: 2,
            decision: 0,
            card_mention: 0,
            card_assigned: 0,
            all: 2,
        },
    };
    const result = await needs_data.fetch_open();
    assert.equal(last_call.method, "GET");
    assert.equal(last_call.url, "/json/needs");
    assert.equal(result.items.length, 1);
    assert.equal(result.counts.all, 1);
    assert.equal(result.broadcast_counts.all, 2);
});

run_test("today's done list asks for resolved items since today", async () => {
    next_response = {
        items: [],
        counts: {approval: 0, mention: 0, decision: 0, card_mention: 0, card_assigned: 0, all: 0},
        broadcast_counts: {
            approval: 0,
            mention: 0,
            decision: 0,
            card_mention: 0,
            card_assigned: 0,
            all: 0,
        },
    };
    await needs_data.fetch_done_today();
    assert.equal(last_call.url, "/json/needs?status=resolved&since=today");
});

run_test("undoing before the delay cancels the action", async () => {
    let commits = 0;
    let settled;
    needs_data.schedule_pending(
        "approval:1",
        () => {
            commits += 1;
            return Promise.resolve();
        },
        (error) => {
            settled = error;
        },
    );
    assert.ok(needs_data.is_pending("approval:1"));
    assert.ok(needs_data.cancel_pending("approval:1"));
    await clock.runAllAsync();
    assert.equal(commits, 0);
    assert.equal(settled, undefined);
    assert.ok(!needs_data.is_pending("approval:1"));
    // Undoing twice is a no-op, not an error.
    assert.ok(!needs_data.cancel_pending("approval:1"));
});

run_test("undoing one item leaves the other pending items alone", async () => {
    const commits = [];
    needs_data.schedule_pending(
        "approval:10",
        () => {
            commits.push("approval:10");
            return Promise.resolve();
        },
        () => {},
    );
    needs_data.schedule_pending(
        "approval:11",
        () => {
            commits.push("approval:11");
            return Promise.resolve();
        },
        () => {},
    );
    assert.ok(needs_data.cancel_pending("approval:10"));
    assert.ok(needs_data.is_pending("approval:11"));
    await clock.tickAsync(needs_data.UNDO_DELAY_MS);
    assert.deepEqual(commits, ["approval:11"]);
});

run_test("scheduling an item again sends its action once", async () => {
    let commits = 0;
    const commit = () => {
        commits += 1;
        return Promise.resolve();
    };
    needs_data.schedule_pending("approval:12", commit, () => {});
    needs_data.schedule_pending("approval:12", commit, () => {});
    await clock.tickAsync(needs_data.UNDO_DELAY_MS * 2);
    assert.equal(commits, 1);
});

run_test("the action commits once the undo window passes", async () => {
    let commits = 0;
    let settled = "not yet called";
    needs_data.schedule_pending(
        "approval:2",
        () => {
            commits += 1;
            return Promise.resolve();
        },
        (error) => {
            settled = error;
        },
    );
    await clock.tickAsync(needs_data.UNDO_DELAY_MS);
    assert.equal(commits, 1);
    assert.equal(settled, undefined);
    assert.ok(!needs_data.is_pending("approval:2"));
});

run_test("a commit that fails reports its error instead of throwing", async () => {
    const failure = new Error("network down");
    let settled;
    needs_data.schedule_pending(
        "approval:3",
        () => Promise.reject(failure),
        (error) => {
            settled = error;
        },
    );
    await clock.tickAsync(needs_data.UNDO_DELAY_MS);
    assert.equal(settled, failure);
});

run_test("approving sends the operation hash and nonce for that approval", async () => {
    next_response = {schema_version: 1, approval_id: "1", decision: "approved", version: 4};
    await needs_data.commit_approve(make_item({id: "approval:1", approval_version: 3}));
    assert.equal(last_call.url, "/json/agent/approvals/1/decision");
    assert.deepEqual(JSON.parse(last_call.data.payload), {
        schema_version: 1,
        expected_version: 3,
        operation_hash: "hash-1",
        nonce: "nonce-1",
        decision: "approved",
    });
});

run_test("choosing a decision option answers that job", async () => {
    next_response = {schema_version: 1};
    await needs_data.commit_decision(
        make_item({id: "decision:9", kind: "decision", job_id: "9", job_version: 2}),
        "Pilih A",
    );
    assert.equal(last_call.url, "/json/agent/jobs/9/inputs");
    const payload = JSON.parse(last_call.data.payload);
    assert.equal(payload.expected_version, 2);
    assert.equal(payload.text, "Pilih A");
    assert.equal(payload.input_type, "answer");
    assert.equal(typeof payload.client_key, "string");
});

run_test("a decision item without a job id cannot be answered", async () => {
    await assert.rejects(
        async () =>
            needs_data.commit_decision(
                make_item({id: "decision:9", kind: "decision", job_id: null}),
                "Pilih A",
            ),
        /job id/,
    );
});

run_test("marking a mention done resolves that message", async () => {
    next_response = {};
    await needs_data.commit_mention_done(make_item({id: "mention:42", kind: "mention"}));
    assert.equal(last_call.method, "POST");
    assert.equal(last_call.url, "/json/needs/mentions/42/resolve");
});

const card = {id: 8, display_id: "GT-8", title: "Write the release notes"};

run_test("a card notification is listed under all and its own tab", () => {
    const items = [
        make_item({id: "mention:1", kind: "mention"}),
        make_item({id: "card_mention:2", kind: "card_mention", task: card}),
        make_item({id: "card_assigned:3", kind: "card_assigned", task: card}),
        make_item({id: "card_assigned:4", kind: "card_assigned", task: card}),
    ];
    assert.equal(needs_data.count_for_tab(items, "all", show_all), 4);
    // A mention in chat and a mention in a card note share one tab.
    assert.equal(needs_data.count_for_tab(items, "mentions", show_all), 2);
    assert.equal(needs_data.count_for_tab(items, "assigned", show_all), 2);
    assert.deepEqual(
        needs_data.items_for_tab(items, "mentions", show_all).map((item) => item.id),
        ["mention:1", "card_mention:2"],
    );
    assert.deepEqual(
        needs_data.items_for_tab(items, "assigned", show_all).map((item) => item.id),
        ["card_assigned:3", "card_assigned:4"],
    );
    assert.ok(needs_data.is_card_kind("card_mention"));
    assert.ok(needs_data.is_card_kind("card_assigned"));
    assert.ok(!needs_data.is_card_kind("mention"));
    assert.ok(!needs_data.is_card_kind("approval"));
});

run_test("a mention in a note shows the card title and the note", () => {
    const mention = make_item({
        id: "card_mention:2",
        kind: "card_mention",
        task: card,
        text: "Can you check the dates, @Ari?",
    });
    assert.deepEqual(needs_data.card_text(mention), {
        text: "Write the release notes",
        excerpt: "Can you check the dates, @Ari?",
    });
});

run_test("a new responsibility shows the card title alone", () => {
    const assigned = make_item({
        id: "card_assigned:3",
        kind: "card_assigned",
        task: card,
        text: "Write the release notes",
    });
    assert.deepEqual(needs_data.card_text(assigned), {
        text: "Write the release notes",
        excerpt: "",
    });
});

run_test("a notification about a card the person cannot see keeps its own text", () => {
    const mention = make_item({id: "card_mention:2", kind: "card_mention", text: "Hello"});
    assert.deepEqual(needs_data.card_text(mention), {text: "Hello", excerpt: ""});
});

run_test("the list parses card notifications and their card", async () => {
    next_response = {
        items: [make_item({id: "card_mention:2", kind: "card_mention", task: card})],
        counts: {approval: 0, mention: 0, decision: 0, card_mention: 1, card_assigned: 0, all: 1},
        broadcast_counts: {
            approval: 0,
            mention: 2,
            decision: 0,
            card_mention: 0,
            card_assigned: 0,
            all: 2,
        },
    };
    const result = await needs_data.fetch_open();
    assert.equal(result.items[0].task.display_id, "GT-8");
    assert.equal(result.counts.card_mention, 1);
});

run_test("marking a card notification as seen resolves that notification", async () => {
    next_response = {};
    await needs_data.commit_card_seen(
        make_item({id: "card_assigned:17", kind: "card_assigned", task: card}),
    );
    assert.equal(last_call.method, "POST");
    assert.equal(last_call.url, "/json/needs/card_notifications/17/resolve");
});

run_test("open mentions from one topic make one group, at the place of the newest", () => {
    const mention = (id, topic, overrides) =>
        make_item({id, kind: "mention", stream_id: 5, topic, ...overrides});
    const items = [
        mention("mention:9", "Cek pagi"),
        make_item({id: "approval:1"}),
        mention("mention:8", "Rekap"),
        mention("mention:7", "Cek pagi"),
        mention("mention:6", "Cek pagi"),
    ];
    const rows = needs_data.rows_for_items(items);
    assert.equal(rows.length, 3);
    assert.deepEqual(
        rows[0].group.items.map((item) => item.id),
        ["mention:9", "mention:7", "mention:6"],
    );
    assert.equal(rows[0].group.key, "5/Cek pagi");
    assert.equal(rows[0].group.topic, "Cek pagi");
    // A topic with one item, and any other kind, stay plain cards.
    assert.equal(rows[1].item.id, "approval:1");
    assert.equal(rows[2].item.id, "mention:8");
});

run_test("the same topic name in two channels is two groups, and a DM never groups", () => {
    const mention = (id, stream_id) => make_item({id, kind: "mention", stream_id, topic: "Cek"});
    const rows = needs_data.rows_for_items([
        mention("mention:4", 1),
        mention("mention:3", 2),
        mention("mention:2", 1),
        mention("mention:1", 2),
        make_item({id: "mention:20", kind: "mention", stream_id: null, topic: null}),
        make_item({id: "mention:21", kind: "mention", stream_id: null, topic: null}),
    ]);
    assert.deepEqual(
        rows.map((row) => ("group" in row ? row.group.key : row.item.id)),
        ["1/Cek", "2/Cek", "mention:20", "mention:21"],
    );
});

run_test("group_key_for_item names a group only when the topic has more than one item", () => {
    const items = [
        make_item({id: "mention:3", kind: "mention", stream_id: 5, topic: "A"}),
        make_item({id: "mention:2", kind: "mention", stream_id: 5, topic: "A"}),
        make_item({id: "mention:1", kind: "mention", stream_id: 5, topic: "B"}),
    ];
    assert.equal(needs_data.group_key_for_item(items[0], items), "5/A");
    assert.equal(needs_data.group_key_for_item(items[2], items), undefined);
});

run_test("only mentions and card items can be marked done together", () => {
    const items = [
        make_item({id: "approval:1"}),
        make_item({id: "decision:2", kind: "decision"}),
        make_item({id: "mention:3", kind: "mention", broadcast: true}),
        make_item({id: "mention:4", kind: "mention"}),
        make_item({id: "card_assigned:5", kind: "card_assigned"}),
    ];
    const ids = (tab, options) =>
        needs_data.resolvable_items_for_tab(items, tab, options).map((item) => item.id);
    // Team-wide mentions that the tab hides are not marked done unseen.
    assert.deepEqual(ids("all", {show_team_wide: false}), ["mention:4", "card_assigned:5"]);
    assert.deepEqual(ids("all", show_all), ["mention:3", "mention:4", "card_assigned:5"]);
    assert.deepEqual(ids("approval", show_all), []);
});

run_test("marking items done together sends their ids and reports what failed", async () => {
    next_response = {done: ["mention:1"], failed: ["mention:2"]};
    const items = [
        make_item({id: "mention:1", kind: "mention"}),
        make_item({id: "mention:2", kind: "mention"}),
    ];
    const result = await needs_data.commit_bulk_done(items);
    assert.equal(last_call.method, "POST");
    assert.equal(last_call.url, "/json/needs/resolve");
    assert.deepEqual(JSON.parse(last_call.data.need_ids), ["mention:1", "mention:2"]);
    assert.deepEqual(result, {done: ["mention:1"], failed: ["mention:2"]});

    next_response = {done: ["mention:1"], failed: []};
    await needs_data.commit_bulk_undo([items[0]]);
    assert.equal(last_call.url, "/json/needs/unresolve");
});

run_test("snoozing returns when the items list again, and can be undone", async () => {
    next_response = {until: "2026-10-18T00:00:00+00:00"};
    const item = make_item({id: "mention:1", kind: "mention"});
    const until = await needs_data.commit_snooze([item]);
    assert.equal(last_call.url, "/json/needs/snooze");
    assert.equal(until.toISOString(), "2026-10-18T00:00:00.000Z");

    next_response = {};
    await needs_data.commit_unsnooze([item]);
    assert.equal(last_call.url, "/json/needs/unsnooze");
});
