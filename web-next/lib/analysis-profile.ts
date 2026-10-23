import type { JsonRecord } from "./model";

export type AnalysisProfile = { id: string; installed: boolean; windows: number[]; task?: string; backend?: string; error?: string };
export function semanticProfile(profile: AnalysisProfile | undefined): boolean {
  return profile?.task === "semantic" || profile?.backend === "semantic";
}
/** Offline run parameters never alter the live Pi model selection. */
export function analysisProfileSettings(
  profile: AnalysisProfile | undefined,
  windowSize: number,
  geometry: JsonRecord,
  perception: JsonRecord,
): { window: number; geometry: JsonRecord; perception: JsonRecord } {
  return semanticProfile(profile)
    ? { window: 1, geometry: { mode: "image", fx: null, fy: null, radius_ratio: null, lookahead_ratio: 2 }, perception: { engine: "segformer" } }
    : { window: windowSize, geometry: { ...geometry }, perception: { ...perception, engine: "depth_plane" } };
}
