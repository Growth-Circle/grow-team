"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

let meta;
let digest;
let meta_failure;
let digest_failure;
let patch_failure;
let requests = [];
mock_esm("../src/channel", {
    get({url}) {
        requests.push(["GET", url]);
        if (url.endsWith("/meta")) {
            return meta_failure ? Promise.reject(meta_failure) : Promise.resolve(meta);
        }
        return digest_failure ? Promise.reject(digest_failure) : Promise.resolve(digest);
    },
    patch({url, data}) {
        requests.push(["PATCH", url, data]);
        if (patch_failure) {
            return Promise.reject(patch_failure);
        }
        meta = {...meta, summary_enabled: data.summary_enabled};
        return Promise.resolve(meta);
    },
});

let toasts = [];
mock_esm("../src/feedback_widget", {
    show_toast(opts) {
        toasts.push(opts);
    },
});

const listeners = new Map();
mock_esm("../src/live_updates", {
    on(type, cb) {
        listeners.set(type, cb);
    },
});

mock_esm("../src/hash_util", {
    // A readable stand-in for the real hash: operator/operand pairs.
    search_terms_to_hash: (terms) =>
        "#narrow" + terms.map((term) => `/${term.operator}/${term.operand}`).join(""),
    by_stream_url: (stream_id) => `#narrow/channel/${stream_id}`,
});

const pins_loaded = [];
mock_esm("../src/channel_pins", {
    ensure_loaded: (stream_id) => pins_loaded.push(stream_id),
});

const opened_pins = [];
mock_esm("../src/pins_drawer", {
    open: (stream_id) => opened_pins.push(stream_id),
});

const room_header = zrequire("room_header");

const stream = {stream_id: 7, name: "rilis"};

// The header binds its click handler once, on the first show(). The
// test harness clears every element after each test, so the first test
// keeps the handler for the tests after it. It acts on the state of the
// module, not on the element.
let click_toggle;
let click_pins;

function base_meta(overrides) {
    return {
        stream_id: 7,
        folder_name: "Proyek",
        owner: {id: 1, full_name: "Dita"},
        summary_enabled: true,
        people_count: 4,
        agent_count: 3,
        whatsapp: false,
        can_toggle_summary: true,
        ...overrides,
    };
}

async function flush() {
    for (let i = 0; i < 8; i += 1) {
        await Promise.resolve();
    }
}

async function open_room(overrides, digest_value) {
    requests = [];
    toasts = [];
    meta = base_meta(overrides);
    digest = digest_value ?? {digest: null};
    meta_failure = undefined;
    digest_failure = undefined;
    patch_failure = undefined;
    room_header.hide();
    room_header.show(stream, []);
    await flush();
}

function header_html() {
    return $("#room-header").html();
}

run_test("the header shows the room name, type, owner, and counts", async () => {
    room_header.hide();
    meta = base_meta({whatsapp: true});
    digest = {digest: null};
    requests = [];
    room_header.show(stream, []);
    click_toggle = $("#room-header").get_on_handler("click", ".room-header-summary-toggle");
    click_pins = $("#room-header").get_on_handler("click", ".room-header-tab-pins");
    assert.ok(header_html().includes("sj-skeleton"), "loading placeholder");
    await flush();
    const html = header_html();
    assert.ok(html.includes("# rilis"));
    assert.ok(html.includes("room-header-type-project"));
    // The test harness puts "translated: " before each translated text.
    assert.ok(html.includes("Owner: Dita · translated: 4 people · translated: 3 agents · WA"));
    // The Today and Drive screens are placeholders, so the header has no
    // links to them.
    assert.ok(!html.includes("room-header-back"));
    assert.ok(!html.includes("room-header-drive"));
});

run_test("the type chip follows the folder name", async () => {
    await open_room({folder_name: "Klien"});
    assert.ok(header_html().includes("room-header-type-client"));
    await open_room({folder_name: "Tim"});
    assert.ok(header_html().includes("room-header-type-team"));
    await open_room({folder_name: "Internal"});
    assert.ok(header_html().includes("room-header-type"));
    assert.ok(!header_html().includes("room-header-type-"));
    await open_room({folder_name: null, owner: null, people_count: 1, agent_count: 0});
    assert.ok(!header_html().includes("room-header-type"));
    assert.ok(header_html().includes("1 person · translated: 0 agents"));
});

