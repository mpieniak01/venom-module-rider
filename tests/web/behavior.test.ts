import assert from "node:assert/strict";
import { test } from "node:test";
import { behaviorCatalog, behaviorDraft, behaviorDraftStale, behaviorSwitchBlock, behaviorWrite, temporalView } from "../../web-next/lib/behavior";
import { translate } from "../../web-next/lib/i18n";
import type { JsonRecord, RiderStatus } from "../../web-next/lib/model";

function policy(active = "legacy", revision = 4): JsonRecord {
  return { revision, behavior: { version: 1, profile_id: active, profile_revision: 1 },
    behavior_catalog: { version: 1, supported: true, active: { version: 1, profile_id: active, profile_revision: 1 },
      effective_policy_sha256: "a".repeat(64), profiles: ["legacy", "cautious", "balanced", "fluid"].map(id => ({
        id, profile_revision: 1, status: id === "legacy" ? "legacy" : "experimental", parameters: { server_owned_value: 137 },
      })) } };
}
function status(): RiderStatus {
  return { connection: { status: "online" }, control: { safety: { state: "ARMED", stale: false, moving: false, operator_control: { state: "paused" } } }, errors: {} } as unknown as RiderStatus;
}
test("missing capability preserves legacy and never invents selectable profiles", () => {
  for (const value of [null, { revision: 4 }, { revision: 4, behavior_catalog: { version: 1, supported: false } }]) {
    const result = behaviorCatalog(value);
    assert.equal(result.supported, false); assert.equal(result.active.profile_id, "legacy");
    assert.deepEqual(result.profiles.map(row => row.id), ["legacy"]);
    assert.equal(behaviorDraft(value ?? {}, "fluid", "panel"), null);
  }
});
test("catalog exposes only server revisions and rejects malformed hashes, IDs, duplicates and missing active rows", () => {
  const current = policy(); assert.equal(behaviorCatalog(current).supported, true);
  assert.deepEqual(behaviorCatalog(current).profiles[2].parameters, { server_owned_value: 137 });
  const invalid: JsonRecord[] = [
    { ...current, behavior_catalog: { ...(current.behavior_catalog as object), effective_policy_sha256: "invalid" } },
    { ...current, behavior_catalog: { ...(current.behavior_catalog as object), active: { version: 1, profile_id: "future", profile_revision: 1 } } },
    { ...current, behavior_catalog: { ...(current.behavior_catalog as object), profiles: [{ id: "fluid", profile_revision: 1, status: "experimental" }] } },
    { ...current, behavior_catalog: { ...(current.behavior_catalog as object), profiles: [{ id: "legacy", profile_revision: 1, status: "legacy" }, { id: "legacy", profile_revision: 1, status: "legacy" }] } },
  ];
  for (const value of invalid) assert.equal(behaviorCatalog(value).supported, false);
});
test("profile save sends only selection and original CAS, excluding parameters and movement approval", () => {
  const current = policy(), draft = behaviorDraft(current, "fluid", "session")!;
  assert.deepEqual(behaviorWrite(draft, current, "session"), { revision: 4, values: { behavior: { version: 1, profile_id: "fluid", profile_revision: 1 } } });
  assert.equal(behaviorCatalog(current).active.profile_id, "legacy");
});
test("external policy revision, effective hash, profile revision and session invalidate an existing draft", () => {
  const current = policy(), draft = behaviorDraft(current, "balanced", "one")!;
  assert.equal(behaviorDraftStale(draft, current, "one"), false);
  const next = policy("legacy", 5);
  for (const [value, session] of [
    [next, "one"], [current, "two"],
    [{ ...current, behavior_catalog: { ...(current.behavior_catalog as object), effective_policy_sha256: "b".repeat(64) } }, "one"],
    [{ ...current, behavior_catalog: { ...(current.behavior_catalog as object), profiles: behaviorCatalog(current).profiles.map(row => ({ ...row, profile_revision: 2 })) } }, "one"],
  ] as [JsonRecord, string][]) {
    assert.equal(behaviorWrite(draft, value, session), null);
  }
});
test("fresh stopped ARMED pause allows selection, active movement and observation block it", () => {
  const s = status();
  assert.equal(behaviorSwitchBlock(s, true, {}, false), "");
  assert.equal(behaviorSwitchBlock(s, true, { drive: true }, false), "behavior.block.motion");
  assert.equal(behaviorSwitchBlock(s, true, { observe: true }, false), "behavior.block.observation");
  assert.equal(behaviorSwitchBlock(s, false, {}, false), "behavior.block.stale");
  assert.equal(behaviorSwitchBlock(s, true, {}, true), "behavior.block.busy");
  assert.equal(behaviorSwitchBlock(null, true, {}, false), "behavior.block.offline");
  (s.control!.safety as JsonRecord).moving = true;
  assert.equal(behaviorSwitchBlock(s, true, {}, false), "behavior.block.motion");
});
test("diagnostics keep raw result separate from temporal policy and do not invent boundaries or permission", () => {
  assert.equal(temporalView({}).available, false);
  const view = temporalView({ running: true, stale: false, decision: { raw_state: "UNKNOWN", behavior: { profile_id: "balanced", profile_revision: 1 },
    temporal: { action: "slow", state: "CLEAR", reason: "brief_uncertainty", risk: "warning", near_guard_clear: true, evidence_frames: 3, speed_factor: .5, spatial: {} } } });
  assert.equal(view.raw, "UNKNOWN"); assert.equal(view.stabilized, "CLEAR");
  assert.equal(view.action, "slow"); assert.equal(view.boundaryPoints, 0);
  assert.equal(view.current, true); assert.equal(view.evidenceFrames, 3);
  assert.equal(temporalView({ stale: true, decision: { temporal: { action: "go", state: "CLEAR" } } }).current, false);
});
test("profile explanations and controls have complete Polish, English and German translations", () => {
  for (const locale of ["pl", "en", "de"] as const) for (const id of ["legacy", "cautious", "balanced", "fluid"]) {
    for (const prefix of ["behavior.profile.", "behavior.description."]) {
      const key = prefix + id; assert.notEqual(translate(locale, key), key);
    }
  }
});
