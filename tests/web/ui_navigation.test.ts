import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import { freshControl, navigationAvailable, navigationView, observationClock, retainControl } from "../../web-next/lib/navigation";
import type { RiderStatus } from "../../web-next/lib/model";
const fixture = JSON.parse(fs.readFileSync(new URL("../../venom_module_rider/fixtures/pi-contracts.json", import.meta.url), "utf8"));
function state(): RiderStatus {
  return { connection: { configured: true, device_id: "fixture", session: "fixture", status: "online" }, polling: { control_ms: 500 }, settings: { ...fixture.settings, revision: 1 }, errors: {}, control: { server_time: 10, safety: { state: "ARMED", owner: "manual", panel_motion_enabled: true, can_move: true, ready: true, capabilities: { move: { available: true } }, generation: "one", observed_at: 10, stale: false } } } as unknown as RiderStatus;
}
test("manual arrows stay available across slow diagnostics and temporary connection loss", () => {
  const current = state();
  for (const status of ["online", "degraded", "offline"] as const) {
    current.connection.status = status;
    assert.equal(navigationAvailable(current), true);
    assert.equal(freshControl(current, { at: 10000, offset: 0, rtt: 600 }, 11000, 500), false);
  }
});
test("confirmed disarm, ESTOP, revoked permission and device deselection disable arrows", () => {
  for (const patch of [{ state: "DISARMED" }, { state: "ESTOP" }, { owner: "agent" }, { panel_motion_enabled: false }, { generation: null }, { capabilities: { move: { available: false } } }]) {
    const current = state(); Object.assign(current.control!.safety as object, patch);
    assert.equal(navigationAvailable(current), false);
  }
  const current = state(); current.connection.configured = false;
  assert.equal(navigationAvailable(current), false); assert.equal(navigationAvailable(null), false);
});
test("missing reads preserve confirmed control but never freshen the command gate", () => {
  const previous = state(); const next = { ...previous, control: null, settings: null, errors: { control: "pi_unreachable" }, connection: { ...previous.connection, status: "degraded" as const } };
  const retained = retainControl(previous, next);
  assert.equal(retained.control, previous.control); assert.equal(retained.settings, previous.settings);
  assert.equal(navigationAvailable(retained), true);
  assert.equal(freshControl(retained, { at: 10000, offset: 0, rtt: 20 }, 10020, 500), false);
  assert.equal(observationClock(null, next, 10000, 10020), null);
});
test("confirmed disarm replaces ARM immediately; an older diagnostics response cannot roll it back", () => {
  const previous = state(); const disarmed = state(); disarmed.control!.server_time = 11; (disarmed.control!.safety as Record<string, unknown>).state = "DISARMED";
  const accepted = retainControl(previous, disarmed); assert.equal(navigationAvailable(accepted), false);
  assert.equal(navigationAvailable(retainControl(accepted, previous)), false);
});
test("device or host session changes never preserve the old control state", () => {
  const previous = state();
  for (const connection of [{ ...previous.connection, session: "new" }, { ...previous.connection, device_id: "new" }]) {
    const next = { ...previous, connection, control: null, settings: null };
    assert.equal(retainControl(previous, next), next); assert.equal(navigationAvailable(next), false);
  }
});
test("a slow diagnostics response does not replace a good clock; command freshness stays bounded", () => {
  const current = state(); const old = { at: 10000, offset: 0, rtt: 20 };
  assert.equal(observationClock(old, current, 10000, 10500), old);
  assert.equal(freshControl(current, old, 10020, 500), true);
  assert.equal(freshControl(current, old, 11001, 500), false);
  const fresh = observationClock(null, current, 9990, 10010)!;
  assert.equal(fresh.rtt, 20); assert.equal(fresh.offset, 0);
});

test("a successful short refresh clears control errors and preserves measurements", () => {
  const previous = state(); previous.errors = { control: "pi_unreachable", health: "timeout" }; previous.telemetry = { battery: .5 };
  const refreshed = state(); refreshed.telemetry = null;
  const view = navigationView(previous, refreshed);
  assert.deepEqual(view.errors, { health: "timeout" }); assert.equal(view.telemetry, previous.telemetry);
  assert.equal(freshControl(view, { at: 10000, offset: 0, rtt: 20 }, 10020, 500), true);
});

test("Pi heartbeat freshness is authoritative instead of an invented browser heartbeat limit", () => {
  const current = state(); const safety = current.control!.safety as Record<string, unknown>;
  safety.observed_at = 8.5;
  assert.equal(freshControl(current, { at: 10000, offset: 0, rtt: 20 }, 10020, 500), true);
  safety.stale = true;
  assert.equal(freshControl(current, { at: 10000, offset: 0, rtt: 20 }, 10020, 500), false);
});