run_test("a member who cannot change the summary sees the reason", async () => {
    await open_room({can_toggle_summary: false});
    const html = header_html();
    assert.ok(html.includes('aria-disabled="true"'));
    assert.ok(html.includes("Only the room owner can change the summary."));
    assert.ok(html.includes('aria-describedby="room-summary-reason"'));
    click_toggle();
    await flush();
    assert.ok(!requests.some(([method]) => method === "PATCH"));
});

run_test("the switch turns the summary off and says so", async () => {
    await open_room();
    assert.ok(header_html().includes('aria-checked="true"'));
    click_toggle();
    await flush();
    assert.deepEqual(
        requests.find(([method]) => method === "PATCH"),
        ["PATCH", "/json/streams/7/meta", {summary_enabled: false}],
    );
    assert.ok(header_html().includes('aria-checked="false"'));
    assert.ok(header_html().includes("summary: OFF"));
    assert.equal(
        toasts.at(-1).text,
        "translated: Summary is off. Kaki stops reading this room for summaries.",
    );
    assert.equal(toasts.at(-1).variant, "success");
});

run_test("a failed change keeps the old state and offers Retry", async () => {
    await open_room();
    patch_failure = new Error("network");
    click_toggle();
    await flush();
    assert.ok(header_html().includes('aria-checked="true"'), "the switch did not move");
    const toast = toasts.at(-1);
    assert.equal(toast.variant, "error");
    assert.equal(toast.text, "translated: Could not change the summary.");

    patch_failure = undefined;
    toast.on_retry();
    await flush();
    assert.ok(header_html().includes('aria-checked="false"'));
    assert.equal(
        toasts.at(-1).text,
        "translated: Summary is off. Kaki stops reading this room for summaries.",
    );
});

run_test("the banner shows Kaki's summary when the summary is on", async () => {
    await open_room({}, {digest: {message_count: 46, summary: "Rilis Jumat siap."}});
    const html = $("#room-summary-banner").html();
    assert.ok(html.includes("Kaki summarized 46 messages since yesterday"));
    assert.ok(html.includes("Rilis Jumat siap."));
});

run_test("no banner when the summary is off, empty, or missing", async () => {
    await open_room({summary_enabled: false}, {digest: {message_count: 9, summary: "x"}});
    assert.equal($("#room-summary-banner").html(), "");
    assert.ok(!requests.some(([, url]) => url.endsWith("/digest")), "no read while off");

    await open_room({}, {digest: {message_count: 9, summary: ""}});
    assert.equal($("#room-summary-banner").html(), "");

    await open_room({}, {digest: null});
    assert.equal($("#room-summary-banner").html(), "");

    await open_room({});
    digest_failure = new Error("not found");
    room_header.hide();
    room_header.show(stream, []);
    await flush();
    assert.equal($("#room-summary-banner").html(), "");
    assert.ok(header_html().includes("# rilis"), "the header still shows");
});

run_test("a failed load shows a toast with Retry", async () => {
    room_header.hide();
    toasts = [];
    meta_failure = new Error("network");
    room_header.show(stream, []);
    await flush();
    assert.equal(header_html(), "");
    assert.equal(toasts.at(-1).text, "translated: Could not load this room.");
    meta_failure = undefined;
    meta = base_meta();
    toasts.at(-1).on_retry();
    await flush();
    assert.ok(header_html().includes("# rilis"));
});

run_test("a topic switch in the same room keeps the header", async () => {
    await open_room();
    requests = [];
    room_header.show(stream, []);
    await flush();
    assert.deepEqual(requests, [], "no new request");
    assert.ok(header_html().includes("# rilis"));
    room_header.show({stream_id: 8, name: "other"}, []);
    assert.ok(header_html().includes("sj-skeleton"), "another room starts over");
    await flush();
});

run_test("a room_meta event refreshes the open room only", async () => {
    await open_room();
    requests = [];
    meta = base_meta({owner: {id: 2, full_name: "Bimo"}});
    const on_event = listeners.get("room_meta");
    on_event({type: "room_meta", stream_id: 99});
    await flush();
    assert.deepEqual(requests, []);
    on_event({type: "room_meta", stream_id: 7});
    await flush();
    assert.ok(header_html().includes("Owner: Bimo"));
});

