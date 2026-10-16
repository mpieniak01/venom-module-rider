import { record, type JsonRecord } from "./model";
import { previewLifetime } from "./preview-media";
import type { FrameTiming } from "./vision-frame";

export type SectorFrame = FrameTiming & { id?: string; analysis?: JsonRecord; stale?: boolean; decoded?: boolean; knownFreshness?: boolean };
export type SectorSide = "left" | "center" | "right";
export type SectorState = "clear" | "obstacle" | "unknown";

/** Read source-space sectors from the decoded JPEG, never from a newer loop poll. */
export function previewSectors(frame: SectorFrame | null, now = performance.now()) {
  const analysis = record(frame?.analysis);
  const space = analysis.sector_space === "image" || analysis.sector_space === "projected_paths" ? analysis.sector_space : "unavailable";
  const source = record(analysis.source);
  const identity = typeof source.frame_id === "string" ? source.frame_id : analysis.frame_id;
  const current = !!frame?.id && frame.id === identity && frame.decoded === true && frame.knownFreshness === true
    && frame.stale !== true && (previewLifetime(frame, now) ?? 0) > 0 && space !== "unavailable";
  const sectors = record(analysis.sectors);
  return { current, space, rows: (["left", "center", "right"] as const).map(side => {
    const row = record(sectors[side]);
    const complete = current && row.coverage_complete === true;
    const state: SectorState = complete && ["clear", "obstacle"].includes(String(row.state)) ? row.state as "clear" | "obstacle" : "unknown";
    return { side, code: side === "left" ? "L" : side === "center" ? "C" : "R", state,
      incomplete: current && row.coverage_complete === false,
      marked: space !== "unavailable" && Object.keys(row).length > 0 };
  }) };
}

export const SECTOR_PRESENTATION_INTERVAL_MS = 1000;
export type SectorPresentation = {
  view: ReturnType<typeof previewSectors>;
  context: string;
  frameId: string | undefined;
};

/** Sample readable text only; no sampled result is used for motion or freshness. */
export function sectorPresentation(
  previous: SectorPresentation | null, frame: SectorFrame | null,
  refresh = false, now = performance.now(),
): SectorPresentation {
  const view = previewSectors(frame, now);
  const analysis = record(frame?.analysis), source = record(analysis.source);
  // Standalone snapshot analysis assigns a new run_id for each JPEG.
  const context = JSON.stringify([analysis.config_revision, analysis.model_sha256,
    view.space, source.camera_session, source.pipeline_revision, source.transport_epoch]);
  // Invalid input or a changed source/policy must never retain a prior clear label.
  if (previous && previous.context === context && previous.view.current === view.current && (!refresh || !view.current)) return previous;
  return { view, context, frameId: frame?.id };
}
