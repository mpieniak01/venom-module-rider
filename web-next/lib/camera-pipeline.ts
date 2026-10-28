import { record, str, type JsonRecord } from "./model";

export type CameraProfile = {
  analysis_location: "local" | "venom";
  local_engine: "canny" | "ssd" | "tflite";
  remote_transport: "snapshots" | "mjpeg";
};
export type CameraPipeline = {
  supported: boolean;
  revision: number;
  profile: CameraProfile;
  active_profile: CameraProfile | null;
  state: string;
  transport_epoch: number;
  camera_session: string | null;
  capabilities: {
    analysis_locations: string[]; local_engines: string[]; remote_transports: string[];
    stream_drive_supported: boolean; local_drive_supported: boolean;
  };
  effective_stages: Record<string, boolean>;
  error: string | null;
  previewAnchor?: { latest: JsonRecord; receivedAt: number; roundTripMs: number };
};
/** Validate literal profile enums without coercing arbitrary JSON values. */
function profile(value: unknown): CameraProfile | null {
  const p = record(value);
  if (!["local", "venom"].includes(str(p.analysis_location))
    || !["canny", "ssd", "tflite"].includes(str(p.local_engine))
    || !["snapshots", "mjpeg"].includes(str(p.remote_transport))) return null;
  return p as CameraProfile;
}
/** Read a supported envelope and reject malformed enum or state fields. */
export function readCameraPipeline(value: unknown): CameraPipeline | null {
  const p = record(value), caps = record(p.capabilities), selected = profile(p.profile);
  if (p.supported === false) return null;
  if (p.contract_id !== "rider.camera_pipeline" || p.contract_version !== 1
    || !Number.isInteger(p.revision) || Number(p.revision) < 1 || !selected
    || !["stopped", "stopping", "starting", "waiting_frame", "active", "failed"].includes(str(p.state))
    || !Number.isInteger(p.transport_epoch) || Number(p.transport_epoch) < 0
    || p.active_profile !== null && !profile(p.active_profile)
    || !["analysis_locations", "local_engines", "remote_transports"].every(key =>
      Array.isArray(caps[key]) && (caps[key] as unknown[]).every(item => typeof item === "string"))) return null;
  return { ...p, supported: true, profile: selected, capabilities: caps } as CameraPipeline;
}
export function pipelinePending(p: CameraPipeline | null): boolean {
  return !!p && ["starting", "stopping", "waiting_frame"].includes(p.state);
}
/** Confirmed OFF suspends media requests, while status polling continues. */
export function cameraPreviewStopped(p: CameraPipeline | null): boolean {
  return !!p && ["stopped", "failed"].includes(p.state);
}
export function nativePreview(p: CameraPipeline | null, view: string | null): boolean {
  // A saved inactive transport choice must not start a second path in local mode.
  return !!p && p.profile.analysis_location === "venom" && p.profile.remote_transport === "mjpeg"
    && (view === "raw" || view === "venom");
}
export function cameraProfileEqual(a: CameraProfile, b: CameraProfile): boolean {
  return a.analysis_location === b.analysis_location && a.local_engine === b.local_engine
    && a.remote_transport === b.remote_transport;
}

/** Compare captures on Pi's clock and elapsed time on this browser's clock only. */
export function anchoredPreviewAge(source: JsonRecord, ageMs: number, receivedAt: number, anchor?: CameraPipeline["previewAnchor"]): number {
  const latest = anchor?.latest;
  if (!latest || latest.camera_session !== source.camera_session || latest.transport_epoch !== source.transport_epoch
    || latest.pipeline_revision !== source.pipeline_revision || !Number.isFinite(latest.captured_monotonic_ns)
    || !Number.isFinite(source.captured_monotonic_ns) || !Number.isFinite(latest.frame_age_ms)) return ageMs;
  return Math.max(ageMs, Number(latest.frame_age_ms) + anchor!.roundTripMs + Math.max(0, receivedAt - anchor!.receivedAt)
    - (Number(source.captured_monotonic_ns) - Number(latest.captured_monotonic_ns)) / 1e6);
}

/** Run sequence and camera sequence are distinct; bind the exact camera reference. */
export function nativeAnalysisMatches(analysis: JsonRecord | undefined, source: JsonRecord): boolean {
  if (!analysis) return false;
  const reference = record(analysis.source);
  return ["camera_session", "sequence", "transport_epoch", "pipeline_revision", "frame_id", "sha256"].every(key =>
    reference[key] !== undefined && reference[key] === source[key]);
}

/** Keep a tighter recent upper age bound, never renew the source or cross sessions. */
export function preferredPreviewAnchor(previous:CameraPipeline["previewAnchor"],candidate:NonNullable<CameraPipeline["previewAnchor"]>):NonNullable<CameraPipeline["previewAnchor"]> {
  const elapsed=previous?candidate.receivedAt-previous.receivedAt:Infinity;
  if(previous&&elapsed>=0&&elapsed<=2000
    &&["camera_session","transport_epoch","pipeline_revision"].every(key=>previous.latest[key]!==undefined&&previous.latest[key]===candidate.latest[key])
    &&Number.isFinite(candidate.latest.frame_age_ms)&&Number.isFinite(candidate.latest.captured_monotonic_ns)){
    const priorAge=anchoredPreviewAge(candidate.latest,0,candidate.receivedAt,previous);
    const candidateAge=Number(candidate.latest.frame_age_ms)+candidate.roundTripMs;
    if(priorAge>=Number(candidate.latest.frame_age_ms)&&priorAge<=candidateAge)return previous;
  }
  return candidate;
}

export function cameraPipelineIdentity(pipeline: CameraPipeline | null | undefined): string | undefined {
  return pipeline ? [pipeline.revision, pipeline.transport_epoch, pipeline.camera_session].join(":") : undefined;
}

export function framePipelineCurrent(frame: { pipelineIdentity?: string; cameraSource?: JsonRecord } | null, pipeline: CameraPipeline | null | undefined): boolean {
  return !!frame && (!frame.pipelineIdentity || frame.pipelineIdentity === cameraPipelineIdentity(pipeline))
    && (!frame.cameraSource || cameraSourceMatches(frame.cameraSource, pipeline));
}


export function readCameraSource(header: string | null): JsonRecord | undefined {
  if (!header || header.length > 4096) return undefined;
  try {
    const source = record(JSON.parse(header));
    return typeof source.camera_session === "string" && Number.isSafeInteger(source.pipeline_revision)
      && Number.isSafeInteger(source.transport_epoch) ? source : undefined;
  } catch { return undefined; }
}
/** Model metadata is produced alongside PROC; requested local profiles cannot re-label it. */
export function cameraSourceMatches(source: JsonRecord, pipeline: CameraPipeline | null | undefined): boolean {
  if (!pipeline || source.pipeline_revision !== pipeline.revision || source.transport_epoch !== pipeline.transport_epoch
    || source.camera_session !== pipeline.camera_session) return false;
  if (source.local_engine !== undefined || source.model_id !== undefined) {
    const active = pipeline.active_profile;
    return !!active && active.analysis_location === "local"
      && (source.local_engine === undefined || source.local_engine === active.local_engine)
      && (source.model_id === undefined || source.model_id === "local-" + active.local_engine)
      && (source.analysis_location === undefined || source.analysis_location === "local");
  }
  return true;
}
