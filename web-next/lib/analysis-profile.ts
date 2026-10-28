import {inputBounds} from "./input-region";
import { record, str, type JsonRecord } from "./model";
import { behaviorSelection, type BehaviorSelection } from "./behavior";
import { SEMANTIC_MODEL_IDS, TUNING_BOUNDS, TUNING_FIELDS } from "./model-tuning";

export type AnalysisProfile = { id: string; installed: boolean; windows: number[]; task?: string; backend?: string; error?: string };
export const LEGACY_ANALYSIS_BEHAVIOR: BehaviorSelection = { version: 1, profile_id: "legacy", profile_revision: 1 };
export type RiderAnalysisSettings = { revision: number; behavior: BehaviorSelection; corridor: number[]; perception: JsonRecord };
export function semanticProfile(profile: AnalysisProfile | undefined): boolean {
  return profile?.task === "semantic" || profile?.backend === "semantic";
}
/** Copy validated numeric depth controls without sharing mutable input regions. */
function depthSettings(source:JsonRecord):JsonRecord {
  const perception: JsonRecord = {};
  for (const [key, low, high, fallback] of [
    ["residual_limit", .01, .15, .06], ["outlier_limit", 0, .02, .005], ["slope_min", 1, 4, 2],
  ] as const) {
    const number = source[key] ?? fallback;
    if (typeof number !== "number" || !Number.isFinite(number) || number < low || number > high) throw new Error("analysis_rider_settings_invalid");
    perception[key] = number;
  }
  if(source.input_region!==undefined){if(!inputBounds(source.input_region).length)throw new Error("analysis_rider_settings_invalid");perception.input_region=structuredClone(source.input_region);}
  return perception;
}
/** Validate one semantic model's device-defined tuning bounds. */
function semanticSettingsEntry(id:string,raw:unknown):JsonRecord {
  const row=record(raw);
  if(!(SEMANTIC_MODEL_IDS as readonly string[]).includes(id)||row.version!==1||!["floor_only","floor_rug"].includes(str(row.surface)))throw new Error("analysis_rider_settings_invalid");
  const entry:JsonRecord={version:1,surface:row.surface};
  for(const field of TUNING_FIELDS){
    const number=row[field]??null;
    if(number!==null&&(typeof number!=="number"||!Number.isFinite(number)||number<TUNING_BOUNDS[field][0]||number>TUNING_BOUNDS[field][1]))throw new Error("analysis_rider_settings_invalid");
    entry[field]=number;
  }
  return entry;
}
/** Reject malformed model maps before copying each independent model entry. */
function semanticSettings(value:unknown):JsonRecord {
  if(!value||Array.isArray(value)||typeof value!=="object")throw new Error("analysis_rider_settings_invalid");
  const copied:JsonRecord={};
  for(const [id,raw] of Object.entries(value))copied[id]=semanticSettingsEntry(id,raw);
  return copied;
}
/** Copy only experiment settings. Provider selection, artifact identity and motion approval never cross this boundary. */
export function riderAnalysisSettings(value: unknown): RiderAnalysisSettings {
  const policy = record(value), source = record(policy.perception), corridor = policy.corridor;
  const behavior = policy.behavior === undefined ? { ...LEGACY_ANALYSIS_BEHAVIOR } : behaviorSelection(policy.behavior);
  if (!Number.isSafeInteger(policy.revision) || !behavior || !Array.isArray(corridor) || corridor.length !== 4
    || !corridor.every(item => typeof item === "number" && Number.isFinite(item) && item >= 0 && item <= 1)
    || corridor[0] >= corridor[2] || corridor[1] >= corridor[3]) throw new Error("analysis_rider_settings_invalid");
  const perception=depthSettings(source);
  if(source.semantic_models!==undefined)perception.semantic_models=semanticSettings(source.semantic_models);
  return { revision: Number(policy.revision), behavior: { ...behavior }, corridor: [...corridor], perception };
}
/** Offline run parameters never alter live selection, and semantic runs never inherit depth geometry or identity. */
export function analysisProfileSettings(
  profile: AnalysisProfile | undefined,
  windowSize: number,
  geometry: JsonRecord,
  perception: JsonRecord,
): { window: number; geometry: JsonRecord; perception: JsonRecord } {
  const models = perception.semantic_models === undefined ? {} : { semantic_models: structuredClone(record(perception.semantic_models)) };
  const depth = Object.fromEntries(["residual_limit", "outlier_limit", "slope_min"].filter(key => key in perception).map(key => [key, perception[key]]));
  if(semanticProfile(profile)){
  return ({ window: 1, geometry: { mode: "image", fx: null, fy: null, radius_ratio: null, lookahead_ratio: 2 }, perception: { engine: "segformer", ...models } });
}
return ({ window: windowSize, geometry: { ...geometry }, perception: { ...depth, ...models, ...(profile?.id==="da2-default"&&perception.input_region!==undefined?{input_region:structuredClone(perception.input_region)}:{}), engine: "depth_plane" } });

}
