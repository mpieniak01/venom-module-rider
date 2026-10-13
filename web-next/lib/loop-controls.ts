import { record, safetyOf, str, type JsonRecord, type RiderStatus } from "./model";

export type LoopPhase = "offline" | "unknown" | "stopped" | "starting" | "running";
export function loopPhase(connected: boolean, state: JsonRecord, goals: JsonRecord): LoopPhase {
  if (!connected) return "offline";
  if (goals.observe === false && goals.drive === false) return "stopped";
  if (state.stale !== true && state.running === true) return "running";
  if ((goals.observe === true || goals.drive === true) && state.stale !== true) return "starting";
  return "unknown";
}
export function loopBlock(policy: JsonRecord | null, runtime: JsonRecord, detector: JsonRecord, connected: boolean, dirty: boolean, status: RiderStatus | null): string {
  if (!connected) return "loopOffline";
  if (!policy) return "loopLoadFailed";
  if (dirty) return "loopUnsaved";
  const mode = str(policy.mode);
  if (runtime[mode] !== true) return runtime.drive_reason === "navigation_executor_update_required" && mode === "drive" ? "loopExecutorUpdate" : "loopRuntimeMissing";
  if (detector.configured !== true) return "loopNoModel";
  if (mode === "drive") {
    if (policy.verified !== true || policy.model_sha256 !== detector.sha256) return "loopUnverified";
    const safety = safetyOf(status);
    if (safety.state !== "ARMED" || safety.owner !== "navigation" || safety.stale === true || status?.errors?.control) return "loopNeedsArm";
  }
  return "";
}
export function loopFailure(error: unknown): string {
  const outer = record(error), payload = record(outer.data), detail = record(payload.detail);
  const code = str(detail.code) || str(record(payload.error).code) || str(payload.error);
  if (["offline", "pi_unreachable"].includes(code)) return "loopOffline";
  if (code === "navigation_api_unavailable") return "loopApiMissing";
  if (code === "pi_operation_timeout") return "operationUnconfirmed";
  return "requestFailed";
}
