import assert from "node:assert/strict";
import test from "node:test";
import { buildRaw } from "../src/mime.ts";
import {
  describe,
  formatDuration,
  formatWib,
  INITIAL_STATE,
  runCheck,
  type Alert,
  type MonitorState,
} from "../src/monitor.ts";

const MINUTE = 60_000;

function harness(options: { failSend?: () => boolean } = {}) {
  let stored: MonitorState | undefined;
  let clock = Date.UTC(2026, 9, 19, 11, 0, 0);
  const alerts: Alert[] = [];
  const check = (up: boolean) =>
    runCheck({
      probe: async () => up,
      send: async (alert) => {
        if (options.failSend?.()) throw new Error("relay down");
        alerts.push(alert);
      },
      load: async () => stored,
      save: async (state) => {
        stored = state;
      },
      now: () => clock,
    });
  const tick = async (up: boolean) => {
    clock += 2 * MINUTE;
    await check(up);
  };
  return { alerts, tick, state: () => stored };
}

test("one failure alone sends nothing", async () => {
  const run = harness();
  await run.tick(false);
  await run.tick(true);
  assert.deepEqual(run.alerts, []);
  assert.deepEqual(run.state(), INITIAL_STATE);
});

test("two failures in a row send one down alert", async () => {
  const run = harness();
  await run.tick(false);
  await run.tick(false);
  assert.equal(run.alerts.length, 1);
  assert.equal(run.alerts[0]?.kind, "down");
});

test("a long outage sends no further alerts", async () => {
  const run = harness();
  for (let i = 0; i < 30; i++) await run.tick(false);
  assert.equal(run.alerts.length, 1);
});

test("recovery sends one email with the downtime and resets", async () => {
  const run = harness();
  await run.tick(false);
  await run.tick(false);
  await run.tick(false);
  await run.tick(true);
  await run.tick(true);
  assert.deepEqual(run.alerts.map((alert) => alert.kind), ["down", "recovered"]);
  const recovered = run.alerts[1] as Extract<Alert, { kind: "recovered" }>;
  assert.equal(recovered.recoveredAt - recovered.downSince, 6 * MINUTE);
  assert.deepEqual(run.state(), INITIAL_STATE);
});

test("a new incident after recovery alerts again", async () => {
  const run = harness();
  for (const up of [false, false, true, false, false]) await run.tick(up);
  assert.deepEqual(run.alerts.map((alert) => alert.kind), ["down", "recovered", "down"]);
});

test("a failed down email is retried on the next check, once delivered it stops", async () => {
  let failing = true;
  const run = harness({ failSend: () => failing });
  await run.tick(false);
  await assert.rejects(run.tick(false), /relay down/);
  assert.equal(run.alerts.length, 0);
  failing = false;
  await run.tick(false);
  await run.tick(false);
  assert.equal(run.alerts.length, 1);
});

test("a failed recovery email is retried and not lost", async () => {
  let failing = false;
  const run = harness({ failSend: () => failing });
  await run.tick(false);
  await run.tick(false);
  failing = true;
  await assert.rejects(run.tick(true), /relay down/);
  failing = false;
  await run.tick(true);
  assert.deepEqual(run.alerts.map((alert) => alert.kind), ["down", "recovered"]);
});

test("messages show WIB times and the downtime length", () => {
  const downSince = Date.UTC(2026, 9, 19, 11, 16, 0);
  const recoveredAt = downSince + 13 * MINUTE;
  assert.equal(formatWib(downSince), "19 Okt 2026, 18:16:00 WIB");
  assert.equal(formatDuration(13 * MINUTE), "13 menit");
  assert.equal(formatDuration(75 * MINUTE), "1 jam 15 menit");
  const { text } = describe({ kind: "recovered", downSince, recoveredAt }, "https://team.growc.id/login/");
  assert.match(text, /Lama gangguan: 13 menit/);
  assert.match(text, /Pulih: 19 Okt 2026, 18:29:00 WIB/);
});

test("raw MIME matches what the email relay accepts", () => {
  const raw = buildRaw("noreply@growc.id", "rama@example.com", "Subject", "Halo é", new Date(0));
  assert.match(raw, /^From: Grow Team Monitor <noreply@growc\.id>\r\n/);
  assert.match(raw, /\r\nMessage-ID: <[^>]+@growc\.id>\r\n/);
  assert.match(raw, /\r\n\r\nSGFsbyDDqQ==$/);
});
