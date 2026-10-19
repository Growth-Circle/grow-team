"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let requested_urls = [];
let pending_request;
mock_esm("../src/channel", {
    get(opts) {
        requested_urls.push(opts.url);
        pending_request = opts;
        return Promise.resolve();
    },
});

const listeners = [];
mock_esm("../src/live_updates", {
    on(type, callback) {
        listeners.push({type, callback});
    },
});

const agent_status_data = zrequire("agent_status_data");

const JOB_ID = "00000000-0000-4000-8000-000000000041";

function make_item(overrides) {
    return {
        id: `job:${JOB_ID}`,
        kind: "job",
        title: "Summarize the sprint",
        status: "running",
        outcome: null,
        detail: null,
        time: "2026-09-25T09:00:00Z",
        job_id: JOB_ID,
        message_id: 101,
        stream_id: 3,
        topic: "rilis-v2",
        ...overrides,
    };
}

function make_status(overrides) {
    return {in_progress: [], waiting_on_you: [], decided: [], ...overrides};
}

function reset() {
    agent_status_data.clear();
    requested_urls = [];
    pending_request = undefined;
}

run_test("the status is unknown until it is loaded", () => {
    reset();
    assert.equal(agent_status_data.get_status("p1"), undefined);
    assert.equal(agent_status_data.waiting_count("p1"), undefined);

    const waiting = make_item({kind: "decision", status: "waiting_for_input"});
    agent_status_data.set_status("p1", make_status({waiting_on_you: [waiting, waiting]}));
    assert.equal(agent_status_data.waiting_count("p1"), 2);
    assert.equal(agent_status_data.waiting_count("p2"), undefined);
    assert.equal(agent_status_data.get_status("p1").waiting_on_you.length, 2);
});

run_test("a load reads the status of the agent and tells the listeners", () => {
    reset();
    const changed = [];
    const stop = agent_status_data.on_change((profile_id) => {
        changed.push(profile_id);
    });

    agent_status_data.load("p1");
    assert.deepEqual(requested_urls, ["/json/agent/profiles/p1/status"]);
    pending_request.success(make_status({in_progress: [make_item()]}));
    assert.deepEqual(changed, ["p1"]);
    assert.equal(agent_status_data.get_status("p1").in_progress[0].title, "Summarize the sprint");
    assert.equal(agent_status_data.has_load_failed("p1"), false);

    stop();
    agent_status_data.load("p1");
    pending_request.success(make_status());
    assert.deepEqual(changed, ["p1"], "a stopped listener hears nothing");
});

run_test("a failed load is marked, and a good one clears the mark", () => {
    reset();
    const changed = [];
    agent_status_data.on_change((profile_id) => {
        changed.push(profile_id);
    });

    agent_status_data.load("p1");
    pending_request.error();
    assert.equal(agent_status_data.has_load_failed("p1"), true);
    assert.deepEqual(changed.slice(-1), ["p1"]);

    // An answer that does not match the schema is a failure too.
    agent_status_data.load("p1");
    pending_request.success({in_progress: "none"});
    assert.equal(agent_status_data.has_load_failed("p1"), true);
    assert.equal(agent_status_data.get_status("p1"), undefined);

    agent_status_data.load("p1");
    pending_request.success(make_status());
    assert.equal(agent_status_data.has_load_failed("p1"), false);
});

run_test("only the newest load of an agent counts", () => {
    reset();
    agent_status_data.load("p1");
    const older = pending_request;
    agent_status_data.load("p1");
    const newer = pending_request;

    newer.success(make_status({in_progress: [make_item({title: "New"})]}));
    older.success(make_status({in_progress: [make_item({title: "Old"})]}));
    older.error();
    assert.equal(agent_status_data.get_status("p1").in_progress[0].title, "New");
    assert.equal(agent_status_data.has_load_failed("p1"), false);
});

run_test("an agent_job event of the watched agent reloads its status once", ({override}) => {
    reset();
    agent_status_data.initialize();
    const [{type, callback}] = listeners;
    assert.equal(type, "agent_job");

    // Each event cancels the timer of the one before it.
    let timers = [];
    override(global, "setTimeout", (fn) => {
        timers.push(fn);
        return timers.length;
    });
    override(global, "clearTimeout", (timer) => {
        timers = timers.filter((_fn, index) => index + 1 !== timer);
    });

    callback({type: "agent_job", profile_id: "p1"});
    assert.deepEqual(requested_urls, [], "nothing is watched");

    agent_status_data.watch("p1");
    callback({type: "agent_job", profile_id: "p2"});
    callback({type: "agent_job", op: "update"});
    assert.deepEqual(timers, [], "an event of another agent, or of none, is ignored");

    callback({type: "agent_job", profile_id: "p1"});
    callback({type: "agent_job", profile_id: "p1"});
    assert.equal(timers.length, 1);
    timers[0]();
    assert.deepEqual(requested_urls, ["/json/agent/profiles/p1/status"]);
});

run_test("is_event_for_profile compares the profile of the event", () => {
    assert.equal(agent_status_data.is_event_for_profile({profile_id: "p1"}, "p1"), true);
    assert.equal(agent_status_data.is_event_for_profile({profile_id: "p2"}, "p1"), false);
    assert.equal(agent_status_data.is_event_for_profile({}, "p1"), false);
});

run_test("job_link_id gives only a job id that is a UUID", () => {
    assert.equal(agent_status_data.job_link_id(make_item()), JOB_ID);
    assert.equal(agent_status_data.job_link_id(make_item({job_id: null})), undefined);
    assert.equal(
        agent_status_data.job_link_id(make_item({job_id: "1/../../evil"})),
        undefined,
    );
});

run_test("only a 1:1 direct message with an agent has a status", () => {
    const profile_ids = new Map([[11, "p-kaki"]]);
    const lookup = (user_id) => profile_ids.get(user_id);

    assert.equal(agent_status_data.dm_agent_profile_id([11], lookup), "p-kaki");
    // A person, or a regular bot, has no agent profile.
    assert.equal(agent_status_data.dm_agent_profile_id([5], lookup), undefined);
    // A group, no narrow and an empty narrow have none either.
    assert.equal(agent_status_data.dm_agent_profile_id([11, 5], lookup), undefined);
    assert.equal(agent_status_data.dm_agent_profile_id(undefined, lookup), undefined);
    assert.equal(agent_status_data.dm_agent_profile_id([], lookup), undefined);
});
