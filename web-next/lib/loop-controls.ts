import { frameAge } from "./vision-frame";
import { navigationAvailable } from "./navigation";
import { record, safetyOf, str, timestampMs, type JsonRecord, type RiderStatus } from "./model";

export type LoopPhase = "offline" | "unknown" | "stopped" | "starting" | "running" | "fault";
export function loopPhase(connected: boolean, state: JsonRecord, goals: JsonRecord): LoopPhase {
  if (!connected) return "offline";
  if (state.latched === true && state.mode === "drive" && state.running === false) return "fault";
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
  if (["navigation_trial_cancelled","navigation_configuration_changed","navigation_authority_unconfirmed","navigation_estop","stale_or_invalid_operator_intent","navigation_trial_prepare_timeout","navigation_trial_authority_changed","navigation_trial_replay","navigation_trial_journal_unavailable","navigation_trial_stopped","navigation_trial_arm_unconfirmed","navigation_trial_authority_unknown"].includes(code)) return "trial.error."+code;
  return "requestFailed";
}

// The saved Pi policy remains authoritative. These helpers only guide the operator.
export function calibrationRecorded(policy: JsonRecord | null): boolean {
  return !!policy && policy.verified === true && !!str(policy.calibration_id).trim() && /^[a-f0-9]{64}$/.test(str(policy.model_sha256));
}
export function calibrationMatches(policy: JsonRecord | null, detector: JsonRecord): boolean {
  return calibrationRecorded(policy) && /^[a-f0-9]{64}$/.test(str(detector.sha256)) && policy?.model_sha256 === detector.sha256;
}
export function calibrationScope(policy: JsonRecord | null, detector: JsonRecord): string {
  return JSON.stringify([policy?.speed, policy?.max_motion_ms, policy?.decision_budget_ms, policy?.corridor, policy?.perception, detector.sha256]);
}
export function loopSettingsValues(policy: JsonRecord, draft: JsonRecord): JsonRecord {
  const {revision: ignoredRevision, protocol_version: ignoredProtocol, ...values} = draft;
  void ignoredRevision; void ignoredProtocol;
  return calibrationScope(policy, {}) === calibrationScope(draft, {}) ? values
    : {...values, verified: false, calibration_id: "", model_sha256: ""};
}
/** UI extents write the existing Pi corridor; they are not distance presets. */
export function stoppingZone(policy: JsonRecord | null): "near" | "far" | "custom" {
  const roi=policy?.corridor;
  if(!Array.isArray(roi)||roi.length!==4)return "custom";
  const height=Number(roi[3])-Number(roi[1]);
  return Math.abs(height-.15)<1e-6?"near":Math.abs(height-.30)<1e-6?"far":"custom";
}
export function stoppingZoneValues(policy: JsonRecord, zone: "near" | "far"): JsonRecord | null {
  const roi=policy.corridor, height=zone==="near"?.15:.30;
  if(!Array.isArray(roi)||roi.length!==4||roi.some(v=>typeof v!=="number"||!Number.isFinite(v)))return null;
  const [x0,,x1,y1]=roi as number[];
  if(!(0<=x0&&x0<x1&&x1<=1&&height<=y1&&y1<=1))return null;
  if(typeof policy.speed!=="number"||!Number.isFinite(policy.speed)||policy.speed<=0
    ||typeof policy.max_motion_ms!=="number"||!Number.isFinite(policy.max_motion_ms)||policy.max_motion_ms<=0)return null;
  const next:JsonRecord={...policy,mode:"observe",corridor:[x0,Number((y1-height).toFixed(6)),x1,y1]};
  // Selecting the close trial extent never raises motion settings.
  if(zone==="near"){
    next.speed=Math.min(Number(policy.speed),.1);
    next.max_motion_ms=Math.min(Number(policy.max_motion_ms),150);
  }
  return loopSettingsValues(policy,next);
}
export function calibrationAcceptance(policy: JsonRecord, detector: JsonRecord, id: string): JsonRecord {
  if (!/^[a-f0-9]{64}$/.test(str(detector.sha256)) || !id.trim()) throw new Error("calibration_model_unavailable");
  return {...loopSettingsValues(policy, policy), verified: true, calibration_id: id, model_sha256: detector.sha256};
}
export function freshAnalysisFrame(frame: {ageMs?: number; receivedAt?: number; ageSource?: string; decoded?: boolean; stale: boolean; knownFreshness: boolean; fileAt: string; id: string; visionState: string; visionPolicy: string} | null, stream: string | null, now = Date.now()): boolean {
  const age = frameAge(frame);
  if (age !== null) return !!frame && frame.decoded === true && stream === "venom" && !frame.stale
    && frame.knownFreshness && !!frame.id && frame.ageSource === "pi-reported-plus-host-elapsed" && age <= 3000;
  const at = timestampMs(frame?.fileAt && Number.isFinite(Number(frame.fileAt)) ? Number(frame.fileAt) : frame?.fileAt);
  return !!frame && frame.decoded === true && stream === "venom" && !frame.stale && frame.knownFreshness && !!frame.id
    && !!frame.visionState && !!frame.visionPolicy && frame.visionPolicy !== "diagnostic_default"
    && at !== null && now >= at - 1000 && now - at <= 3000;
}

