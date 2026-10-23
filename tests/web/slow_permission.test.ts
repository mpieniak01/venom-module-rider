import assert from "node:assert/strict";
import { test } from "node:test";
import { slowLeaseFactor } from "../../web-next/lib/behavior";
import { decisionView, executionHeadline, motionPermission } from "../../web-next/lib/loop-controls";
import { translate } from "../../web-next/lib/i18n";
import type { JsonRecord, RiderStatus } from "../../web-next/lib/model";

function fixture() {
  const behavior = { version: 1, profile_id: "balanced", profile_revision: 1 };
  const hash = "b".repeat(64), model = "a".repeat(64);
  const policy: JsonRecord = { revision: 4, mode: "drive", verified: true, speed: .1, max_motion_ms: 150,
    calibration_id: "accepted", model_sha256: model, behavior: { ...behavior },
    behavior_catalog: { version: 1, supported: true, active: { ...behavior }, effective_policy_sha256: hash,
      profiles: [{ id: "balanced", profile_revision: 1, status: "experimental", parameters: { slow_speed_factor: .5 } }] } };
  const decision = { state: "unknown", raw_state: "unknown", action: "slow", vision_action: "slow", clear_count: 3,
    run_id: "run-4", sequence: 7, config_revision: 4, model_sha256: model, valid_for_ms: 150,
    behavior: { ...behavior }, effective_policy_sha256: hash,
    temporal: { version: 1, state: "unknown", action: "slow", risk: "caution", near_guard_clear: true, speed_factor: .5, evidence_frames: 4 } };
  const state: JsonRecord = { running: true, stale: false, latched: false, paused: false, mode: "drive",
    run_id: "run-4", generation: "generation-1", config_revision: 4, permit_remaining_ms: 140, age_s: .02, decision };
  const safety: JsonRecord = { state: "ARMED", generation: "generation-1", owner: "navigation", can_move: true,
    moving: false, stale: false, ready: true, capabilities: { move: { available: true } }, remote_decision_deadline_supported: true };
  const status = { connection: { configured: true, status: "online" }, errors: {}, control: { safety } } as unknown as RiderStatus;
  const detector = { sha256: model, clear_frames_required: 3 };
  return { state, decision, policy, safety, status, detector };
}
test("a valid Pi SLOW lease stays distinct from raw UNKNOWN, full GO and actual motor movement", () => {
  const f = fixture();
  assert.equal(slowLeaseFactor(f.state, f.policy, f.safety), .5);
  const view = decisionView(f.state, f.detector);
  assert.equal(view.reply, "slow"); assert.equal(view.vision, "stop");
  assert.equal(f.decision.raw_state, "unknown"); assert.equal(f.safety.moving, false);
  const permission = motionPermission(f.state, { drive: true }, f.policy, f.detector, f.status);
  assert.equal(permission.venom, true); assert.equal(permission.reason, "permissionSlow");
  assert.equal(executionHeadline(f.state, { drive: true }, f.status, permission), "permissionVenomSlow");
});
test("a SLOW request without Pi lease evidence is shown but never claims a motion grant", () => {
  const f = fixture(); delete f.state.permit_remaining_ms;
  assert.equal(decisionView(f.state, f.detector).reply, "slow");
  const permission = motionPermission(f.state, { drive: true }, f.policy, f.detector, f.status);
  assert.equal(permission.venom, false);
  assert.equal(permission.reason, "permissionSlowUnconfirmed");
  assert.equal(executionHeadline(f.state, { drive: true }, f.status, permission), "permissionVenomSlowUnconfirmed");
});
test("legacy and unsupported peers never treat a SLOW response as permitted", () => {
  for (const change of [
    (f: ReturnType<typeof fixture>) => { f.policy.behavior = { version: 1, profile_id: "legacy", profile_revision: 1 }; },
    (f: ReturnType<typeof fixture>) => { delete f.policy.behavior; delete f.policy.behavior_catalog; },
    (f: ReturnType<typeof fixture>) => { (f.policy.behavior_catalog as JsonRecord).supported = false; },
  ]) {
    const f = fixture(); change(f);
    assert.equal(motionPermission(f.state, { drive: true }, f.policy, f.detector, f.status).venom, false);
  }
});
test("expired, paused, latched and stale SLOW permits never appear as a current grant", () => {
  for (const patch of [
    { permit_remaining_ms: 49 }, { permit_remaining_ms: 80, age_s: .04 }, { age_s: undefined },
    { paused: true }, { paused: undefined }, { latched: true }, { stale: true }, { running: false },
  ]) {
    const f = fixture(); Object.assign(f.state, patch);
    assert.equal(slowLeaseFactor(f.state, f.policy, f.safety), null);
    assert.equal(motionPermission(f.state, { drive: true }, f.policy, f.detector, f.status).venom, false);
  }
  const f = fixture();
  assert.equal(decisionView({ ...f.state, stale: true }, f.detector).reply, "none");
});
test("SLOW must bind the current run, configuration, model, generation and immutable behavior hash", () => {
  const mutations: ((f: ReturnType<typeof fixture>) => void)[] = [
    f => { f.decision.run_id = "old-run"; }, f => { f.decision.config_revision = 3; },
    f => { f.state.generation = "old-generation"; }, f => { f.state.config_revision = 3; },
    f => { f.decision.model_sha256 = "c".repeat(64); }, f => { f.decision.effective_policy_sha256 = "c".repeat(64); },
    f => { f.decision.behavior.profile_revision = 2; },
  ];
  for (const mutate of mutations) {
    const f = fixture(); mutate(f);
    assert.equal(slowLeaseFactor(f.state, f.policy, f.safety), null);
    assert.equal(motionPermission(f.state, { drive: true }, f.policy, f.detector, f.status).venom, false);
  }
});
test("inconsistent temporal state, speed factor, guard or evidence cannot authorize SLOW", () => {
  const patches = [
    { speed_factor: 1 }, { speed_factor: -1 }, { speed_factor: Number.NaN }, { action: "go" }, { risk: "clear" },
    { near_guard_clear: false }, { evidence_frames: 2 }, { version: 2 },
  ];
  for (const patch of patches) {
    const f = fixture(); Object.assign(f.decision.temporal, patch);
    assert.equal(slowLeaseFactor(f.state, f.policy, f.safety), null);
  }
});
test("a model-side SLOW lease never bypasses stale, manual, disarmed or inhibited executor authority", () => {
  for (const patch of [{ stale: true }, { owner: "manual" }, { state: "DISARMED" }, { can_move: false }, { execution: { inhibit: "operator" } }]) {
    const f = fixture(); Object.assign(f.safety, patch);
    assert.notEqual(motionPermission(f.state, { drive: true }, f.policy, f.detector, f.status).venom, true);
  }
});
test("SLOW labels describe limited lease evidence separately from motor acknowledgement in every language", () => {
  for (const locale of ["pl", "en", "de"] as const) {
    for (const key of ["permissionSlow", "permissionSlowUnconfirmed", "permissionVenomSlow", "permissionVenomSlowUnconfirmed", "decisionSlow", "decisionSlowUnconfirmed"]) {
      assert.notEqual(translate(locale, key), key);
    }
  }
});
