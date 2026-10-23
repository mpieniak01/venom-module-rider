import assert from "node:assert/strict";
import { test } from "node:test";
import { analysisProfileSettings, LEGACY_ANALYSIS_BEHAVIOR, riderAnalysisSettings, semanticProfile, type AnalysisProfile } from "../../web-next/lib/analysis-profile";

const geometry = { mode: "ground", fx: 1.4, fy: 1.7, radius_ratio: .4, lookahead_ratio: 3 };
const perception = { residual_limit: .06, outlier_limit: .005, slope_min: 2 };
test("semantic offline profiles force one image and semantic engine without depth calibration or live model selection", () => {
  for (const field of [{ task: "semantic" }, { backend: "semantic" }]) {
    const profile: AnalysisProfile = { id: "segformer-b0-ade20k", installed: true, windows: [1], ...field };
    assert.equal(semanticProfile(profile), true);
    const result = analysisProfileSettings(profile, 5, geometry, perception);
    assert.equal(result.window, 1);
    assert.deepEqual(result.geometry, { mode: "image", fx: null, fy: null, radius_ratio: null, lookahead_ratio: 2 });
    assert.deepEqual(result.perception, { engine: "segformer" });
    assert.equal("model_id" in result.perception, false);
    assert.equal("revision" in result, false);
  }
});
test("depth offline profiles preserve temporal windows and exact calibrated settings without mutating drafts", () => {
  const profile = { id: "da2-default", installed: true, windows: [1,3,5] };
  assert.equal(semanticProfile(profile), false);
  assert.equal(semanticProfile(undefined), false);
  const settings = analysisProfileSettings(profile, 3, geometry, perception);
  assert.equal(settings.window, 3);
  assert.deepEqual(settings.geometry, geometry);
  assert.deepEqual(settings.perception, { ...perception, engine: "depth_plane" });
  settings.geometry.fx = 99;
  settings.perception.residual_limit = .01;
  assert.equal(geometry.fx, 1.4);
  assert.equal(perception.residual_limit, .06);
});

test("semantic experiment preserves independent model overrides without copying depth knobs or live identity", () => {
  const models = {
    "segformer-b0-ade20k": { version: 1, surface: "floor_rug", pixel_confidence: .82 },
    "yolo26n-sem-ade20k": { version: 1, surface: "floor_only", pixel_confidence: .9 },
  };
  const source = { ...perception, semantic_models: models, model_id: "depth-anything-v2-indoor-small", artifact_sha256: "a".repeat(64) };
  const result = analysisProfileSettings({ id: "segformer-b0-ade20k", installed: true, windows: [1], task: "semantic" }, 5, geometry, source);
  assert.deepEqual(result.perception, { engine: "segformer", semantic_models: models });
  (result.perception.semantic_models as typeof models)["segformer-b0-ade20k"].surface = "floor_only";
  assert.equal(models["segformer-b0-ade20k"].surface, "floor_rug");
  assert.equal(result.window, 1); assert.equal(result.geometry.mode, "image");
});
test("explicit Rider import freezes only classification behavior, thresholds and corridor for any selected research model", () => {
  const policy = { revision: 12, mode: "drive", verified: true, calibration_id: "ignored", model_sha256: "b".repeat(64),
    behavior: { version: 1, profile_id: "fluid", profile_revision: 1 }, corridor: [.3, .7, .7, .95],
    perception: { ...perception, model_id: "yolo26n-sem-ade20k", artifact_sha256: "c".repeat(64),
      semantic_models: { "segformer-b0-ade20k": { version: 1, surface: "floor_rug", pixel_confidence: .82 } } } };
  const snapshot = riderAnalysisSettings(policy);
  assert.deepEqual(snapshot.behavior, policy.behavior); assert.deepEqual(snapshot.corridor, policy.corridor);
  assert.equal(snapshot.revision, 12); assert.equal(snapshot.perception.model_id, undefined); assert.equal(snapshot.perception.artifact_sha256, undefined);
  assert.equal("verified" in snapshot, false); assert.equal("calibration_id" in snapshot, false); assert.equal("mode" in snapshot, false);
  policy.behavior.profile_id = "legacy"; policy.corridor[0] = .1;
  policy.perception.semantic_models["segformer-b0-ade20k"].pixel_confidence = .99;
  assert.equal(snapshot.behavior.profile_id, "fluid"); assert.equal(snapshot.corridor[0], .3);
  assert.equal(((snapshot.perception.semantic_models as Record<string, Record<string, unknown>>)["segformer-b0-ade20k"]).pixel_confidence, .82);
  const depth = analysisProfileSettings({ id: "da2-default", installed: true, windows: [1] }, 1, geometry, policy.perception);
  assert.equal(depth.perception.model_id, undefined); assert.equal(depth.perception.artifact_sha256, undefined);
});
test("an older valid config imports Legacy without needing a model catalog or connectivity for subsequent runs", () => {
  const snapshot = riderAnalysisSettings({ revision: 1, corridor: [.35,.8,.65,.95] });
  assert.deepEqual(snapshot.behavior, LEGACY_ANALYSIS_BEHAVIOR); assert.deepEqual(snapshot.perception, perception);
  assert.deepEqual(analysisProfileSettings({ id: "segformer-b0-ade20k", installed: true, windows: [1], task: "semantic" }, 1, geometry, snapshot.perception).perception, { engine: "segformer" });
});
test("malformed imported behavior, corridor and semantic thresholds are rejected without producing an experiment snapshot", () => {
  const valid = { revision: 1, corridor: [.35,.8,.65,.95], perception };
  for (const invalid of [
    { ...valid, behavior: { version: 9, profile_id: "fluid", profile_revision: 1 } },
    { ...valid, corridor: [.7,.8,.3,.95] },
    { ...valid, perception: { ...perception, residual_limit: Number.NaN } },
    { ...valid, perception: { ...perception, semantic_models: { "segformer-b0-ade20k": { version: 1, surface: "floor_rug", pixel_confidence: 1.5 } } } },
  ]) assert.throws(() => riderAnalysisSettings(invalid), /analysis_rider_settings_invalid/);
});
