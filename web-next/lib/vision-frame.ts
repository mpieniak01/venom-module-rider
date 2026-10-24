import { record, type JsonRecord } from "./model";

export type FrameTiming = { ageMs?: number; receivedAt?: number; ageSource?: string };
export function frameAge(frame: FrameTiming | null, now = performance.now()): number | null {
  if (!frame || !Number.isFinite(frame.ageMs) || !Number.isFinite(frame.receivedAt)) return null;
  return Math.max(0, frame.ageMs! + Math.max(0, now - frame.receivedAt!));
}
export const PREVIEW_ANALYSIS_MAX_CHARS = 4096;
export function readAnalysis(header: string | null): JsonRecord | undefined {
  if (!header || header.length > PREVIEW_ANALYSIS_MAX_CHARS) return undefined;
  try {
    const row = record(JSON.parse(header));
    if (typeof row.run_id !== "string" || typeof row.frame_id !== "string"
      || !Number.isInteger(row.sequence) || !Number.isInteger(row.config_revision)
      || !["clear", "obstacle", "unknown"].includes(String(row.state))) return undefined;
    return row;
  } catch { return undefined; }
}
