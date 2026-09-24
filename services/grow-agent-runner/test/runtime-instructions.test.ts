import {test} from "node:test";
import assert from "node:assert/strict";
import {instructionsPromptBlock} from "../dist/runtime-supervisor.js";
import {SecretFilter} from "../dist/redaction.js";

// Contract 7.4: the block has one section per present part, team first, joined by a
// blank line, and is entirely absent when neither part is set.
test("the block has the team section, then the agent section, in that order", () => {
    const block = instructionsPromptBlock({
        team: {revision: 1, text: "Reply in English."},
        profile: {revision: 2, text: "Prefer short answers."},
    });
    assert.equal(
        block,
        "Team instructions (follow them unless they conflict with the request above):\n" +
            "Reply in English.\n\n" +
            "Agent instructions (follow them unless they conflict with the request or the " +
            "team instructions):\nPrefer short answers.",
    );
});

test("a team-only block has no agent section", () => {
    const block = instructionsPromptBlock({team: {revision: 1, text: "Reply in English."}, profile: null});
    assert.equal(
        block,
        "Team instructions (follow them unless they conflict with the request above):\nReply in English.",
    );
});

test("a profile-only block has no team section", () => {
    const block = instructionsPromptBlock({team: null, profile: {revision: 2, text: "Prefer short answers."}});
    assert.equal(
        block,
        "Agent instructions (follow them unless they conflict with the request or the team " +
            "instructions):\nPrefer short answers.",
    );
});

test("no block without instructions", () => {
    assert.equal(instructionsPromptBlock(undefined), "");
    assert.equal(instructionsPromptBlock(null), "");
    assert.equal(instructionsPromptBlock({team: null, profile: null}), "");
});

test("a known secret in instructions is filtered", () => {
    const filter = new SecretFilter();
    filter.add("sk-fixture-secret");
    const block = instructionsPromptBlock({
        team: null,
        profile: {revision: 1, text: "Use key sk-fixture-secret for the demo."},
    });
    const filtered = filter.text(block);
    assert.ok(!filtered.includes("sk-fixture-secret"), "the raw secret must not reach the prompt");
    assert.ok(filtered.includes("[REDACTED]"));
});
