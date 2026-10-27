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


test("continuous selection renews for 30 seconds without pointer holding, then toggles off", async t => {
  const { controller, intents, active } = setup(t);
  controller.toggle("forward"); await flush();
  controller.end(15); controller.releasePointer();
  for (let elapsed = 0; elapsed < 30000; elapsed += timing.hold_renewal_ms) {
    t.mock.timers.tick(timing.hold_renewal_ms); await flush();
  }
  assert.equal(active.at(-1), "forward");
  assert.ok(intents.length >= 200);
  assert.ok(intents.every(intent => intent.mode === "hold" && intent.direction === "forward"));
  controller.toggle("forward"); await flush();
  assert.equal(intents.at(-1)?.mode, "release");
  const count = intents.length;
  t.mock.timers.tick(30000); await flush();
  assert.equal(intents.length, count); assert.equal(active.at(-1), null);
});

test("pulse replaces continuous selection permanently and unrelated async work does not cancel it", async t => {
  const { controller, intents } = setup(t);
  controller.toggle("forward"); await flush();
  await Promise.resolve("sample saved");
  t.mock.timers.tick(timing.hold_renewal_ms); await flush();
  assert.equal(intents.at(-1)?.mode, "hold");
  controller.start("right", 1); controller.end(1); await flush();
  assert.deepEqual(intents.at(-1), { direction: "right", mode: "pulse" });
  const count = intents.length;
  t.mock.timers.tick(30000); await flush();
  assert.equal(intents.length, count);
});

test("STOP during pending continuous request cannot replay it or its replaced direction", async t => {
  let finish: (() => void) | undefined;
  const { controller, intents } = setup(t, async intent => {
    if (intent.mode === "hold") await new Promise<void>(resolve => { finish = resolve; });
  });
  controller.toggle("left"); await flush(); controller.toggle("right");
  controller.cancel(); finish?.(); await flush();
  t.mock.timers.tick(30000); await flush();
  assert.deepEqual(intents, [{ direction: "left", mode: "hold" }]);
});

test("pointer blur keeps continuous selection; global release stops it", async t => {
  const { controller, intents } = setup(t);
  controller.toggle("backward"); await flush(); controller.releasePointer();
  t.mock.timers.tick(timing.hold_renewal_ms); await flush();
  assert.equal(intents.at(-1)?.mode, "hold");
  controller.release(); await flush();
  assert.equal(intents.at(-1)?.mode, "release");
});

test("operator pause starts on pointerdown; a released short click waits for its own ACK", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  let finish!:()=>void, current!:()=>boolean, preparations=0;
  const intents:MovementIntent[]=[];
  const controller=new HoldController({timing:()=>timing,canMove:()=>true,prepare:valid=>{preparations++;current=valid;return new Promise<void>(resolve=>{finish=resolve;});},send:async intent=>{intents.push(intent);},onActive:()=>undefined,onError:()=>undefined});
  controller.start("left",1);
  assert.equal(preparations,1);assert.equal(intents.length,0);
  controller.end(1);await flush();assert.equal(current(),true);assert.equal(intents.length,0);
  finish();await flush();
  assert.deepEqual(intents,[{direction:"left",mode:"pulse"}]);
  controller.cancel();
});
test("a late pause ACK after TTL or STOP never replays a click", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  for(const cancelled of [false,true]) {
    let now=0,finish!:()=>void;
    const intents:MovementIntent[]=[];
    const controller=new HoldController({timing:()=>timing,canMove:()=>true,now:()=>now,prepare:()=>new Promise<void>(resolve=>{finish=resolve;}),send:async intent=>{intents.push(intent);},onActive:()=>undefined,onError:()=>undefined});
    controller.start("forward",1);controller.end(1);await flush();
    if(cancelled)controller.cancel();else now=timing.request_ttl_ms+1;
    finish();await flush();assert.equal(intents.length,0);
  }
});
test("hold renews only after confirmed pause while the same gesture remains held", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  let finish!:()=>void;
  const intents:MovementIntent[]=[];
  const controller=new HoldController({timing:()=>timing,canMove:()=>true,prepare:()=>new Promise<void>(resolve=>{finish=resolve;}),send:async intent=>{intents.push(intent);},onActive:()=>undefined,onError:()=>undefined});
  controller.start("forward",1);t.mock.timers.tick(timing.click_threshold_ms);await flush();assert.equal(intents.length,0);
  finish();await flush();assert.deepEqual(intents.map(intent=>intent.mode),["hold"]);
  t.mock.timers.tick(timing.hold_renewal_ms);await flush();controller.end(1);await flush();
  assert.deepEqual(intents.map(intent=>intent.mode),["hold","hold","release"]);
});

