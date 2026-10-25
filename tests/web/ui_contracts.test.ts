import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { calibrationDraft, calibrationValues, validCalibration } from "../../web-next/lib/calibration";
import { gestureBody, gestureFrom, validFaceSettings, validGesture } from "../../web-next/lib/face";
import { messages, translate } from "../../web-next/lib/i18n";
import { actuatorState, cameraOn, canRequestArm, commandAccepted, data, displayValue, errorCode, faceRunning, featureRows, formatNumber, frameRequested, sensorRows, telemetryAge, timestampMs, type JsonRecord, type RiderStatus } from "../../web-next/lib/model";

const contracts = JSON.parse(fs.readFileSync(new URL("../../venom_module_rider/fixtures/pi-contracts.json", import.meta.url), "utf8")) as { settings: JsonRecord; face: JsonRecord };
test("all module messages have complete PL, EN and DE values", () => {
  for (const [key, values] of Object.entries(messages)) {
    assert.equal(values.length, 3, key);
    for (const language of ["pl", "en", "de"] as const) assert.ok(translate(language, key).trim(), `${language}:${key}`);
  }
});
test("every literal UI translation key is module-owned", () => {
  const root = new URL("../../web-next/", import.meta.url);
  const sources = [fs.readFileSync(new URL("page.tsx", root), "utf8"), ...fs.readdirSync(new URL("components/", root)).filter(file => file.endsWith(".tsx")).map(file => fs.readFileSync(new URL(`components/${file}`, root), "utf8"))];
  for (const source of sources) for (const match of source.matchAll(/\bt\("([^"]+)"\)/g)) assert.ok(match[1] in messages, match[1]);
});
test("null measurements remain missing while a measured zero remains zero", () => {
  assert.equal(formatNumber(null, "en"), "—"); assert.equal(formatNumber(undefined, "pl"), "—"); assert.equal(formatNumber(0, "de"), "0");
  assert.equal(formatNumber(Number.NaN, "en"), "—"); assert.equal(formatNumber(1.5, "pl"), "1,5"); assert.equal(formatNumber(1.5, "en"), "1.5");
});
test("individual Pi field descriptors control display scaling and precision", () => {
  assert.equal(displayValue(.25, { display_factor: 1000, display_precision: 0 }, "en"), "250");
  assert.equal(displayValue(.35, { display_factor: 100, display_precision: 1 }, "pl"), "35");
  assert.equal(displayValue(95, { unit: "sdk_height_command", display_factor: 1, display_precision: 0 }, "en"), "95");
});
test("canonical sensor rows preserve source units, quality and unknown times", () => {
  const sample = { value: 0, unit: "sdk_raw", observed_at: null, ready: false };
  const status = { telemetry: { devices: { xgo: { sensor_quality: { sensors: { roll: sample } } } } } } as unknown as RiderStatus;
  assert.deepEqual(sensorRows(status), [["roll", sample]]);
  assert.equal(timestampMs(null), null); assert.equal(timestampMs(1720000000), 1720000000000);
});
test("posture feedback reads the same canonical safety actuator state in every view", () => {
  const actual = { height: { value: 95, ts: null }, balance: { enabled: false } };
  const status = { control: { safety: { actuators: actual }, height: { value: 115 } } } as unknown as RiderStatus;
  assert.equal(actuatorState(status), actual);
  assert.deepEqual(actuatorState(null), {});
});
test("catalog metadata merges with separate goal runtime state", () => {
  const status = { catalog: { features: [{ name: "s2_camera_preview", can_move: false }] }, logic: { features: { s2_camera_preview: { enabled: true, state: "running" } } } } as unknown as RiderStatus;
  assert.equal(featureRows(status)[0].active, true); assert.equal(featureRows(status)[0].can_move, false);
});
test("API envelopes and errors are decoded without translating them into physical success", () => {
  assert.deepEqual(data({ meta: {}, data: { value: 1 } }), { value: 1 });
  assert.equal(errorCode({ data: { detail: { code: "stale_device_session" } } }), "stale_device_session");
  assert.equal(errorCode({ data: { error: { code: "face_settings_conflict" } } }), "face_settings_conflict");
});
test("the exact Pi manual settings fixture validates; incompatible direction bounds fail", () => {
  const draft = calibrationDraft(contracts.settings); assert.equal(validCalibration(draft, contracts.settings), true);
  draft.directions.forward.minimum = .9; draft.directions.forward.maximum = .2;
  assert.equal(validCalibration(draft, contracts.settings), false);
});
test("calibration patches exclude legacy speed aliases and carry producer-defined smoothing", () => {
  const values = calibrationValues(calibrationDraft(contracts.settings), contracts.settings);
  assert.equal("linear_speed" in values, false); assert.equal("pulse_duration" in values, false); assert.equal("speed_limit" in values, false);
  assert.equal(values.linear_rate, 3); assert.equal(values.ramp_mode, 0);
});
test("the exact Pi face gestures validate and built-in saves are copies", () => {
  const gestures = contracts.face.gestures as unknown[];
  for (const row of gestures) { const gesture = gestureFrom(row); assert.equal(validGesture(gesture, contracts.face), true, gesture.name); assert.equal("id" in gestureBody(gesture), false); assert.equal("builtin" in gestureBody(gesture), false); }
});
test("face segment validation rejects overlapping segments, invalid fields and out-of-range values", () => {
  const gesture = gestureFrom((contracts.face.gestures as unknown[])[0]);
  gesture.tracks["mouth.open"] = [{ t0: 0, t1: .4, v0: 0, v1: 1, ease: "lin" }, { t0: .3, t1: .6, v0: 1, v1: 0, ease: "lin" }];
  assert.equal(validGesture(gesture, contracts.face), false);
  gesture.tracks = {}; gesture.initial["mouth.open"] = 2; assert.equal(validGesture(gesture, contracts.face), false);
});
test("face runtime settings validate producer choices and reject non-finite or unknown values", () => {
  const settings = { ...contracts.face.settings as JsonRecord }, descriptors = contracts.face.descriptors as JsonRecord;
  assert.equal(validFaceSettings(settings, descriptors, contracts.face), true);
  assert.equal(validFaceSettings({ ...settings, tempo: Number.NaN }, descriptors, contracts.face), false);
  assert.equal(validFaceSettings({ ...settings, skin: "unknown" }, descriptors, contracts.face), false);
  assert.equal(validFaceSettings({ ...settings, active_gestures: ["unknown"] }, descriptors, contracts.face), false);
});

test("known OFF and unknown cameras never trigger a frame poll", () => {
  const status = (camera: JsonRecord) => ({ health: { camera } } as unknown as RiderStatus);
  assert.equal(cameraOn(status({ activity: { on: false } })), false);
  assert.equal(cameraOn(status({ activity: { on: true } })), true);
  assert.equal(cameraOn(status({ state: "OFF" })), false);
  assert.equal(cameraOn(status({})), false);
});
test("face activity comes from the canonical nested Pi runtime state", () => {
  const status = (runtime: JsonRecord) => ({ face: { runtime } } as unknown as RiderStatus);
  assert.equal(faceRunning(status({ meta: {}, data: { state: { running: true, playing: false, sink: "lcd" } } })), true);
  assert.equal(faceRunning(status({ state: { running: false, playing: false }, running: true })), false);
  assert.equal(faceRunning(status({})), false);
  assert.equal(faceRunning(null), false);
});
test("late frames are discarded after hiding, changing a stream or leaving its view", () => {
  assert.equal(frameRequested("camera", "/camera/raw", "control", { camera: "raw", face: false }), true);
  assert.equal(frameRequested("camera", "/camera/raw", "samples", { camera: "raw", face: false }), true);
  assert.equal(frameRequested("camera", "/camera/raw", "samples", { camera: null, face: false }), false);
  assert.equal(frameRequested("camera", "/camera/venom", "control", { camera: "venom", face: false }), true);
  assert.equal(frameRequested("camera", "/camera/proc", "control", { camera: "venom", face: false }), false);
  assert.equal(frameRequested("camera", "/camera/venom", "control", { camera: null, face: false }), false);
  assert.equal(frameRequested("camera", "/camera/venom", "compute", { camera: "venom", face: false }), false);
  assert.equal(frameRequested("camera", "/camera/raw", "control", { camera: null, face: false }), false);
  assert.equal(frameRequested("camera", "/camera/raw", "control", { camera: "proc", face: false }), false);
  assert.equal(frameRequested("camera", "/camera/raw", "face", { camera: "raw", face: true }), false);
  assert.equal(frameRequested("face", "/face/runtime/frame", "face", { camera: null, face: true }), true);
  assert.equal(frameRequested("face", "/face/runtime/frame", "face", { camera: null, face: false }), false);
  assert.equal(frameRequested("face", "/face/runtime/frame", "control", { camera: "raw", face: true }), false);
});
test("ARM is an operator request independent of observation availability", () => {
  const ready = { connection: { status: "online", configured: true }, control: { safety: { state: "DISARMED", ready: true, can_move: false, stale: false, generation: "instance" } } } as unknown as RiderStatus;
  assert.equal(canRequestArm(ready), true);
  for (const safety of [{ ready: false }, { stale: true }]) {
    assert.equal(canRequestArm({ ...ready, control: { safety: { ...data(ready.control).safety as JsonRecord, ...safety } } }), true);
  }
  assert.equal(canRequestArm({ ...ready, control: { safety: { generation: null } } }), false);
  assert.equal(canRequestArm({ ...ready, connection: { ...ready.connection, status: "offline" } }), true);
  assert.equal(canRequestArm(null), false);
});

test("pending transport acceptance stays pending and errors stay rejected", () => {
  assert.equal(commandAccepted({ ok: true, meta: {}, data: { pending: true }, operation: { status: "pending" } }), true);
  assert.equal(commandAccepted({ ok: false, meta: {}, data: { error: "rejected" } }), false);
});

test("parallel observations use the latest Pi source clock for the shared telemetry age", () => {
  const status = { control: { server_time: 100 }, telemetry: { server_time: 100.01, devices: { xgo: { observed_at: 100.005 } } } } as unknown as RiderStatus;
  assert.ok(Math.abs((telemetryAge(status) ?? -1) - .005) < 1e-9);
  status.control = { server_time: 101 };
  assert.ok(Math.abs((telemetryAge(status) ?? -1) - .995) < 1e-9);
  assert.equal(telemetryAge({ telemetry: { devices: { xgo: {} } } } as unknown as RiderStatus), null);
});
