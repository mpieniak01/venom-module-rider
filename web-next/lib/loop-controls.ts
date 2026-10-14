import { record, safetyOf, str, timestampMs, type JsonRecord, type RiderStatus } from "./model";

export type LoopPhase = "offline" | "unknown" | "stopped" | "starting" | "running" | "fault";
export function loopPhase(connected: boolean, state: JsonRecord, goals: JsonRecord): LoopPhase {
  if (!connected) return "offline";
  if (goals.observe === false && goals.drive === false) return "stopped";
  if (state.latched === true && (goals.observe === true || goals.drive === true)) return "fault";
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
    if (!calibrationMatches(policy, detector)) return "loopUnverified";
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

// The saved Pi policy remains authoritative. These helpers only guide the operator.
export function calibrationMatches(policy: JsonRecord | null, detector: JsonRecord): boolean {
  return !!policy && policy.verified === true && !!str(policy.calibration_id).trim()
    && /^[a-f0-9]{64}$/.test(str(detector.sha256)) && policy.model_sha256 === detector.sha256;
}
export function calibrationScope(policy: JsonRecord | null, detector: JsonRecord): string {
  return JSON.stringify([policy?.speed, policy?.max_motion_ms, policy?.decision_budget_ms, policy?.corridor, detector.sha256]);
}
export function loopSettingsValues(policy: JsonRecord, draft: JsonRecord): JsonRecord {
  const {revision: ignoredRevision, protocol_version: ignoredProtocol, ...values} = draft;
  void ignoredRevision; void ignoredProtocol;
  return calibrationScope(policy, {}) === calibrationScope(draft, {}) ? values
    : {...values, verified: false, calibration_id: "", model_sha256: ""};
}
export function calibrationAcceptance(policy: JsonRecord, detector: JsonRecord, id: string): JsonRecord {
  if (!/^[a-f0-9]{64}$/.test(str(detector.sha256)) || !id.trim()) throw new Error("calibration_model_unavailable");
  return {...loopSettingsValues(policy, policy), verified: true, calibration_id: id, model_sha256: detector.sha256};
}
export function freshAnalysisFrame(frame: {decoded?: boolean; stale: boolean; knownFreshness: boolean; fileAt: string; id: string; visionState: string; visionPolicy: string} | null, stream: string | null, now = Date.now()): boolean {
  const at = timestampMs(frame?.fileAt && Number.isFinite(Number(frame.fileAt)) ? Number(frame.fileAt) : frame?.fileAt);
  return !!frame && frame.decoded === true && stream === "venom" && !frame.stale && frame.knownFreshness && !!frame.id
    && !!frame.visionState && !!frame.visionPolicy && frame.visionPolicy !== "diagnostic_default"
    && at !== null && now >= at - 1000 && now - at <= 3000;
}

export function decisionView(state: JsonRecord, detector: JsonRecord) {
  const decision=record(state.decision), required=Number(detector.clear_frames_required)||3;
  const current=state.running===true&&state.stale!==true&&state.latched!==true;
  const count=typeof decision.clear_count==="number"?decision.clear_count:0;
  const vision=decision.vision_action==="go" || decision.state==="clear"&&count>=required ? "go":"stop";
  return {
    current, required, count,
    vision:current?vision:"none",
    reply:current&&["go","stop"].includes(String(decision.action))?String(decision.action):"none",
    reason:current?String(decision.motion_reason??(decision.state!=="clear"?decision.reason:count<required?"clear_frames_pending":state.mode==="observe"?"observe_only":decision.reason)??"waiting_for_decision"):String(state.stop_reason??"waiting_for_decision"),
    floor:typeof decision.floor_fraction==="number"?decision.floor_fraction:null,
    confidence:typeof decision.confidence_fraction==="number"?decision.confidence_fraction:null,
  };
}
export function trialBlock(policy: JsonRecord|null, runtime:JsonRecord, detector:JsonRecord, connected:boolean, dirty:boolean, status:RiderStatus|null):string {
  const base=loopBlock(policy?{...policy,mode:"observe"}:null,runtime,detector,connected,dirty,status);
  if(base)return base;
  if(runtime.drive!==true)return runtime.drive_reason==="navigation_executor_update_required"?"loopExecutorUpdate":"loopRuntimeMissing";
  if(!calibrationMatches(policy,detector))return "loopUnverified";
  const safety=safetyOf(status);
  if(safety.state==="ESTOP")return "trialEstop";
  if(safety.stale===true||status?.errors?.control)return "trialControlStale";
  if(!str(safety.generation)||!["DISARMED","ARMED"].includes(str(safety.state)))return "trialControlStale";
  if(record(safety.capabilities).move&&record(record(safety.capabilities).move).available!==true)return "trialExecutorUnavailable";
  if(safety.remote_decision_deadline_supported!==true)return "loopExecutorUpdate";
  if(record(safety.execution).inhibit)return "trialExecutionInhibited";
  return "";
}
