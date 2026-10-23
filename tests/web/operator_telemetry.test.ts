import assert from "node:assert/strict";
import { test } from "node:test";
import { operatorTelemetry } from "../../web-next/lib/operator-telemetry";
import type { JsonRecord, RiderStatus } from "../../web-next/lib/model";
function status(samples: JsonRecord): RiderStatus {
  return { connection: { status: "online", configured: true, device_id: "fixture", session: "fixture", mode: "demo" },
    polling: {}, errors: {}, control: { server_time: 100 }, health: null, system: null, settings: null,
    resources: {}, catalog: null, logic: null, history: null, face: { library: null, runtime: null },
    telemetry: { server_time: 100, devices: { xgo: { sensor_quality: { sensors: samples } } } } };
}
const sample = (value: unknown, unit: string) => ({ value, unit, ready: true, stale: false, observed_at: 99, max_age_s: 2.5 });
const sensors = () => ({ battery: sample(.62, "ratio"), roll: sample(2.5, "sdk_raw"), pitch: sample(-43.8, "sdk_raw"), yaw: sample(-925.9, "sdk_raw") });
test("compact telemetry converts battery ratio and retains raw SDK axes without wrapping", () => {
  const result = operatorTelemetry(status(sensors()));
  assert.equal(result.battery.value, 62);
  assert.deepEqual(result.axes.map(axis => axis.value), [2.5, -43.8, -925.9]);
  assert.equal(result.unit, "sdk_raw");
  assert.equal(result.quality, "fresh");
});
test("measured battery zero remains zero and percentages are not multiplied twice", () => {
  for (const [value, unit, expected] of [[0, "ratio", 0], [99, "%", 99], [25, "percent", 25], [40, "pct", 40]] as const)
    assert.equal(operatorTelemetry(status({ battery: sample(value, unit) })).battery.value, expected);
});
test("missing or unknown-unit battery never becomes a plausible charge", () => {
  for (const [value, unit] of [[null, "ratio"], [101, "%"], [1.1, "ratio"], [-1, "%"], [75, "sdk_raw"], [Number.NaN, "ratio"]] as const)
    assert.equal(operatorTelemetry(status({ battery: sample(value, unit) })).battery.value, null);
  assert.equal(operatorTelemetry(null).quality, "missing");
});
test("expired sensor timestamps override a retained ready flag", () => {
  const input = status(sensors());
  input.control = { server_time: 103 };
  const result = operatorTelemetry(input);
  assert.equal(result.quality, "stale");
  assert.equal(result.battery.value, null);
  assert.ok(result.axes.every(axis => axis.value === null));
});
test("explicit stale, offline, unready and unknown samples are not current measurements", () => {
  for (const flags of [{ stale: true }, { offline: true }, { ready: false }, { ready: undefined }]) {
    const result = operatorTelemetry(status({ battery: { ...sample(.8, "ratio"), ...flags } }));
    assert.equal(result.battery.value, null);
    assert.notEqual(result.quality, "fresh");
  }
});
test("offline or failed telemetry clears displayed readings while unrelated errors do not", () => {
  const offline = status(sensors()); offline.connection.status = "offline";
  assert.equal(operatorTelemetry(offline).battery.value, null);
  const failure = status(sensors()); failure.errors.telemetry = "timeout";
  assert.equal(operatorTelemetry(failure).battery.value, null);
  const unrelated = status(sensors()); unrelated.errors.history = "timeout";
  assert.equal(operatorTelemetry(unrelated).quality, "fresh");
});
test("partial readings retain each available axis and its own unit", () => {
  const result = operatorTelemetry(status({ battery: sample(.5, "ratio"), roll: sample(0, "deg"), pitch: sample(1, "rad") }));
  assert.equal(result.quality, "partial");
  assert.equal(result.unit, "");
  assert.deepEqual(result.axes.map(axis => [axis.value, axis.unit]), [[0, "deg"], [1, "rad"], [null, ""]]);
});
test("legacy age and observation envelopes use the same Pi freshness rules", () => {
  const input = status({});
  input.telemetry = { meta: {}, data: { devices: { xgo: { observation: { sensor_quality: { sensors: {
    battery: { value: .5, unit: "ratio", ready: true, age_s: 4, max_age_s: 2.5 }
  } } } } } } };
  assert.equal(operatorTelemetry(input).battery.quality, "stale");
});
