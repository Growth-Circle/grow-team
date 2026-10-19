import { DurableObject } from "cloudflare:workers";
import { buildRaw } from "./mime.ts";
import { describe, runCheck, type Alert, type MonitorState } from "./monitor.ts";

const SENDER = "noreply@growc.id";
const STATE_KEY = "state";

interface Env {
  MONITOR: DurableObjectNamespace<UptimeMonitor>;
  MAIL: Fetcher;
  CHECK_URL: string;
  RELAY_TOKEN: string;
  ALERT_RECIPIENTS: string;
}

// A Durable Object holds the incident state: storage reads are strongly
// consistent, so a slow-propagating KV write can never make two consecutive
// checks both see "not alerted yet" and send the same email twice.
export class UptimeMonitor extends DurableObject<Env> {
  async run(): Promise<void> {
    const { CHECK_URL, RELAY_TOKEN, ALERT_RECIPIENTS, MAIL } = this.env;
    if (!RELAY_TOKEN || !ALERT_RECIPIENTS) {
      throw new Error("Set the RELAY_TOKEN and ALERT_RECIPIENTS secrets before the first check.");
    }
    const recipients = ALERT_RECIPIENTS.split(",").map((address) => address.trim()).filter(Boolean);
    await runCheck({
      now: () => Date.now(),
      load: () => this.ctx.storage.get<MonitorState>(STATE_KEY),
      save: (state) => this.ctx.storage.put(STATE_KEY, state),
      probe: async () => {
        try {
          const response = await fetch(CHECK_URL, {
            redirect: "manual",
            signal: AbortSignal.timeout(10_000),
            headers: { "User-Agent": "Grow-Team-Uptime/1.0" },
          });
          await response.body?.cancel();
          return response.status === 200;
        } catch {
          return false;
        }
      },
      send: async (alert: Alert) => {
        const { subject, text } = describe(alert, CHECK_URL);
        const failed: string[] = [];
        for (const to of recipients) {
          const response = await MAIL.fetch("https://mail.internal/send", {
            method: "POST",
            headers: { Authorization: `Bearer ${RELAY_TOKEN}`, "Content-Type": "application/json" },
            body: JSON.stringify({ from: SENDER, to, raw: buildRaw(SENDER, to, subject, text, new Date()) }),
          });
          if (!response.ok) failed.push(`${to}: HTTP ${response.status}`);
        }
        // One delivered recipient is enough; retrying would re-mail the others.
        if (failed.length === recipients.length) {
          throw new Error(`Alert email failed for every recipient (${failed.join("; ")}).`);
        }
        if (failed.length > 0) console.error(JSON.stringify({ event: "partial_alert_failure", failed }));
      },
    });
  }
}

export default {
  async scheduled(_controller, env): Promise<void> {
    await env.MONITOR.get(env.MONITOR.idFromName("team-growc-id")).run();
  },
} satisfies ExportedHandler<Env>;
