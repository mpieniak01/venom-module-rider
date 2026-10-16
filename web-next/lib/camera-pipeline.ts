import { record, type JsonRecord } from "./model";

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
function profile(value: unknown): CameraProfile | null {
  const p = record(value);
  if (!["local", "venom"].includes(String(p.analysis_location))
    || !["canny", "ssd", "tflite"].includes(String(p.local_engine))
    || !["snapshots", "mjpeg"].includes(String(p.remote_transport))) return null;
  return p as CameraProfile;
}
export function readCameraPipeline(value: unknown): CameraPipeline | null {
  const p = record(value), caps = record(p.capabilities), selected = profile(p.profile);
  if (p.supported === false) return null;
  if (p.contract_id !== "rider.camera_pipeline" || p.contract_version !== 1
    || !Number.isInteger(p.revision) || Number(p.revision) < 1 || !selected
    || !["stopped", "stopping", "starting", "waiting_frame", "active", "failed"].includes(String(p.state))
    || !Number.isInteger(p.transport_epoch) || Number(p.transport_epoch) < 0
    || p.active_profile !== null && !profile(p.active_profile)
    || !["analysis_locations", "local_engines", "remote_transports"].every(key =>
      Array.isArray(caps[key]) && (caps[key] as unknown[]).every(item => typeof item === "string"))) return null;
  return { ...p, supported: true, profile: selected, capabilities: caps } as CameraPipeline;
}
export function pipelinePending(p: CameraPipeline | null): boolean {
  return !!p && ["starting", "stopping", "waiting_frame"].includes(p.state);
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
