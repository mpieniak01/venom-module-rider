import assert from "node:assert/strict";
import fs from "node:fs";
import { test, type TestContext } from "node:test";
import { HoldController, readHoldTiming, type HoldTiming, type MovementIntent } from "../../web-next/lib/hold-controller";
import type { Direction } from "../../web-next/lib/model";
const contracts = JSON.parse(fs.readFileSync(new URL("../../venom_module_rider/fixtures/pi-contracts.json", import.meta.url), "utf8"));
const timing = contracts.settings.control_modes as HoldTiming;
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function setup(t: TestContext, send?: (intent: MovementIntent) => Promise<void>) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const intents: MovementIntent[] = [], active: (Direction | null)[] = [], errors: unknown[] = [];
  const controller = new HoldController({ timing: () => timing, canMove: () => true, send: async intent => { intents.push(intent); await send?.(intent); }, onActive: direction => active.push(direction), onError: error => errors.push(error) });
  return { controller, intents, active, errors };
}
test("only descriptor timing with supported modes and bounded TTL is accepted", () => {
  assert.ok(readHoldTiming(timing)); assert.equal(readHoldTiming(null), null);
  assert.equal(readHoldTiming({ ...timing, request_ttl_ms: 501 }), null);
  assert.equal(readHoldTiming({ ...timing, hold_renewal_ms: timing.hold_timeout_ms }), null);
  assert.equal(readHoldTiming({ ...timing, supported: ["pulse"] }), null);
});
test("a short click submits exactly one pulse and no later hold", async t => {
  const { controller, intents } = setup(t);
  controller.start("forward", 1); t.mock.timers.tick(timing.click_threshold_ms - 1); controller.end(1); await flush();
  t.mock.timers.tick(timing.hold_timeout_ms * 2); await flush();
  assert.deepEqual(intents, [{ direction: "forward", mode: "pulse" }]);
});
test("a held direction renews independently and release sends braking without disarming", async t => {
  const { controller, intents } = setup(t);
  controller.start("left", 1); t.mock.timers.tick(timing.click_threshold_ms); await flush();
  t.mock.timers.tick(timing.hold_renewal_ms); await flush(); controller.end(1); await flush();
  t.mock.timers.tick(timing.hold_timeout_ms * 2); await flush();
  assert.deepEqual(intents.map(intent => intent.mode), ["hold", "hold", "release"]);
});
test("new direction replaces a pending hold with no overlapping request or queued renewal", async t => {
  let finish: (() => void) | undefined; let inFlight = 0, maximum = 0;
  const { controller, intents } = setup(t, async intent => { inFlight++; maximum = Math.max(maximum, inFlight); if (intent.direction === "forward") await new Promise<void>(resolve => { finish = resolve; }); inFlight--; });
  controller.start("forward", 1); t.mock.timers.tick(timing.click_threshold_ms); await flush();
  controller.start("right", 2); t.mock.timers.tick(timing.click_threshold_ms); await flush();
  controller.end(1); controller.release(1); assert.equal(intents.length, 1);
  finish?.(); await flush(); assert.equal(maximum, 1); assert.equal(intents.at(-1)?.direction, "right");
  controller.release(2); await flush(); assert.equal(intents.at(-1)?.mode, "release");
});
test("losing focus before hold threshold cancels the click without creating a pulse", async t => {
  const { controller, intents } = setup(t); controller.start("forward", 1); controller.release();
  t.mock.timers.tick(timing.click_threshold_ms + timing.hold_timeout_ms); await flush(); assert.equal(intents.length, 0);
});
test("a failed command clears the active hold and timers; a new operator click can recover", async t => {
  let fail = true;
  const { controller, intents, active, errors } = setup(t, async () => { if (fail) throw new Error("offline"); });
  controller.start("backward", 1); t.mock.timers.tick(timing.click_threshold_ms); await flush();
  t.mock.timers.tick(timing.hold_timeout_ms * 3); await flush(); assert.equal(intents.length, 1); assert.equal(errors.length, 1); assert.equal(active.at(-1), null);
  fail = false; controller.start("forward", 2); controller.end(2); await flush(); assert.equal(intents.at(-1)?.mode, "pulse");
});
test("STOP cancellation discards local renewal without replaying release or movement", async t => {
  const { controller, intents } = setup(t); controller.start("forward", 1); t.mock.timers.tick(timing.click_threshold_ms); await flush(); controller.cancel();
  t.mock.timers.tick(timing.hold_timeout_ms * 3); await flush(); controller.end(1); await flush(); assert.equal(intents.length, 1);
});
test("a hold response after the Pi watchdog timeout ends the intent instead of resuming", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let now = 0, finish: (() => void) | undefined;
  const intents: MovementIntent[] = [], errors: unknown[] = [];
  const controller = new HoldController({ timing: () => timing, canMove: () => true, now: () => now,
    send: async intent => { intents.push(intent); if (intent.mode === "hold") await new Promise<void>(resolve => { finish = resolve; }); },
    onActive: () => undefined, onError: error => errors.push(error) });
  controller.start("forward", 1); t.mock.timers.tick(timing.click_threshold_ms); await flush();
  now = timing.hold_timeout_ms; finish?.(); await flush();
  t.mock.timers.tick(timing.hold_timeout_ms * 2); await flush();
  assert.deepEqual(intents.map(intent => intent.mode), ["hold", "release"]); assert.equal(errors.length, 1);
});

