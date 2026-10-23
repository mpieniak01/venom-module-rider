import assert from "node:assert/strict";
import { test } from "node:test";
import { perceptionInstallAction, perceptionApplyNeeded, perceptionCatalog, perceptionError, perceptionFrameCurrent, perceptionMetric, perceptionPolicy, perceptionReason, perceptionResultMatches, perceptionRuntime, perceptionSelectionKey, perceptionSwitchBlock, selectionFrom } from "../../web-next/lib/perception";
import type { JsonRecord, RiderStatus } from "../../web-next/lib/model";

const hash = "a".repeat(64), model = "segformer-b0-ade20k";
const selection = { model_id: model, artifact_sha256: hash, revision: 31 };
const artifact = {
  id: model, label: "SegFormer-B0", engine: "segformer", task: "semantic_segmentation", revision: "pinned",
  installed: true, installation: "ready", loaded: true, selectable: true, reason: null,
  drive_supported: false, provider: "venom", execution_provider: "CPUExecutionProvider", sha256: hash, job: null,
};
const local = { ...artifact, id: "local-ssd", engine: "ssd", label: "Pi SSD", provider: "tflite", loaded: false };
const catalogRow = {
  models: [artifact], selection, switching: false,
  provider: "venom", pipeline_revision: 8, local_engine: "ssd",
  providers: [{ id: "venom", available: true }, { id: "local", available: true }],
  local_models: [local], diagnostics: { runtime: { state: "active", active_model_id: model } },
};
test("host catalog supplies models and readiness without constructing client-side model lists", () => {
  const result = perceptionCatalog(catalogRow);
  assert.equal(result.models[0].id, model);
  assert.equal(result.models[0].selectable, true);
  assert.equal(result.models[0].provider, "venom");
  assert.equal(result.models[0].execution_provider, "CPUExecutionProvider");
  assert.deepEqual(result.selection, selection);
  assert.equal(result.models[0].reason, "");
  assert.equal(result.models[0].drive_supported, false);
  assert.deepEqual(result.providers, [{ id: "venom", available: true }, { id: "local", available: true }]);
  assert.equal(result.local_models[0].engine, "ssd");
  assert.equal(result.provider, "venom");
  assert.equal(result.pipeline_revision, 8);
  assert.equal(result.local_engine, "ssd");
  assert.throws(() => perceptionCatalog({}), /perception_catalog_invalid/);
  assert.throws(() => perceptionCatalog({ ...catalogRow, models: [artifact, artifact] }), /perception_catalog_invalid/);
  assert.throws(() => perceptionCatalog({ ...catalogRow, models: [{ ...artifact, installation: "maybe" }] }), /perception_catalog_invalid/);
  assert.throws(() => perceptionCatalog({ ...catalogRow, local_models: [{ ...local, installation: "maybe" }] }), /perception_catalog_invalid/);
});
test("model statuses stay independent: installed and selected do not manufacture loaded or active", () => {
  const ready = perceptionCatalog({ ...catalogRow, models: [{ ...artifact, loaded: false }], diagnostics: {} });
  assert.equal(ready.models[0].installed, true);
  assert.equal(ready.models[0].loaded, false);
  assert.equal(perceptionRuntime(ready), "unavailable");
  assert.equal(perceptionRuntime(perceptionCatalog({ ...catalogRow, switching: true })), "loading");
  for (const state of ["unloaded", "loading", "warming", "active", "unavailable"])
    assert.equal(perceptionRuntime(perceptionCatalog({ ...catalogRow, diagnostics: { runtime: { state } } })), state);
  assert.equal(perceptionRuntime(null), "unavailable");
});
test("explicit model results match the applied policy and artifact, never a draft or approval hash", () => {
  const result = { ...selection, config_revision: 31 };
  assert.equal(perceptionResultMatches(result, selection), true);
  assert.equal(perceptionResultMatches(undefined, selection), false);
  for (const patch of [{ model_id: "other" }, { artifact_sha256: "b".repeat(64) }, { config_revision: 30 }, { artifact_sha256: undefined, model_sha256: hash }])
    assert.equal(perceptionResultMatches({ ...result, ...patch }, selection), false);
  assert.equal(perceptionResultMatches(result, { ...selection, artifact_sha256: "" }), false);
  assert.equal(perceptionResultMatches(result, null), false, "a switch blocks processed results until policy readback");
  assert.equal(perceptionResultMatches(result, undefined), true, "old callers retain legacy preview behavior");
  assert.equal(perceptionResultMatches(undefined, { ...selection, model_id: "" }), true);
});
test("model freshness uses original monotonic capture age and does not re-label stale imagery", () => {
  const frame = { analysis: { ...selection, config_revision: 31 }, stale: false, knownFreshness: true, decoded: true, ageMs: 50, receivedAt: 1000 };
  assert.equal(perceptionFrameCurrent(frame, selection, 1100), true);
  for (const patch of [{ stale: true }, { knownFreshness: false }, { decoded: false }, { ageMs: undefined }, { ageMs: 901 }, { analysis: { model_id: "old", artifact_sha256: hash, config_revision: 30 } }])
    assert.equal(perceptionFrameCurrent({ ...frame, ...patch }, selection, 1100), false);
  assert.equal(perceptionFrameCurrent(frame, selection, 1950), false);
  assert.equal(perceptionFrameCurrent(null, selection, 1100), false);
});
test("legacy policies map without inventing model fingerprints; an explicit policy stays explicit", () => {
  assert.deepEqual(perceptionPolicy({ revision: 2, perception: { engine: "depth_plane" }, model_sha256: hash }), { model_id: "", artifact_sha256: "", revision: 2 });
  assert.deepEqual(perceptionPolicy({ revision: 31, perception: selection }), selection);
  assert.equal(perceptionPolicy(null), null);
  assert.equal(perceptionPolicy({ revision: "2" }), null);
  assert.equal(selectionFrom({ model_id: model, revision: -1 }), null);
  assert.equal(selectionFrom({ model_id: model, revision: 2.5 }), null);
});
const idle = {
  connection: { configured: true, status: "online" }, errors: {},
  control: { safety: { state: "DISARMED", stale: false, can_move: false, moving: false, owner: null } },
} as unknown as RiderStatus;
test("UI blocks switching for authority, requested drive, unknown control and concurrent operations", () => {
  const policy: JsonRecord = { revision: 31, mode: "observe" };
  assert.equal(perceptionSwitchBlock(idle, true, {}, policy, false), "");
  for (const safety of [{ state: "ARMED", moving: false }, { can_move: true }, { owner: "manual" }, { owner: "navigation" }, { moving: true }]) {
    const status = { ...idle, control: { safety: { ...idle.control!.safety as object, ...safety } } };
    assert.equal(perceptionSwitchBlock(status, true, {}, policy, false), "perception.block.motion");
  }
  assert.equal(perceptionSwitchBlock(idle, false, {}, policy, false), "perception.block.stale");
  assert.equal(perceptionSwitchBlock({ ...idle, errors: { control: "unknown" } }, true, {}, policy, false), "perception.block.stale");
  assert.equal(perceptionSwitchBlock(null, true, {}, policy, false), "perception.block.offline");
  assert.equal(perceptionSwitchBlock(idle, true, { drive: true }, policy, false), "perception.block.motion");
  assert.equal(perceptionSwitchBlock(idle, true, {}, { ...policy, mode: "drive" }, false), "", "inactive saved mode alone grants no authority and does not block a reset");
  assert.equal(perceptionSwitchBlock(idle, true, {}, policy, true), "perception.block.busy");
  assert.equal(perceptionSwitchBlock(idle, true, {}, null, false), "perception.block.policy");
});
test("provider, model and revisions participate in conflict detection; runtime measurements do not", () => {
  const base = perceptionCatalog(catalogRow), key = perceptionSelectionKey(base);
  for (const patch of [{ provider: "local" }, { pipeline_revision: 9 }, { local_engine: "canny" }, { selection: { ...selection, revision: 32 } }, { selection: { ...selection, artifact_sha256: "b".repeat(64) } }])
    assert.notEqual(perceptionSelectionKey(perceptionCatalog({ ...catalogRow, ...patch })), key);
  assert.equal(perceptionSelectionKey(perceptionCatalog({ ...catalogRow, diagnostics: { inference_ms: 50 } })), key);
});
test("diagnostics preserve measured zero and report missing memory and FPS as N/A", () => {
  assert.equal(perceptionMetric(0, "pl"), "0");
  assert.equal(perceptionMetric(1.25, "pl"), "1,3");
  for (const missing of [null, undefined, NaN, "0", false, Infinity]) assert.equal(perceptionMetric(missing, "en"), "N/A");
  assert.equal(perceptionReason("model_needs_review"), "perception.reason.review");
  assert.equal(perceptionReason("camera_pipeline_conflict"), "perception.reason.conflict");
  assert.equal(perceptionReason("unexpected"), "perception.reason.failed");
  assert.equal(perceptionError(new Error("model_selection_conflict")), "model_selection_conflict");
  assert.equal(perceptionError(new Error("raw secret text")), "request_failed");
  assert.equal(perceptionError({ data: { detail: { code: "model_hash_mismatch" } } }), "model_hash_mismatch");
});

