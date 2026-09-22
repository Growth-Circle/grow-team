"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

let rendered = "";
let known_profiles = [];
let preflight_ids = [];
mock_esm("../src/markdown", {render: () => ({content: rendered})});
const api = mock_esm("../src/agent_api", {
    async list_profiles() {
        return {profiles: known_profiles, count: known_profiles.length};
    },
    async preflight_message(ids) {
        preflight_ids = ids;
        return {decisions: []};
    },
    async message_dispatch() {
        return {dispatch_receipts: []};
    },
});
const compose_banner = mock_esm("../src/compose_banner", {
    WARNING: "warning",
    SUCCESS: "success",
    CLASSNAMES: {agent_task_receipt_banner: "agent_task_receipt_banner"},
    clear_agent_task_receipt_banner: () => {},
    append_compose_banner_to_banner_list: () => true,
});
const {set_current_user} = zrequire("state_data");
set_current_user({user_id: 1});
const send = zrequire("agent_message_send");
function mentions(ids) {
    const rows = ids.map((id) => $(`<span id='mention-${id}'>`).attr("data-user-id", id));
    $("<div>").set_find_results(
        ".user-mention:not(.silent)[data-user-id]",
        rows.flatMap((row) => row.get()),
    );
}

run_test("a two-person bot DM can attach its exact target identity", async () => {
    rendered = "";
    mentions([]);
    known_profiles = [{id: "profile-a", bot_user_id: 90}];
    const result = await send.prepare({
        type: "private",
        content: "help",
        topic: "",
        recipient_ids: [90],
    });
    assert.deepEqual(result.profile_ids, ["profile-a"]);
    assert.equal(result.metadata_safe, true);
    assert.deepEqual(preflight_ids, ["profile-a"]);
});

run_test("parser disagreement cannot attach an incomplete target set", async () => {
    rendered =
        '<span class="user-mention" data-user-id="90">@A</span><span class="user-mention" data-user-id="91">@B</span>';
    mentions([90, 91]);
    known_profiles = [{id: "profile-a", bot_user_id: 90}];
    const result = await send.prepare({type: "stream", content: "@A @B", stream_id: 3, topic: "t"});
    assert.deepEqual(result.profile_ids, ["profile-a"]);
    assert.equal(result.metadata_safe, false);
});

run_test("silent mentions and code blocks have no client task lookup", () => {
    rendered = '<code>@A</code><span class="user-mention silent" data-user-id="90">A</span>';
    mentions([]);
    assert.equal(
        send.needs_target_lookup({type: "stream", content: "code", stream_id: 3, topic: "t"}),
        false,
    );
});

run_test("each receipt names its agent and links its accepted job", () => {
    const accepted_job_id = "11111111-1111-4111-8111-111111111111";
    const names = new Map([["a", "Helper"]]);
    const rows = send.receipt_rows(
        [
            {profile_id: "a", decision: "accepted", job_id: accepted_job_id},
            {profile_id: "b", decision: "rejected", job_id: null},
        ],
        names,
    );
    assert.equal(rows.length, 2);
    assert.equal(rows[0].name, "Helper");
    assert.match(rows[0].outcome, /started a task/);
    assert.equal(rows[0].job_url, `#agent-jobs/${accepted_job_id}`);
    assert.equal(rows[1].name, "translated: An agent you cannot view");
    assert.match(rows[1].outcome, /started no task/);
    assert.equal(rows[1].job_url, undefined);
});

run_test(
    "a sent message reports every receipt in one banner",
    async ({override, mock_template}) => {
        const accepted_job_id = "22222222-2222-4222-8222-222222222222";
        known_profiles = [{id: "a", name: "Helper"}];
        override(api, "message_dispatch", async () => ({
            dispatch_receipts: [
                {
                    profile_id: "a",
                    decision: "accepted",
                    reason: "",
                    job_id: accepted_job_id,
                    job_status: "queued",
                },
                {
                    profile_id: "b",
                    decision: "rejected",
                    reason: "runner_busy",
                    job_id: null,
                    job_status: null,
                },
            ],
        }));
        let appended = 0;
        override(compose_banner, "append_compose_banner_to_banner_list", () => {
            appended += 1;
            return true;
        });
        let captured_data;
        let captured_html;
        mock_template("compose_banner/agent_dispatch_receipt_banner.hbs", true, (data, html) => {
            captured_data = data;
            captured_html = html;
            return html;
        });

        await send.report_dispatch(81);

        assert.equal(appended, 1);
        assert.equal(captured_data.banner_type, compose_banner.WARNING);
        const hrefs = captured_html.match(/href="[^"]*"/g) ?? [];
        assert.deepEqual(hrefs, [`href="#agent-jobs/${accepted_job_id}"`]);
        const visible_text = captured_html.replaceAll(/<[^>]+>/g, " ");
        assert.match(visible_text, /started a task/);
        assert.match(visible_text, /started no task/);
    },
);

run_test("an unavailable receipt names a path that exists", async ({override, mock_template}) => {
    override(api, "message_dispatch", async () => {
        throw new Error("network");
    });
    let appended = 0;
    override(compose_banner, "append_compose_banner_to_banner_list", () => {
        appended += 1;
        return true;
    });
    let captured_data;
    let captured_html;
    mock_template("compose_banner/agent_dispatch_receipt_banner.hbs", true, (data, html) => {
        captured_data = data;
        captured_html = html;
        return html;
    });

    await send.report_dispatch(81);

    assert.equal(appended, 1);
    assert.equal(captured_data.banner_type, compose_banner.WARNING);
    const visible_text = captured_html.replaceAll(/<[^>]+>/g, " ");
    assert.match(visible_text, /message menu/);
    assert.ok(!visible_text.includes("/"), `unexpected path in: ${visible_text}`);
    assert.ok(
        !/\b(?:GET|POST|PUT|DELETE|PATCH)\b/.test(visible_text),
        `unexpected HTTP method in: ${visible_text}`,
    );
    assert.ok(!/endpoint/i.test(visible_text), `unexpected "endpoint" in: ${visible_text}`);
});