test("an expired replacement click waiting for an earlier request is never replayed", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  let now=0,finish!:()=>void;
  const intents:MovementIntent[]=[];
  const controller=new HoldController({timing:()=>timing,canMove:()=>true,now:()=>now,prepare:async()=>undefined,send:async intent=>{intents.push(intent);if(intent.direction==="forward")await new Promise<void>(resolve=>{finish=resolve;});},onActive:()=>undefined,onError:()=>undefined});
  controller.start("forward",1);controller.end(1);await flush();
  controller.start("left",2);controller.end(2);await flush();
  now=timing.request_ttl_ms+1;finish();await flush();
  assert.deepEqual(intents,[{direction:"forward",mode:"pulse"}]);
});

test("release and replacement cancel a pending atomic handoff before its acknowledgement",async t=>{
  t.mock.timers.enable({apis:["setTimeout"]});
  let finish!:()=>void,cancelled=0;
  const sent:MovementIntent[]=[];
  const controller=new HoldController({timing:()=>timing,canMove:()=>true,cancelPending:()=>{cancelled++;},send:async(intent)=>{sent.push(intent);if(intent.mode==="hold")await new Promise<void>(resolve=>{finish=resolve;});},onActive:()=>undefined,onError:()=>undefined});
  controller.start("forward",1);t.mock.timers.tick(timing.click_threshold_ms);await flush();
  const before=cancelled;controller.end(1);
  assert.equal(cancelled,before+1,"release must cancel without waiting for in-flight ACK");
  await flush();assert.deepEqual(sent.map(intent=>intent.mode),["hold","release"]);
  finish();await flush();assert.deepEqual(sent.map(intent=>intent.mode),["hold","release"]);
  controller.cancel();
});
test("global cancellation invalidates an in-flight pulse while pointer capture loss after click does not",async t=>{
  t.mock.timers.enable({apis:["setTimeout"]});
  let finish!:()=>void,cancelled=0;
  const controller=new HoldController({timing:()=>timing,canMove:()=>true,cancelPending:()=>{cancelled++;},send:async()=>new Promise<void>(resolve=>{finish=resolve;}),onActive:()=>undefined,onError:()=>undefined});
  controller.start("left",1);controller.end(1);await flush();
  const before=cancelled;controller.release(1);assert.equal(cancelled,before);
  controller.release();assert.equal(cancelled,before+1);finish();await flush();
  controller.cancel();
});

test("an acknowledged scoped cancellation suppresses an unscoped release against the old owner",async t=>{
  t.mock.timers.enable({apis:["setTimeout"]});
  let pending=false,finish!:()=>void;
  const intents:MovementIntent[]=[];
  const controller=new HoldController({timing:()=>timing,canMove:()=>true,cancelPending:()=>{const cancelled=pending;pending=false;return cancelled;},send:async intent=>{intents.push(intent);pending=true;await new Promise<void>(resolve=>{finish=resolve;});},onActive:()=>undefined,onError:()=>undefined});
  controller.start("forward",1);t.mock.timers.tick(timing.click_threshold_ms);await flush();controller.end(1);
  finish();await flush();assert.deepEqual(intents.map(intent=>intent.mode),["hold"]);controller.cancel();
});

test("pointer release bypasses a stalled hold ACK and cannot replay its renewal", async t => {
  let finish: (() => void) | undefined;
  const { controller, intents } = setup(t, async intent => {
    if (intent.mode === "hold") await new Promise<void>(resolve => { finish = resolve; });
  });
  controller.start("forward", 1);
  t.mock.timers.tick(timing.click_threshold_ms); await flush();
  controller.end(1); await flush();
  assert.deepEqual(intents.map(intent => intent.mode), ["hold", "release"]);
  finish?.(); await flush();
  t.mock.timers.tick(timing.hold_timeout_ms * 2); await flush();
  assert.deepEqual(intents.map(intent => intent.mode), ["hold", "release"]);
});

test("in-place arrows retain their distinct maneuver through hold and release", async t => {
  const {controller,intents}=setup(t);
  controller.start("left",1,"in_place");
  t.mock.timers.tick(timing.click_threshold_ms);await flush();
  controller.end(1);await flush();
  assert.deepEqual(intents,[{direction:"left",mode:"hold",turn_mode:"in_place"},{direction:"left",mode:"release",turn_mode:"in_place"}]);
});
test("normal and in-place continuous turns are different intents", async t => {
  const {controller,intents}=setup(t);
  controller.toggle("left","in_place");await flush();
  controller.toggle("left");await flush();
  assert.deepEqual(intents.map(({turn_mode})=>turn_mode),["in_place",undefined]);
  controller.cancel();
});
