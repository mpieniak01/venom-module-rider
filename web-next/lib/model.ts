export type JsonRecord = Record<string, unknown>;
export type Section = "control" | "overview" | "calibration" | "resources" | "face" | "compute";
export type Direction = "forward" | "backward" | "left" | "right";
export const DIRECTIONS: Direction[] = ["forward", "backward", "left", "right"];
export type DeviceConfig = { devices: { id: string; label: string; mode: "demo" | "device" }[]; device_id: string | null; session: string };
export type RiderStatus = {
  connection: { status: "unconfigured" | "offline" | "online" | "degraded"; configured: boolean; device_id: string | null; label?: string; session: string; mode: "demo" | "device"; error?: string | null; last_seen?: number | null };
  polling: Record<string, number>;
  telemetry: JsonRecord | null;
  health: JsonRecord | null;
  system: JsonRecord | null;
  control: JsonRecord | null;
  settings: JsonRecord | null;
  resources: Record<string, JsonRecord>;
  catalog: JsonRecord | null;
  logic: JsonRecord | null;
  history: JsonRecord | null;
  face: { library: JsonRecord | null; runtime: JsonRecord | null };
  errors: Record<string, string>;
  compute?: JsonRecord | null;
  conversation?: JsonRecord | null;
};
export type NumericDescriptor = { type?: string; unit?: string; display_unit?: string; display_factor?: number; minimum?: number; maximum?: number; step?: number; display_precision?: number; meaning?: string; available?: boolean };
export type Gesture = { id?: string; builtin?: boolean; name: string; expr: string; duration: number; contract_version: number; initial: Record<string, number>; tracks: Record<string, Segment[]> };
export type Segment = { t0: number; t1: number; v0: number; v1: number; ease: string };
export const record = (value: unknown): JsonRecord => value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
export const rows = (value: unknown): JsonRecord[] => Array.isArray(value) ? value.map(record) : [];
export const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
export const str = (value: unknown): string => typeof value === "string" ? value : "";
export const data = (value: unknown): JsonRecord => { const row = record(value); return row.meta && row.data ? record(row.data) : row; };
export const safetyOf = (status: RiderStatus | null): JsonRecord => record(data(status?.control).safety);
export const actuatorState = (status: RiderStatus | null): JsonRecord => record(safetyOf(status).actuators);
export const settingsOf = (status: RiderStatus | null): JsonRecord => data(status?.settings);
export const faceRunning = (status: RiderStatus | null): boolean => record(data(status?.face.runtime).state).running === true;
export function canRequestArm(status: RiderStatus | null): boolean {
  const safety = safetyOf(status);
  return ["online", "degraded"].includes(status?.connection.status ?? "") && safety.stale !== true && !!str(safety.generation) && (safety.state === "ESTOP" || safety.ready === true);
}
export function frameRequested(kind: "camera" | "face", path: string, section: Section, media: { camera: "raw" | "proc" | null; face: boolean }): boolean {
  return kind === "camera" ? section === "control" && media.camera !== null && path === `/camera/${media.camera}` : section === "face" && media.face && path === "/face/runtime/frame";
}
export const timestampMs = (value: unknown): number | null => { if (finite(value)) return value < 1e12 ? value * 1000 : value; if (typeof value === "string") { const at = Date.parse(value); return Number.isFinite(at) ? at : null; } return null; };
export const formatNumber = (value: unknown, locale: string, precision = 1): string => finite(value) ? new Intl.NumberFormat(locale, { maximumFractionDigits: precision }).format(value) : "—";
export const formatTime = (value: unknown, locale: string): string => { const at = timestampMs(value); return at === null ? "—" : new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(at); };
export const displayValue = (value: unknown, descriptor: NumericDescriptor, locale: string): string => formatNumber(finite(value) ? value * (descriptor.display_factor ?? 1) : value, locale, descriptor.display_precision ?? 1);
export function errorCode(error: unknown): string {
  const outer = record(error); const response = record(outer.data); const detail = record(response.detail); const nested = record(response.error);
  return str(detail.code) || str(nested.code) || str(response.error) || (outer.name === "AbortError" ? "cancelled" : "request_failed");
}
export function commandAccepted(value: unknown): boolean { const outer = record(value); const body = data(value); return outer.ok !== false && body.ok !== false && !body.error; }
export function featureRows(status: RiderStatus | null): JsonRecord[] {
  const features = data(status?.catalog).features;
  const catalog = Array.isArray(features) ? rows(features) : Object.entries(record(features)).map(([name, row]) => ({ name, ...record(row) }));
  const logicFeatures = data(status?.logic).features;
  const logic = Array.isArray(logicFeatures) ? rows(logicFeatures) : Object.entries(record(logicFeatures)).map(([name, row]) => ({ name, ...record(row) }));
  return catalog.map(item => {
    const state: JsonRecord = logic.find(row => row.name === item.name) ?? {};
    return { ...item, ...state, ...(state.enabled !== undefined ? { requested: state.enabled, active: state.enabled === true && state.state === "running" } : {}) };
  });
}
export function controlIdentity(status: RiderStatus | null): string { const safety = safetyOf(status); return [status?.connection.session, safety.generation, safety.instance_id ?? safety.instance].join(":"); }
export function sensorRows(status: RiderStatus | null): [string, JsonRecord][] {
  const xgo = record(record(data(status?.telemetry).devices).xgo); const observation = record(xgo.observation); const quality = record(observation.sensor_quality ?? xgo.sensor_quality); const sensors = record(quality.sensors ?? quality);
  return Object.entries(sensors).filter(([, sample]) => typeof sample === "object").map(([name, sample]) => [name, record(sample)]);
}

export function cameraOn(status: RiderStatus | null): boolean {
  for (const payload of [data(status?.health).camera, data(status?.telemetry).camera]) {
    const camera = record(payload);
    const activity = record(camera.activity);
    if (camera.offline === true || camera.stale === true) continue;
    const value = activity.on ?? activity.enabled ?? camera.on ?? camera.enabled;
    if (typeof value === "boolean") return value;
    const state = str(activity.state ?? camera.state ?? camera.status).toLowerCase();
    if (["on", "running", "active"].includes(state)) return true;
    if (["off", "stopped", "disabled"].includes(state)) return false;
  }
  return false;
}

export function telemetryAge(status: RiderStatus | null): number | null {
  const telemetry = data(status?.telemetry);
  const xgo = record(record(telemetry.devices).xgo);
  const observation = record(xgo.observation);
  const observed = timestampMs(observation.observed_at ?? xgo.observed_at ?? telemetry.observed_at);
  const clocks = [data(status?.control).server_time, telemetry.server_time].map(timestampMs).filter((time): time is number => time !== null);
  const server = clocks.length ? Math.max(...clocks) : null;
  return observed !== null && server !== null && server >= observed ? (server - observed) / 1000 : null;
}
