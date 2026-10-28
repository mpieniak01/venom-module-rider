import type { Direction } from "./model";

export type HoldTiming = { click_threshold_ms: number; hold_renewal_ms: number; hold_timeout_ms: number; request_ttl_ms: number; supported: string[] };
export type MovementIntent = { direction: Direction; mode: "pulse" | "hold" | "release"; turn_mode?: "in_place" };
type Press = { direction: Direction; pointerId: number; started: number; held: boolean; token: number; latched?: boolean; turn_mode?: "in_place" };
export function readHoldTiming(value: unknown): HoldTiming | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const keys = ["click_threshold_ms", "hold_renewal_ms", "hold_timeout_ms", "request_ttl_ms"] as const;
  if (keys.some(key => typeof row[key] !== "number" || !Number.isFinite(row[key]) || (row[key] as number) <= 0)) return null;
  const supported = row.supported;
  if (!Array.isArray(supported) || !["pulse", "hold", "release"].every(mode => supported.includes(mode))) return null;
  if ((row.request_ttl_ms as number) > 500 || (row.hold_renewal_ms as number) >= (row.hold_timeout_ms as number)) return null;
  return row as HoldTiming;
}
/** A single operator intent. In-flight renewals are serial and are never replayed. */
export class HoldController {
  private press: Press | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private token = 0;
  private inFlight: Promise<void> = Promise.resolve();
  private preparation: {token:number;started:number;ready:boolean;submitted:boolean;failed:boolean;promise:Promise<void>} | null = null;
  private sending = false;
  private sendingMode: MovementIntent["mode"] | null = null;
  constructor(private readonly options: {
    timing: () => HoldTiming | null;
    canMove: () => boolean;
    prepare?: (current: () => boolean) => Promise<void> | void;
    send: (intent: MovementIntent, current: () => boolean) => Promise<void>;
    onActive: (direction: Direction | null) => void;
    onTurnMode?: (mode: "in_place" | null) => void;
    onMode?: (mode: "hold" | "continuous" | null) => void;
    onError: (error: unknown) => void;
    cancelPending?: () => boolean | void;
    now?: () => number;
  }) {}
  /** Start one pointer gesture without replaying a previous direction. */
  start(direction: Direction, pointerId: number, turnMode?: "in_place") {
    const timing = this.options.timing();
    if (!timing || !this.options.canMove()) return;
    this.options.cancelPending?.();
    this.clearTimer();
    const token = ++this.token;
    this.press = { direction, pointerId, started: this.now(), held: false, token, ...(turnMode ? {turn_mode:turnMode} : {}) };
    this.options.onActive(direction);
    this.options.onTurnMode?.(turnMode ?? null);
    this.options.onMode?.("hold");
    this.prepare(token);
    this.timer = setTimeout(() => this.beginHold(token), timing.click_threshold_ms);
  }
  /** Toggle a continuous gesture, treating in-place and legacy turns separately. */
  toggle(direction: Direction, turnMode?: "in_place") {
    if (this.press?.latched && this.press.direction === direction && this.press.turn_mode === turnMode) { this.release(); return; }
    if (!this.options.timing() || !this.options.canMove()) return;
    this.options.cancelPending?.();
    this.clearTimer();
    const token = ++this.token;
    this.press = { direction, pointerId: -3, started: this.now(), held: true, token, latched: true, ...(turnMode ? {turn_mode:turnMode} : {}) };
    this.options.onActive(direction);
    this.options.onTurnMode?.(turnMode ?? null);
    this.options.onMode?.("continuous");
    this.prepare(token);
    void this.renew(token);
  }
  releasePointer() { if (!this.press?.latched) this.release(); }
  /** Finish a click or send release immediately for an established hold. */
  end(pointerId: number) {
    const press = this.press;
    if (!press || press.pointerId !== pointerId) return;
    this.press = null;
    this.clearTimer();
    this.options.onTurnMode?.(null);
    this.options.onActive(null);
    this.options.onMode?.(null);
    const token = ++this.token;
    if (this.preparation?.token === press.token) this.preparation.token = token;
    if (press.held) { if (!this.options.cancelPending?.()) this.enqueueRelease(press, token); }
    else {
      void this.dispatch(this.intent(press, "pulse"), () => token === this.token && !this.press);
    }
  }
  /** Release only the matching gesture and cancel a pending atomic takeover. */
  release(pointerId?: number) {
    if (pointerId !== undefined && this.press?.pointerId !== pointerId) return;
    const press = this.press;
    if (!press) { if (pointerId === undefined && this.sendingMode === "pulse") { ++this.token; this.options.cancelPending?.(); } return; }
    this.press = null;
    this.clearTimer();
    this.options.onTurnMode?.(null);
    this.options.onActive(null);
    this.options.onMode?.(null);
    const token = ++this.token;
    const cancelled = this.options.cancelPending?.();
    if (this.preparation?.token === press.token) this.preparation.token = token;
    if (press?.held && !cancelled) this.enqueueRelease(press, token);
  }
  /** Invalidate local gestures and timers at the STOP/session boundary. */
  cancel() {
    this.options.cancelPending?.();
    this.press = null; ++this.token; this.preparation = null; this.clearTimer();
    this.options.onTurnMode?.(null);
    this.options.onActive(null); this.options.onMode?.(null);
  }
  private prepare(token: number) {
    const preparation = {token,started:this.now(),ready:false,submitted:false,failed:false,promise:Promise.resolve()};
    this.preparation = preparation;
    const current = () => this.preparation === preparation && this.token === preparation.token;
    try {
      const result = this.options.prepare?.(current);
      if (!result) { preparation.ready = true; return; }
      preparation.promise = result.catch(error => {
        preparation.failed = true;
        if (current()) { this.cancel(); this.options.onError(error); }
      });
    } catch (error) {
      preparation.failed = true;
      if (current()) { this.cancel(); this.options.onError(error); }
    }
  }
  private now() { return this.options.now?.() ?? Date.now(); }
  private clearTimer() { if (this.timer) clearTimeout(this.timer); this.timer = null; }
  private beginHold(token: number) {
    const press = this.press;
    if (!press || press.token !== token || !this.options.canMove()) { this.release(); return; }
    press.held = true;
    void this.renew(token);
  }
  /** Renew a live gesture once, stopping at its original timeout. */
  private async renew(token: number) {
    const press = this.press;
    const timing = this.options.timing();
    if (!press || press.token !== token || !timing || !this.options.canMove()) { this.release(); return; }
    // The producer watchdog applies if a request stalls; do not enqueue renewals.
    const started = this.now();
    await this.dispatch(this.intent(press, "hold"), () => this.press?.token === token && this.options.canMove());
    if (this.press?.token !== token) return;
    const elapsed = this.now() - started;
    if (elapsed >= timing.hold_timeout_ms) {
      this.release();
      this.options.onError(Object.assign(new Error("hold_timed_out"), { data: { error: "hold_timed_out" } }));
      return;
    }
    this.timer = setTimeout(() => { void this.renew(token); }, Math.max(0, timing.hold_renewal_ms - elapsed));
  }
  /** Carry the selected maneuver into every pulse, renewal and release. */
  private intent(press: Press, mode: MovementIntent["mode"]): MovementIntent {
    return {direction:press.direction,mode,...(press.turn_mode ? {turn_mode:press.turn_mode} : {})};
  }
  /** Dispatch zero intent independently of a stalled motion acknowledgement. */
  private enqueueRelease(press: Press, token: number) {
    void this.dispatch(this.intent(press, "release"), () => this.token === token && !this.press);
  }
  /** Serialize motion renewals while allowing release to bypass their ACK. */
  private async dispatch(intent: MovementIntent, valid: () => boolean) {
    const preparation = this.preparation;
    if (preparation && !preparation.ready) {
      await preparation.promise;
      if (!valid() || preparation.failed || preparation !== this.preparation) return;
      if (this.now() - preparation.started >= (this.options.timing()?.request_ttl_ms ?? 0)) { this.cancel(); return; }
      preparation.ready = true;
    }
    // Releasing must reach Pi even while an earlier hold response is stalled.
    // The newer sequence retires late renewals; never wait for their HTTP ACK.
    if (intent.mode === "release") {
      if (!valid()) return;
      try { await this.options.send(intent, valid); }
      catch (error) { this.options.onError(error); }
      return;
    }
    // Replacement waits only for the request already being sent, never for a queue.
    if (this.sending) await this.inFlight;
    if (!valid()) return;
    // Fresh envelope timestamps cannot make a queued, expired gesture current.
    if (preparation && !preparation.submitted) {
      if (preparation !== this.preparation || this.now() - preparation.started >= (this.options.timing()?.request_ttl_ms ?? 0)) { this.cancel(); return; }
      preparation.submitted = true;
    }
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
