import assert from "node:assert/strict";
import { test } from "node:test";
import { configForConnection } from "../../web-next/lib/device-session";
import type { DeviceConfig, RiderStatus } from "../../web-next/lib/model";

const demo: DeviceConfig = { devices: [{ id: "demo", label: "Demo", mode: "demo" }], device_id: "demo", session: "old" };
const real: DeviceConfig = { devices: [{ id: "robot", label: "Robot", mode: "device" }], device_id: "robot", session: "new" };
const connection = (device_id: string | null, session: string) => ({ device_id, session } as RiderStatus["connection"]);

test("stable observations reuse the configuration without another request", async () => {
  const value = await configForConnection(demo, connection("demo", "old"), async () => { throw new Error("unexpected request"); });
  assert.equal(value, demo);
});

test("a host restart replaces the whole device allowlist and demo mode", async () => {
  let requests = 0;
  const value = await configForConnection(demo, connection("robot", "new"), async () => { requests++; return real; });
  assert.equal(value, real);
  assert.deepEqual(value.devices, real.devices);
  assert.equal(value.devices[0].mode, "device");
  assert.equal(requests, 1);
});

test("a changed selected device also refreshes configuration", async () => {
  const replacement = { ...real, session: "old" };
  assert.equal(await configForConnection(demo, connection("robot", "old"), async () => replacement), replacement);
});

test("a second session or device switch during refresh discards the observation", async () => {
  for (const fresh of [{ ...real, session: "newer" }, { ...real, device_id: null }]) {
    await assert.rejects(configForConnection(demo, connection("robot", "new"), async () => fresh), { message: "stale_device_session" });
  }
  assert.equal(demo.session, "old");
  assert.deepEqual(demo.devices.map(device => device.id), ["demo"]);
});
