import assert from "node:assert/strict";
import { test } from "node:test";
import { stopLoopResources } from "../../web-next/lib/stop-loop";

test("explicit loop STOP retires both producers before releasing the trial S2 camera reservation", async () => {
  const calls: unknown[] = [];
  await stopLoopResources({ drive: true, observe: true }, async (...args) => { calls.push(args); });
  assert.deepEqual(calls, [
    ["/goal/s14_venom_navigation", { enabled: false }, "POST", true],
    ["/goal/s15_venom_perception", { enabled: false }, "POST", true],
    ["/resource/camera", { action: "stop" }, "POST", true],
  ]);
});
test("STOP still releases S2 after normal completion or a failed producer; repeated STOP is idempotent", async () => {
  const calls: string[] = [];
  for (let i = 0; i < 2; i++) await stopLoopResources({ drive: false, observe: false }, async path => { calls.push(path); });
  assert.deepEqual(calls, ["/resource/camera", "/resource/camera"]);
});
test("failed producer STOP does not switch off the camera beneath a potentially active drive", async () => {
  const calls: string[] = [];
  await assert.rejects(stopLoopResources({ drive: true, observe: true }, async path => {
    calls.push(path); throw new Error("pi_operation_timeout");
  }), /pi_operation_timeout/);
  assert.deepEqual(calls, ["/goal/s14_venom_navigation"]);
});
test("camera release failure is reported rather than claiming a complete stop", async () => {
  await assert.rejects(stopLoopResources({ observe: true }, async path => {
    if (path === "/resource/camera") throw new Error("camera_stop_failed");
  }), /camera_stop_failed/);
});

test("another camera owner is reported, never forcibly released or marked stopped", async () => {
  await assert.rejects(stopLoopResources({}, async path => {
    if (path === "/resource/camera") throw new Error("resource_in_use");
  }), /resource_in_use/);
});
