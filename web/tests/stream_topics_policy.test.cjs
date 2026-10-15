"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const stream_topics_policy = zrequire("stream_topics_policy");

run_test("the no topics option maps to the topics policy setting", () => {
    assert.equal(stream_topics_policy.policy_for_no_topics_option(true), "empty_topic_only");
    // Unchecking it hands the channel back to the organization default.
    assert.equal(stream_topics_policy.policy_for_no_topics_option(false), "inherit");
});

run_test("only the empty topic policy turns the option on", () => {
    assert.equal(stream_topics_policy.is_no_topics_policy("empty_topic_only"), true);
    for (const policy of ["inherit", "allow_empty_topic", "disable_empty_topic", ""]) {
        assert.equal(stream_topics_policy.is_no_topics_policy(policy), false, policy);
    }
});
