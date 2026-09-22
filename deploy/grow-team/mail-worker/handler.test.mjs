import assert from "node:assert/strict";
import test from "node:test";
import { createHandler } from "./handler.mjs";

class EmailMessage {
  constructor(from, to, raw) {
    Object.assign(this, { from, to, raw });
  }
}

const payload = {
  from: "noreply@growc.id",
  to: "owner@example.com",
  raw: "From: Grow Team <noreply@growc.id>\r\nTo: owner@example.com\r\nSubject: Verify\r\n\r\nA verification link.",
};
const handler = createHandler(EmailMessage);
const request = (body = payload, token = "test-token") => new Request("https://mail.example/send", {
  method: "POST",
  headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const environment = (send) => ({ RELAY_TOKEN: "test-token", EMAIL: { send } });

test("rejects missing or incorrect credentials before email delivery", async () => {
  for (const token of ["", "wrong"]) {
    const response = await handler(request(payload, token), environment(() => assert.fail("Email sent")));
    assert.equal(response.status, 401);
  }
  const response = await handler(request(), { EMAIL: { send: () => assert.fail("Email sent") } });
  assert.equal(response.status, 503);
});

test("preserves MIME content and the envelope recipient", async () => {
  let delivered;
  const response = await handler(request(), environment(async (message) => { delivered = message; }));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { success: true });
  assert.deepEqual({ ...delivered }, payload);
});

test("rejects unauthorized senders, invalid recipients, and empty MIME", async () => {
  for (const body of [
    { ...payload, from: "attacker@example.com" },
    { ...payload, to: "victim@example.com\r\nBcc: other@example.com" },
    { ...payload, raw: "" },
  ]) {
    const response = await handler(request(body), environment(() => assert.fail("Email sent")));
    assert.equal(response.status, 400);
  }
});

test("rejects an oversized MIME message", async () => {
  const response = await handler(request({ ...payload, raw: "x".repeat(5 * 1024 * 1024 + 1) }), environment(() => assert.fail("Email sent")));
  assert.equal(response.status, 413);
});

test("reports provider failure without exposing message content", async () => {
  const response = await handler(request(), environment(async () => { throw new Error("private message body"); }));
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { success: false, error: "E_DELIVERY_FAILED" });
});

test("returns the provider error code so the caller can log the cause", async () => {
  const response = await handler(request(), environment(async () => {
    throw Object.assign(new Error("private message body"), { code: "E_DAILY_LIMIT_EXCEEDED" });
  }));
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { success: false, error: "E_DAILY_LIMIT_EXCEEDED" });
});