test("legacy and inactive host models can be explicitly applied without inventing a different model", () => {
  const current = perceptionCatalog(catalogRow);
  assert.equal(perceptionApplyNeeded(current, "venom", model, model), false);
  assert.equal(perceptionApplyNeeded(current, "local", "ssd", model), true);
  assert.equal(perceptionApplyNeeded(perceptionCatalog({ ...catalogRow, selection: { ...selection, artifact_sha256: "" } }), "venom", model, model), true);
  assert.equal(perceptionApplyNeeded(perceptionCatalog({ ...catalogRow, diagnostics: {} }), "venom", model, model), true);
});

test("an offline Pi does not masquerade as the Venom provider or hide the host model catalog", () => {
  const row = perceptionCatalog({ ...catalogRow, provider: null, selection: null, pipeline_revision: null, local_models: [], selection_error: "pi_unreachable" });
  assert.equal(row.provider, "unknown");
  assert.equal(row.selection, null);
  assert.equal(row.pipeline_revision, null);
  assert.equal(row.selection_error, "pi_unreachable");
  assert.equal(row.models[0].id, model);
  assert.equal(row.models[0].installed, true);
});


test("existing files waiting for qualification offer verification, while real jobs offer cancellation", () => {
  const parsed = perceptionCatalog(catalogRow).models[0];
  assert.equal(perceptionInstallAction(parsed), null);
  assert.equal(perceptionInstallAction({ ...parsed, installation: "verifying", installed: true, selectable: false, job: {} }), "start");
  assert.equal(perceptionInstallAction({ ...parsed, installation: "not_installed", installed: false, selectable: false }), "start");
  assert.equal(perceptionInstallAction({ ...parsed, installation: "needs_review", installed: false }), null);
  for (const state of ["waiting", "downloading", "verifying", "cancelling"])
    assert.equal(perceptionInstallAction({ ...parsed, job: { id: "actual-job", state } }), "cancel");
  assert.equal(perceptionInstallAction({ ...parsed, installation: "failed", job: { id: "old", state: "failed" } }), "start");
});