run_test("hide clears the header and the banner", async () => {
    await open_room({}, {digest: {message_count: 1, summary: "x"}});
    room_header.hide();
    assert.equal(header_html(), "");
    assert.equal($("#room-summary-banner").html(), "");
});

// This test comes last: it makes the header stop asking for summaries.
run_test("a server without summaries is asked once", async () => {
    await open_room({}, undefined);
    requests = [];
    digest_failure = {status: 404};
    room_header.hide();
    room_header.show(stream, []);
    await flush();
    assert.equal(requests.filter(([, url]) => url.endsWith("/digest")).length, 1);
    assert.equal($("#room-summary-banner").html(), "");

    requests = [];
    room_header.hide();
    room_header.show(stream, []);
    await flush();
    assert.equal(requests.filter(([, url]) => url.endsWith("/digest")).length, 0);
    assert.ok(header_html().includes("# rilis"));
});

function tabs_html() {
    return $("#room-header .room-header-tabs").html();
}

function show_room(terms) {
    room_header.show(stream, terms);
}

run_test("the tabs are Chat, Pins, Files and Links, and Chat is the current one", async () => {
    requests = [];
    meta = base_meta();
    digest = {digest: null};
    room_header.hide();
    show_room([
        {operator: "channel", operand: "7"},
        {operator: "topic", operand: "Deploys"},
    ]);
    await flush();

    const html = tabs_html();
    assert.ok(html.includes('href="#narrow/channel/7/topic/Deploys"'));
    assert.ok(html.includes('href="#narrow/channel/7/has/attachment"'));
    assert.ok(html.includes('href="#narrow/channel/7/has/link"'));
    // Pins opens a drawer, so it is a button without a link.
    assert.ok(html.includes('class="room-header-tab room-header-tab-pins"'));
    for (const label of ["Chat", "Pins", "Files", "Links"]) {
        assert.ok(html.includes(`translated: ${label}`), label);
    }
    assert.equal(html.match(/aria-current="page"/g).length, 1);
    assert.ok(/data-tab="chat" href="[^"]*" aria-current="page"/.test(html));
});

run_test("the tab follows the narrow, and Chat goes back to the last topic", async () => {
    requests = [];
    meta = base_meta();
    digest = {digest: null};
    room_header.hide();
    show_room([
        {operator: "channel", operand: "7"},
        {operator: "topic", operand: "Deploys"},
    ]);
    await flush();

    show_room([
        {operator: "channel", operand: "7"},
        {operator: "has", operand: "attachment"},
    ]);
    let html = tabs_html();
    assert.ok(/data-tab="files" href="[^"]*" aria-current="page"/.test(html));
    assert.ok(!/data-tab="chat"[^>]*aria-current/.test(html));
    // The room did not load again: only the tabs changed.
    assert.equal(requests.filter(([, url]) => url.endsWith("/meta")).length, 1);
    assert.ok(html.includes('href="#narrow/channel/7/topic/Deploys"'));

    show_room([
        {operator: "channel", operand: "7"},
        {operator: "has", operand: "link"},
    ]);
    html = tabs_html();
    assert.ok(/data-tab="links" href="[^"]*" aria-current="page"/.test(html));
});

run_test("a room opened on Files sends Chat to the room itself", async () => {
    requests = [];
    meta = base_meta();
    digest = {digest: null};
    room_header.hide();
    show_room([
        {operator: "channel", operand: "7"},
        {operator: "has", operand: "attachment"},
    ]);
    await flush();

    assert.ok(tabs_html().includes('data-tab="chat" href="#narrow/channel/7"'));
});

run_test("only a plain has:attachment or has:link narrow is a tab", () => {
    const channel_term = {operator: "channel", operand: "7"};
    assert.equal(room_header.tab_for_terms([channel_term]), "chat");
    assert.equal(
        room_header.tab_for_terms([channel_term, {operator: "has", operand: "image"}]),
        "chat",
    );
    assert.equal(
        room_header.tab_for_terms([
            channel_term,
            {operator: "has", operand: "link", negated: true},
        ]),
        "chat",
    );
    assert.equal(
        room_header.tab_for_terms([channel_term, {operator: "has", operand: "link"}]),
        "links",
    );
});

run_test("the pins load with the room, and the Pins tab opens the drawer", async () => {
    await open_room();
    assert.ok(pins_loaded.includes(7));

    click_pins();
    assert.deepEqual(opened_pins, [7]);
});
