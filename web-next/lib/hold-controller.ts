import type { Direction } from "./model";

export type HoldTiming = { click_threshold_ms: number; hold_renewal_ms: number; hold_timeout_ms: number; request_ttl_ms: number; supported: string[] };
export type MovementIntent = { direction: Direction; mode: "pulse" | "hold" | "release" };
type Press = { direction: Direction; pointerId: number; started: number; held: boolean; token: number };
export function readHoldTiming(value: unknown): HoldTiming | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const keys = ["click_threshold_ms", "hold_renewal_ms", "hold_timeout_ms", "request_ttl_ms"] as const;
  if (keys.some(key => typeof row[key] !== "number" || !Number.isFinite(row[key]) || (row[key] as number) <= 0)) return null;
  if (!Array.isArray(row.supported) || !["pulse", "hold", "release"].every(mode => row.supported instanceof Array && row.supported.includes(mode))) return null;
  if ((row.request_ttl_ms as number) > 500 || (row.hold_renewal_ms as number) >= (row.hold_timeout_ms as number)) return null;
  return row as HoldTiming;
}
/** A single operator intent. In-flight renewals are serial and are never replayed. */
export class HoldController {
  private press: Press | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private token = 0;
  private inFlight: Promise<void> = Promise.resolve();
  private sending = false;
  private sendingMode: MovementIntent["mode"] | null = null;
  constructor(private readonly options: {
    timing: () => HoldTiming | null;
    canMove: () => boolean;
    send: (intent: MovementIntent, current: () => boolean) => Promise<void>;
    onActive: (direction: Direction | null) => void;
    onError: (error: unknown) => void;
    now?: () => number;
  }) {}
  start(direction: Direction, pointerId: number) {
    const timing = this.options.timing();
    if (!timing || !this.options.canMove()) return;
    this.clearTimer();
    const token = ++this.token;
    this.press = { direction, pointerId, started: this.now(), held: false, token };
    this.options.onActive(direction);
    this.timer = setTimeout(() => this.beginHold(token), timing.click_threshold_ms);
  }
  end(pointerId: number) {
    const press = this.press;
    if (!press || press.pointerId !== pointerId) return;
    this.press = null;
    this.clearTimer();
    this.options.onActive(null);
    if (press.held) this.enqueueRelease(press.direction, ++this.token);
    else {
      const token = ++this.token;
      void this.dispatch({ direction: press.direction, mode: "pulse" }, () => token === this.token && !this.press);
    }
  }
  release(pointerId?: number) {
    if (pointerId !== undefined && this.press?.pointerId !== pointerId) return;
    const press = this.press;
    if (!press) { if (this.sendingMode === "pulse") ++this.token; return; }
    this.press = null;
    this.clearTimer();
    this.options.onActive(null);
    const token = ++this.token;
    if (press?.held) this.enqueueRelease(press.direction, token);
  }
  cancel() { this.press = null; ++this.token; this.clearTimer(); this.options.onActive(null); }
  private now() { return this.options.now?.() ?? Date.now(); }
  private clearTimer() { if (this.timer) clearTimeout(this.timer); this.timer = null; }
  private beginHold(token: number) {
    const press = this.press;
    if (!press || press.token !== token || !this.options.canMove()) { this.release(); return; }
    press.held = true;
    void this.renew(token);
  }
  private async renew(token: number) {
    const press = this.press;
    const timing = this.options.timing();
    if (!press || press.token !== token || !timing || !this.options.canMove()) { this.release(); return; }
    // The producer watchdog applies if a request stalls; do not enqueue renewals.
    const started = this.now();
    await this.dispatch({ direction: press.direction, mode: "hold" }, () => this.press?.token === token && this.options.canMove());
    if (this.press?.token !== token) return;
    const elapsed = this.now() - started;
    if (elapsed >= timing.hold_timeout_ms) {
      this.release();
      this.options.onError(Object.assign(new Error("hold_timed_out"), { data: { error: "hold_timed_out" } }));
      return;
    }
    this.timer = setTimeout(() => { void this.renew(token); }, Math.max(0, timing.hold_renewal_ms - elapsed));
  }
  private enqueueRelease(direction: Direction, token: number) {
    void this.dispatch({ direction, mode: "release" }, () => this.token === token && !this.press);
  }
  private async dispatch(intent: MovementIntent, valid: () => boolean) {
    // Replacement waits only for the request already being sent, never for a queue.
    if (this.sending) await this.inFlight;
    if (!valid()) return;
    this.sending = true;
    this.sendingMode = intent.mode;
    const sentToken = this.token;
    this.inFlight = this.options.send(intent, valid).catch(error => {
      if (this.token === sentToken) this.cancel();
      this.options.onError(error);
    }).finally(() => { this.sending = false; this.sendingMode = null; });
    await this.inFlight;
  }
}