/** A new zone cannot be accepted from the previous zone's still-fresh JPEG. */
export function freshPolicyFrame(frame: (Parameters<typeof freshAnalysisFrame>[0] & {analysis?:JsonRecord})|null, stream:string|null, state:JsonRecord, policy:JsonRecord|null):boolean {
  const analysis=record(frame?.analysis);
  return freshAnalysisFrame(frame,stream)&&!!state.run_id&&analysis.run_id===state.run_id
    &&typeof policy?.revision==="number"&&analysis.config_revision===policy.revision
    &&analysis.state==="clear"&&analysis.vision_action==="go";
}

export function decisionView(state: JsonRecord, detector: JsonRecord) {
  const decision=record(state.decision), required=Number(detector.clear_frames_required)||3;
  const current=state.running===true&&state.stale!==true&&state.latched!==true;
  const count=typeof decision.clear_count==="number"?decision.clear_count:0;
  const vision=decision.state==="clear"&&count>=required&&decision.vision_action!=="stop" ? "go":"stop";
  return {
    current, required, count,
    imageReason:!current?(decision.state?"analysis_not_current":"waiting_for_decision"):decision.state!=="clear"?String(decision.reason??"waiting_for_decision"):count<required?"clear_frames_pending":"clear_frames_ready",
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
  if(detector.drive_supported===false)return "loopDepthObservation";
  if(runtime.drive!==true)return runtime.drive_reason==="navigation_executor_update_required"?"loopExecutorUpdate":"loopRuntimeMissing";
  if(!calibrationRecorded(policy)||detector.loaded===true&&!calibrationMatches(policy,detector))return "loopUnverified";
  const safety=safetyOf(status);
  if(safety.state==="ESTOP")return "trialEstop";
  if(safety.stale===true||status?.errors?.control)return "trialControlStale";
  if(!str(safety.generation)||!["DISARMED","ARMED"].includes(str(safety.state)))return "trialControlStale";
  if(safety.ready===false||record(record(safety.capabilities).move).available!==true)return "trialExecutorUnavailable";
  if(safety.remote_decision_deadline_supported!==true||safety.scoped_stop_generation_supported!==true)return "loopExecutorUpdate";
  if(record(safety.execution).inhibit)return "trialExecutionInhibited";
  return "";
}

export function motionPermission(state:JsonRecord, goals:JsonRecord, policy:JsonRecord|null, detector:JsonRecord, status:RiderStatus|null) {
  const safety=safetyOf(status), view=decisionView(state,detector);
  const known=!!status?.connection.configured&&["online","degraded"].includes(status.connection.status)&&!status.errors.control&&safety.stale!==true&&!!str(safety.generation)&&["ARMED","DISARMED","ESTOP"].includes(str(safety.state));
  const ready=safety.ready!==false&&record(record(safety.capabilities).move).available===true&&!record(safety.execution).inhibit;
  const manual=known?navigationAvailable(status)&&safety.can_move===true&&ready:null;
  let reason="permissionUnknown", allowed=false;
  if(known){
    if(!ready)reason="permissionExecutorBlocked";
    else if(view.current&&view.vision!=="go")reason="permissionDecisionStop";
    else if(goals.drive!==true||state.mode!=="drive")reason=goals.observe===true?"permissionObservation":"permissionIdle";
    else if(safety.state!=="ARMED"||safety.can_move!==true)reason="permissionDisarmed";
    else if(safety.owner!=="navigation")reason="permissionManualOwner";
    else if(!calibrationMatches(policy,detector))reason="permissionParameters";
    else if(!view.current)reason="permissionNoDecision";
    else if(view.reply!=="go"||view.vision!=="go")reason="permissionDecisionStop";
    else if(safety.remote_decision_deadline_supported!==true||!Number.isFinite(state.permit_remaining_ms??record(state.decision).valid_for_ms)||Number(state.permit_remaining_ms??record(state.decision).valid_for_ms)<50)reason="permissionPermitShort";
    else {allowed=true;reason="permissionGo";}
  }
  return {venom:known?allowed:null,manual,reason};
}

export function executionHeadline(state:JsonRecord,goals:JsonRecord,status:RiderStatus|null,permission:{venom:boolean|null}):string {
  if(state.mode==="drive"&&goals.drive===true&&state.paused===true&&state.running===true&&state.stale!==true&&safetyOf(status).state==="ARMED"&&safetyOf(status).owner==="navigation")return "trialVisualPause";
  if(permission.venom===null)return "permissionVenomUnknown";
  if(permission.venom)return "permissionVenomYes";
  const safety=safetyOf(status);
  if(goals.drive!==true||state.mode!=="drive")return goals.observe===true?"permissionVenomObserve":"permissionVenomIdle";
  if(safety.state==="DISARMED")return "permissionPiDisarmed";
  if(safety.state==="ARMED"&&safety.owner==="manual")return "permissionVenomManual";
  return "permissionVenomNo";
}

export function previewNotice(stale:boolean,selected:string,current:boolean|undefined,running:boolean|undefined):string|null {
  if(!stale&&!(selected==="venom"&&current===false))return null;
  return selected==="venom"&&running===false?"previewPausedNotice":"previewDelayedNotice";
}


/** Historical outcome is presentation only; never an authorization for motion. */
export function completedTrial(state: JsonRecord): JsonRecord | null {
  if(state.mode==="drive"&&state.running===true)return null;
  const row=record(state.last_trial);
  return row.mode==="drive"&&row.running===false&&!!str(row.run_id)
    &&timestampMs(row.observed_at)!==null&&!!str(row.stop_reason)?row:null;
}
