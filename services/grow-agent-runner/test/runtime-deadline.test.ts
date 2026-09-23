import {test} from "node:test";
import assert from "node:assert/strict";
import {attemptDeadline} from "../dist/runtime-supervisor.js";
import {resolveDeadline} from "../dist/broker-exchange.js";

test("attemptDeadline aligns with the claim time and keeps a 5s margin", () => {
    const claimedAt = Date.now();
    const d = {
        lease_expires_at: new Date(claimedAt + 90_000).toISOString(),
        budget: {active_seconds: 600},
    };
    // The server ends the attempt at claim time + active_seconds; attemptDeadline must
    // land 5s before that, derived only from the lease expiry and the budget.
    assert.equal(attemptDeadline(d as never), claimedAt + 600_000 - 5_000);
});

test("attemptDeadline moves with a shorter lease and budget", () => {
    const claimedAt = Date.now() - 3_600_000;
    const d = {
        lease_expires_at: new Date(claimedAt + 90_000).toISOString(),
        budget: {active_seconds: 60},
    };
    assert.equal(attemptDeadline(d as never), claimedAt + 60_000 - 5_000);
});

test("the endpoint child uses deadline_ms from its config when present", () => {
    const deadline = Date.now() + 120_000;
    assert.equal(resolveDeadline({deadline_ms: deadline, budget: {active_seconds: 9999}}), deadline);
});

test("the endpoint child falls back to the local budget computation without deadline_ms", () => {
    const before = Date.now();
    const fallback = resolveDeadline({budget: {active_seconds: 60}});
    assert.ok(fallback >= before + 60_000);
    assert.ok(fallback <= Date.now() + 60_000);
});
