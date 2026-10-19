export const FAILURES_BEFORE_ALERT = 2;

export interface MonitorState {
  failures: number;
  downSince: number | null;
  alerted: boolean;
}

export const INITIAL_STATE: MonitorState = { failures: 0, downSince: null, alerted: false };

export type Alert =
  | { kind: "down"; downSince: number }
  | { kind: "recovered"; downSince: number; recoveredAt: number };

interface Decision {
  alert: Alert | null;
  /** State to keep once the alert (if any) was delivered. */
  next: MonitorState;
  /** State to keep when delivery fails, so the next check retries the alert. */
  retry: MonitorState;
}

export function decide(state: MonitorState, up: boolean, now: number): Decision {
  if (up) {
    if (state.alerted && state.downSince !== null) {
      return {
        alert: { kind: "recovered", downSince: state.downSince, recoveredAt: now },
        next: INITIAL_STATE,
        retry: state,
      };
    }
    return { alert: null, next: INITIAL_STATE, retry: INITIAL_STATE };
  }
  const failing: MonitorState = {
    failures: state.failures + 1,
    downSince: state.downSince ?? now,
    alerted: state.alerted,
  };
  if (failing.failures >= FAILURES_BEFORE_ALERT && !failing.alerted) {
    return {
      alert: { kind: "down", downSince: failing.downSince as number },
      next: { ...failing, alerted: true },
      retry: failing,
    };
  }
  return { alert: null, next: failing, retry: failing };
}

export interface CheckDeps {
  probe(): Promise<boolean>;
  send(alert: Alert): Promise<void>;
  load(): Promise<MonitorState | undefined>;
  save(state: MonitorState): Promise<void>;
  now(): number;
}

export async function runCheck(deps: CheckDeps): Promise<void> {
  const state = (await deps.load()) ?? INITIAL_STATE;
  const decision = decide(state, await deps.probe(), deps.now());
  if (decision.alert === null) {
    await deps.save(decision.next);
    return;
  }
  try {
    await deps.send(decision.alert);
  } catch (error) {
    await deps.save(decision.retry);
    throw error;
  }
  await deps.save(decision.next);
}

const wib = new Intl.DateTimeFormat("id-ID", {
  timeZone: "Asia/Jakarta",
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export function formatWib(ms: number): string {
  return `${wib.format(ms).replaceAll(".", ":")} WIB`;
}

export function formatDuration(ms: number): string {
  const minutes = Math.max(1, Math.round(ms / 60_000));
  if (minutes < 60) return `${minutes} menit`;
  const hours = Math.floor(minutes / 60);
  return `${hours} jam ${minutes % 60} menit`;
}

export function describe(alert: Alert, url: string): { subject: string; text: string } {
  if (alert.kind === "down") {
    return {
      subject: "[Grow Team] DOWN - team.growc.id tidak merespons",
      text: [
        `${url} gagal dicek 2 kali berturut-turut.`,
        `Mulai gagal: ${formatWib(alert.downSince)}`,
        "Email berikutnya hanya dikirim saat situs pulih.",
      ].join("\n"),
    };
  }
  return {
    subject: "[Grow Team] PULIH - team.growc.id kembali normal",
    text: [
      `${url} kembali merespons 200.`,
      `Mulai gagal: ${formatWib(alert.downSince)}`,
      `Pulih: ${formatWib(alert.recoveredAt)}`,
      `Lama gangguan: ${formatDuration(alert.recoveredAt - alert.downSince)}`,
    ].join("\n"),
  };
}
