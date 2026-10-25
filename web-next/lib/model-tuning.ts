import {inputCovers} from "./input-region";
import { record, str, type JsonRecord } from "./model";

export const SEMANTIC_MODEL_IDS = ["segformer-b0-ade20k", "yolo26n-sem-ade20k"] as const;
export const TUNING_FIELDS = ["pixel_confidence", "min_floor_fraction", "max_uncertain_fraction", "min_region_fraction"] as const;
export type TuningField = typeof TUNING_FIELDS[number];
export type SemanticTuning = { version: 1; surface: "floor_only" | "floor_rug" } & Record<TuningField, number | null>;
export type ModelTuningDraft = { revision: number; session: string; modelId: string; perception: JsonRecord };
export const TUNING_BOUNDS: Record<TuningField, readonly [number, number]> = {
  pixel_confidence: [.05, 1], min_floor_fraction: [.5, 1], max_uncertain_fraction: [0, .5], min_region_fraction: [0, .02],
};
export const semanticModel = (id: string) => (SEMANTIC_MODEL_IDS as readonly string[]).includes(id);
export function tuningModelId(policy: JsonRecord | null): string {
  const perception = record(policy?.perception);
  return str(perception.model_id) || (perception.engine === "depth_plane" ? "depth-anything-v2-indoor-small"
    : perception.engine === "segformer" ? "segformer-b0-ade20k" : "");
}
export function semanticTuning(perception: JsonRecord, id: string): SemanticTuning {
  const row = record(record(perception.semantic_models)[id]);
  return Object.fromEntries([["version", 1], ["surface", row.surface === "floor_rug" ? "floor_rug" : "floor_only"],
    ...TUNING_FIELDS.map(field => [field, typeof row[field] === "number" ? row[field] : null])]) as SemanticTuning;
}
export function tuningDraft(policy: JsonRecord | null, session: string): ModelTuningDraft | null {
  if (!policy || !Number.isSafeInteger(policy.revision) || !tuningModelId(policy)) return null;
  return { revision: Number(policy.revision), session, modelId: tuningModelId(policy), perception: structuredClone(record(policy.perception)) };
}
export function tuningDraftStale(draft: ModelTuningDraft, policy: JsonRecord | null, session: string): boolean {
  return !policy || draft.session !== session || draft.revision !== policy.revision || draft.modelId !== tuningModelId(policy);
}
export function validTuning(draft: ModelTuningDraft): boolean {
  if (semanticModel(draft.modelId)) {
    const row = semanticTuning(draft.perception, draft.modelId);
    return TUNING_FIELDS.every(field => row[field] === null || Number.isFinite(row[field])
      && row[field]! >= TUNING_BOUNDS[field][0] && row[field]! <= TUNING_BOUNDS[field][1]);
  }
  return draft.perception.engine === "depth_plane" && [
    ["residual_limit", .01, .15], ["outlier_limit", 0, .02], ["slope_min", 1, 4],
  ].every(([field, low, high]) => typeof draft.perception[field as string] === "number"
    && Number.isFinite(draft.perception[field as string]) && Number(draft.perception[field as string]) >= Number(low)
    && Number(draft.perception[field as string]) <= Number(high));
}
/** Build a revision-checked write only for supported and valid operator settings. */
export function tuningWrite(draft: ModelTuningDraft, policy: JsonRecord | null, session: string): JsonRecord | null {
  if (tuningDraftStale(draft, policy, session) || !validTuning(draft)
    || draft.perception.input_region !== undefined && (!inputCovers(draft.perception.input_region,policy?.corridor) || policy?.input_region_supported !== true)
    || semanticModel(draft.modelId) && policy?.model_tuning_supported !== true) return null;
  return { revision: draft.revision, values: { perception: draft.perception } };
}
/** Effective values are supplied by the server; the client does not reconstruct profile defaults. */
export function effectiveTuning(policy: JsonRecord | null): JsonRecord | null {
  const row = record(policy?.perception_tuning);
  return row.version === 1 && row.active_model_id === tuningModelId(policy) ? record(row.effective) : null;
}
