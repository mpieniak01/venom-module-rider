import assert from "node:assert/strict";
import { test } from "node:test";
import { analysisProfileSettings, semanticProfile, type AnalysisProfile } from "../../web-next/lib/analysis-profile";

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
