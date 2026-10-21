import { readHoldTiming } from "./hold-controller";
import { controlIdentity, data, record, safetyOf, settingsOf, str, timestampMs, type RiderStatus } from "./model";

export type ControlClock = { offset: number; rtt: number; at: number };
export function operatorPrioritySupported(status: RiderStatus | null): boolean {
  return record(safetyOf(status).operator_control).version === 1;
}
/** Versioned explicit gestures can establish new manual authority, never reuse GO. */
export function manualInterventionSupported(status: RiderStatus | null): boolean {
  return safetyOf(status).operator_intervention_version === 1;
}
export function manualInterventionAvailable(status: RiderStatus | null, panelId?: string): boolean {
  const safety = safetyOf(status);
  return manualInterventionSupported(status) && !!panelId && !!status?.connection.configured
    && ["online", "degraded"].includes(status.connection.status) && !status.errors.control && safety.stale !== true
    && ["ARMED", "DISARMED"].includes(str(safety.state)) && safety.ready !== false
    && (safety.state === "DISARMED" || safety.panel_motion_enabled === true) && record(record(safety.capabilities).move).available === true
    && record(safety.panel_control).version === 1 && !!str(safety.generation) && Number.isInteger(settingsOf(status).revision);
}
/** Journey authority is independent from a newly consented manual gesture. */
export function journeyOwnsMotion(status: RiderStatus | null): boolean {
  const safety = safetyOf(status);
  return safety.owner === "navigation" && safety.arming_mode === "journey";
}
export function operatorSessionActive(status: RiderStatus | null): boolean {
  return !journeyOwnsMotion(status) && operatorPrioritySupported(status) && ["paused", "qualifying", "active"].includes(str(record(safetyOf(status).operator_control).state));
}
/** Buttons accept a new operator intent; Pi still authorizes every command. */
export function navigationAvailable(status: RiderStatus | null, panelId?: string): boolean {
  const safety = safetyOf(status);
  const panel = record(safety.panel_control);
  if (manualInterventionSupported(status)) return manualInterventionAvailable(status, panelId);
  if (journeyOwnsMotion(status)) return false;
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
  if ((previous.connection.transport?.generation ?? 0) !== (next.connection.transport?.generation ?? 0)) return next;
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
  if (server === null || !timing || received < started || received - started >= timing.request_ttl_ms) return previous;
  // A combined status response can spend most of its RTT before Pi samples
  // control. The midpoint can put a command in Pi's future. Receipt gives a
  // conservative lower bound; Pi still enforces the original, unchanged TTL.
  return { offset: server - received, rtt: received - started, at: received };
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

/** Stale navigation metadata cannot turn a manual release into an unsupported pause. */
export function manualStopAction(status:RiderStatus|null):"stop"|"navigation_pause"|"release" {
  if(journeyOwnsMotion(status))return "stop";
  return safetyOf(status).owner==="navigation"&&operatorSessionActive(status)?"navigation_pause":"release";
}
