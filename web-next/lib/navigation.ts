import { readHoldTiming } from "./hold-controller";
import { controlIdentity, data, record, safetyOf, settingsOf, str, timestampMs, type RiderStatus } from "./model";

export type ControlClock = { offset: number; rtt: number; at: number };
export function operatorPrioritySupported(status: RiderStatus | null): boolean {
  return record(safetyOf(status).operator_control).version === 1;
}
export function operatorSessionActive(status: RiderStatus | null): boolean {
  return operatorPrioritySupported(status) && ["paused", "qualifying", "active"].includes(str(record(safetyOf(status).operator_control).state));
}
/** Buttons accept a new operator intent; Pi still authorizes every command. */
export function navigationAvailable(status: RiderStatus | null, panelId?: string): boolean {
  const safety = safetyOf(status);
  const panel = record(safety.panel_control);
  const priority = safety.owner === "navigation" && operatorPrioritySupported(status);
  if (panelId && panel.version === 1 && panel.holder !== panelId) return false;
  if (priority && (!["online", "degraded"].includes(status?.connection.status ?? "") || !panelId || panel.version !== 1 || panel.holder !== panelId || status?.errors.control || safety.stale === true)) return false;
  return !!status?.connection.configured && status.connection.status !== "unconfigured" && safety.state === "ARMED" && (safety.owner === "manual" || priority) && safety.panel_motion_enabled === true && record(record(safety.capabilities).move).available === true && !!str(safety.generation) && Number.isInteger(settingsOf(status).revision);
}
export function freshControl(status: RiderStatus | null, clock: ControlClock | null, now: number, cadence: number): boolean {
  const timing = readHoldTiming(settingsOf(status).control_modes);
  const safety = safetyOf(status);
  const observed = timestampMs(safety.observed_at);
  const freshness = timing ? Math.max(timing.hold_timeout_ms * 2, cadence + timing.hold_timeout_ms) : 0;
  // Pi owns heartbeat freshness; the browser bounds receipt age and clock latency.
  return !!clock && !!timing && !status?.errors.control && observed !== null && safety.stale !== true && clock.rtt < timing.request_ttl_ms && now - clock.at < freshness;
}
/** Missing diagnostics must not erase confirmed ARM or roll back a newer read. */
export function retainControl(previous: RiderStatus | null, next: RiderStatus): RiderStatus {
  if (!previous || previous.connection.session !== next.connection.session || previous.connection.device_id !== next.connection.device_id || !next.connection.configured) return next;
  const oldTime = timestampMs(data(previous.control).server_time);
  const newTime = timestampMs(data(next.control).server_time);
  const older = oldTime !== null && newTime !== null && newTime < oldTime;
  const missingPermission = !["ARMED", "DISARMED", "ESTOP"].includes(str(safetyOf(next).state));
  const control = next.control === null || missingPermission || older ? previous.control : next.control;
  const settings = next.settings === null || older ? previous.settings : next.settings;
  const errors = missingPermission ? { ...next.errors, control: "executor_observation_unavailable" } : next.errors;
  return { ...next, control, settings, errors };
}
export function observationClock(previous: ControlClock | null, next: RiderStatus, started: number, received: number): ControlClock | null {
  const server = timestampMs(data(next.control).server_time);
  const timing = readHoldTiming(settingsOf(next).control_modes);
  if (server === null || !timing || received - started >= timing.request_ttl_ms) return previous;
  return { offset: server - (started + received) / 2, rtt: received - started, at: received };
}
export function controlChanged(previous: RiderStatus | null, next: RiderStatus): boolean {
  return !!previous && controlIdentity(previous) !== controlIdentity(next);
}

export function navigationView(previous: RiderStatus, next: RiderStatus): RiderStatus {
  const errors = { ...previous.errors, ...next.errors };
  for (const key of ["control", "settings"]) if (!next.errors[key]) delete errors[key];
  return { ...previous, connection: next.connection, control: next.control, settings: next.settings, errors };
}

export function continuousAvailable(status: RiderStatus | null, panelId: string): boolean {
  return !status?.errors.control && safetyOf(status).stale !== true && navigationAvailable(status, panelId) && ["online", "degraded"].includes(status?.connection.status ?? "") && record(safetyOf(status).panel_control).version === 1;
}