test("duplicate blur and visibility releases preserve a release waiting for the active request", async t => {
  let finish: (() => void) | undefined;
  const { controller, intents } = setup(t, async intent => { if (intent.mode === "hold") await new Promise<void>(resolve => { finish = resolve; }); });
  controller.start("forward", 1); t.mock.timers.tick(timing.click_threshold_ms); await flush();
  controller.release(); controller.release(); finish?.(); await flush();
  assert.deepEqual(intents.map(intent => intent.mode), ["hold", "release"]);
});

test("a late release failure does not cancel a newer operator press", async t => {
  let rejectRelease: ((error: Error) => void) | undefined;
  const { controller, intents, errors } = setup(t, async intent => {
    if (intent.mode === "release") await new Promise<void>((_, reject) => { rejectRelease = reject; });
  });
  controller.start("forward", 1); t.mock.timers.tick(timing.click_threshold_ms); await flush();
  controller.end(1); await flush();
  controller.start("right", 2); t.mock.timers.tick(timing.click_threshold_ms); await flush();
  rejectRelease?.(new Error("old release failed")); await flush();
  assert.equal(errors.length, 1);
  assert.equal(intents.at(-1)?.direction, "right");
  assert.equal(intents.at(-1)?.mode, "hold");
  controller.cancel();
});


test("a direction replaced while refreshing state is discarded before sending; newest intent wins", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const sent: MovementIntent[] = []; let refreshed: (() => void) | undefined;
  const controller = new HoldController({ timing: () => timing, canMove: () => true, onActive: () => undefined, onError: () => undefined,
    send: async (intent, current) => {
      if (intent.direction === "forward") await new Promise<void>(resolve => { refreshed = resolve; });
      if (current()) sent.push(intent);
    } });
  controller.start("forward", 1); controller.end(1); await flush();
  controller.start("right", 2); controller.end(2); await flush(); refreshed?.(); await flush();
  assert.deepEqual(sent, [{ direction: "right", mode: "pulse" }]);
});
test("STOP during a state refresh discards the unsent intent and never replays it", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const sent: MovementIntent[] = []; let refreshed: (() => void) | undefined;
  const controller = new HoldController({ timing: () => timing, canMove: () => true, onActive: () => undefined, onError: () => undefined,
    send: async (intent, current) => { await new Promise<void>(resolve => { refreshed = resolve; }); if (current()) sent.push(intent); } });
  controller.start("forward", 1); controller.end(1); await flush(); controller.cancel(); refreshed?.(); await flush();
  t.mock.timers.tick(timing.hold_timeout_ms * 2); await flush(); assert.deepEqual(sent, []);
});

test("blur while a pulse awaits state refresh discards it without cancelling queued braking", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const sent: MovementIntent[] = []; let refreshed: (() => void) | undefined;
  const controller = new HoldController({ timing: () => timing, canMove: () => true, onActive: () => undefined, onError: () => undefined,
    send: async (intent, current) => { await new Promise<void>(resolve => { refreshed = resolve; }); if (current()) sent.push(intent); } });
  controller.start("forward", 1); controller.end(1); await flush(); controller.release(); refreshed?.(); await flush();
  assert.deepEqual(sent, []);
});
