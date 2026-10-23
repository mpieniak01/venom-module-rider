import assert from "node:assert/strict";
import { test } from "node:test";
import { effectiveTuning, semanticTuning, tuningDraft, tuningDraftStale, tuningModelId, tuningWrite, validTuning, TUNING_FIELDS, TUNING_BOUNDS } from "../../web-next/lib/model-tuning";
import type { JsonRecord } from "../../web-next/lib/model";

const seg = "segformer-b0-ade20k", yolo = "yolo26n-sem-ade20k";
const policy = (): JsonRecord => ({ revision: 7, model_tuning_supported: true, perception: {
  engine: "segformer", model_id: seg, artifact_sha256: "a".repeat(64),
  residual_limit: .06, outlier_limit: .005, slope_min: 2,
  semantic_models: { [yolo]: { version: 1, surface: "floor_rug", pixel_confidence: .9 } },
} });
test("semantic defaults preserve floor-only and inherit each threshold without inventing profile defaults", () => {
  assert.deepEqual(semanticTuning({}, seg), { version: 1, surface: "floor_only", pixel_confidence: null,
    min_floor_fraction: null, max_uncertain_fraction: null, min_region_fraction: null });
  assert.equal(effectiveTuning(policy()), null);
});
test("model-specific floor+ rug edit preserves other model settings, exact model identity and original CAS", () => {
  const original = policy(), draft = tuningDraft(original, "session")!;
  draft.perception.semantic_models = { ...draft.perception.semantic_models as JsonRecord,
    [seg]: { ...semanticTuning(draft.perception, seg), surface: "floor_rug" } };
  const body = tuningWrite(draft, original, "session")!;
  assert.equal(body.revision, 7); assert.deepEqual(Object.keys(body.values as JsonRecord), ["perception"]);
  assert.equal((body.values as { perception: JsonRecord }).perception.model_id, seg);
  assert.deepEqual((draft.perception.semantic_models as JsonRecord)[yolo], ((original.perception as JsonRecord).semantic_models as JsonRecord)[yolo]);
  assert.equal(((original.perception as JsonRecord).semantic_models as JsonRecord)[seg], undefined);
});
test("older backend cannot receive semantic tuning, and active model, revision and session changes invalidate drafts", () => {
  const original = policy(), draft = tuningDraft(original, "one")!;
  assert.equal(tuningWrite(draft, { ...original, model_tuning_supported: false }, "one"), null);
  for (const next of [{ ...original, revision: 8 }, { ...original, perception: { ...original.perception as JsonRecord, model_id: yolo } }]) {
    assert.equal(tuningDraftStale(draft, next, "one"), true); assert.equal(tuningWrite(draft, next, "one"), null);
  }
  assert.equal(tuningWrite(draft, original, "two"), null);
});
test("all custom thresholds enforce finite producer bounds while nullable fields inherit", () => {
  for (const field of TUNING_FIELDS) {
    const p = policy(), draft = tuningDraft(p, "one")!;
    for (const value of [null, ...TUNING_BOUNDS[field]]) {
      draft.perception.semantic_models = { [seg]: { ...semanticTuning({}, seg), [field]: value } };
      assert.equal(validTuning(draft), true, field + ":" + value);
    }
    for (const value of [Number.NaN, Number.POSITIVE_INFINITY, TUNING_BOUNDS[field][0] - .001, TUNING_BOUNDS[field][1] + .001]) {
      draft.perception.semantic_models = { [seg]: { ...semanticTuning({}, seg), [field]: value } };
      assert.equal(validTuning(draft), false, field + ":" + value); assert.equal(tuningWrite(draft, p, "one"), null);
    }
  }
});
test("depth keeps established tuning without negotiating semantic support and rejects invalid geometry", () => {
  const p: JsonRecord = { revision: 1, perception: { engine: "depth_plane", residual_limit: .06, outlier_limit: .005, slope_min: 2 } };
  assert.equal(tuningModelId(p), "depth-anything-v2-indoor-small");
  const draft = tuningDraft(p, "one")!; assert.ok(tuningWrite(draft, p, "one"));
  draft.perception.residual_limit = .5; assert.equal(tuningWrite(draft, p, "one"), null);
});
test("effective values are read only for the active model and matching producer contract", () => {
  const p = policy(), effective = { pixel_confidence: .83, surface: "floor_rug", floor_labels: [3, 28] };
  p.perception_tuning = { version: 1, active_model_id: seg, effective };
  assert.deepEqual(effectiveTuning(p), effective);
  p.perception_tuning = { version: 1, active_model_id: yolo, effective };
  assert.equal(effectiveTuning(p), null);
});

test("explicit legacy SegFormer resolves the pinned semantic model, effective settings and model-specific floor/rug CAS", () => {
  const p = policy();
  p.perception = { ...p.perception as JsonRecord, engine: "segformer", model_id: "" };
  p.perception_tuning = { version: 1, active_model_id: seg, effective: { surface: "floor_only", pixel_confidence: .8 } };
  assert.equal(tuningModelId(p), seg); assert.equal(effectiveTuning(p)?.pixel_confidence, .8);
  const draft = tuningDraft(p, "one")!;
  draft.perception.semantic_models = { ...draft.perception.semantic_models as JsonRecord,
    [seg]: { ...semanticTuning(draft.perception, seg), surface: "floor_rug" } };
  const write = tuningWrite(draft, p, "one")!;
  assert.equal(write.revision, 7);
  const perception = (write.values as { perception: JsonRecord }).perception;
  assert.equal(perception.model_id, "");
  assert.equal((perception.semantic_models as Record<string, { surface: string }>)[seg].surface, "floor_rug");
  assert.equal(tuningWrite(draft, { ...p, revision: 8 }, "one"), null);
  for (const value of [null, { perception: {} }, { perception: { engine: "unknown", model_id: "" } }]) assert.equal(tuningModelId(value), "");
  p.perception_tuning = { version: 1, active_model_id: yolo, effective: { pixel_confidence: .9 } };
  assert.equal(effectiveTuning(p), null);
});
