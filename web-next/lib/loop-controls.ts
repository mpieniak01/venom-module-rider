import { slowLeaseFactor } from "./behavior";
import { messages } from "./i18n";
import { frameAge } from "./vision-frame";
import { navigationAvailable } from "./navigation";
import { record, safetyOf, str, timestampMs, type JsonRecord, type RiderStatus } from "./model";

export type LoopPhase = "offline" | "unknown" | "stopped" | "starting" | "running" | "fault";
export function loopPhase(connected: boolean, state: JsonRecord, goals: JsonRecord): LoopPhase {
  if (!connected) return "offline";
  if (state.mode === "drive" && state.running === false && state.stop_reason === "trial_completed") return "stopped";
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
  return JSON.stringify([policy?.speed, policy?.max_motion_ms, policy?.decision_budget_ms, policy?.corridor, policy?.perception, policy?.avoidance, detector.sha256]);
}
/** Apply only reads that do not roll back the last response or policy revision. */
export function navigationReadCurrent(request: number, applied: number, incoming: unknown, current: unknown): boolean {
  return request >= applied && !(typeof incoming === "number" && typeof current === "number" && incoming < current);
}
/** A confirmed Start survives a failed status GET; STOP/session/epoch changes still cancel it. */
export async function refreshStartedNavigation(read: () => Promise<void>, current: () => boolean): Promise<unknown | null> {
  const check=()=>{if(!current())throw new Error("navigation_trial_cancelled");};
  check();
  try{await read();}catch(error){check();return error;}
  check();return null;
}
/** A warm service request alone does not imply permission to move. */
export function navigationSettingsDriveBlocked(status: RiderStatus | null, controlFresh: boolean, goals: JsonRecord): boolean {
  if (goals.drive !== true) return false;
  const safety = safetyOf(status);
  return !status || !controlFresh || !!status.errors.control || safety.stale === true
    || safety.state !== "DISARMED" || safety.moving !== false
    || record(goals.preparation).active === true
    || ["active", "qualifying"].includes(str(record(safety.operator_control).state));
}
/** Old Pi versions retain their limits; only a device capability can raise the field range. */
export function navigationPulseLimit(policy: JsonRecord | null, journey = false): number {
  const limits = record(policy?.motion_limits);
  if (limits.version === 1 && Number.isInteger(limits.max_motion_ms) && Number(limits.max_motion_ms) >= 50)
    return Math.min(500, Number(limits.max_motion_ms));
  return journey || record(policy?.avoidance).enabled === true ? 150 : 200;
}
/** Capabilities belong to Pi; old firmware keeps its original motion bounds. */
export function navigationSpeedLimit(policy: JsonRecord | null, journey = false, turn = false): number {
  const limits = record(policy?.motion_limits), value = limits[turn ? "max_turn_speed" : "max_speed"];
  if (limits.version === 1 && typeof value === "number" && Number.isFinite(value) && value >= .01) return Math.min(1, value);
  return turn || journey || record(policy?.avoidance).enabled === true ? .1 : .15;
}
export function navigationTurnPulseLimit(policy: JsonRecord | null): number {
  const limits = record(policy?.motion_limits);
  return limits.version === 1 && Number.isInteger(limits.max_turn_ms) && Number(limits.max_turn_ms) >= 50 ? Math.min(500, Number(limits.max_turn_ms)) : 150;
}
export function loopSettingsValues(policy: JsonRecord, draft: JsonRecord): JsonRecord {
  // Pi presentation metadata is read-only; only persisted policy fields are writable.
  const fields = ["mode", "speed", "max_motion_ms", "decision_budget_ms", "corridor", "perception", "avoidance", "behavior", "verified", "calibration_id", "model_sha256"];
  const values = Object.fromEntries(fields.filter(key => key in draft).map(key => [key, draft[key]]));
  return calibrationScope(policy, {}) === calibrationScope(draft, {}) ? values
    : {...values, mode: "observe", verified: false, calibration_id: "", model_sha256: ""};
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
export function calibrationAcceptance(policy: JsonRecord, detector: JsonRecord, id: string, acceptSweep = false): JsonRecord {
  if (!/^[a-f0-9]{64}$/.test(str(detector.sha256)) || !id.trim()) throw new Error("calibration_model_unavailable");
  const avoidance=record(policy.avoidance);
  if(acceptSweep&&(avoidance.enabled!==true||avoidance.protocol_version!==3))throw new Error("calibration_avoidance_unavailable");
  return {...loopSettingsValues(policy, policy), ...(acceptSweep?{avoidance:{...avoidance,sweep_verified:true}}:{}), verified: true, calibration_id: id, model_sha256: detector.sha256};
}
export function freshAnalysisFrame(frame: {ageMs?: number; receivedAt?: number; ageSource?: string; decoded?: boolean; stale: boolean; knownFreshness: boolean; fileAt: string; id: string; visionState: string; visionPolicy: string} | null, stream: string | null, now = Date.now()): boolean {
  const age = frameAge(frame);
  if (age !== null) return !!frame && frame.decoded === true && stream === "venom" && !frame.stale
    && frame.knownFreshness && !!frame.id && ["pi-reported-plus-host-elapsed","pi-status-anchor-plus-host-elapsed"].includes(frame.ageSource??"") && age <= 3000;
  const at = timestampMs(frame?.fileAt && Number.isFinite(Number(frame.fileAt)) ? Number(frame.fileAt) : frame?.fileAt);
  return !!frame && frame.decoded === true && stream === "venom" && !frame.stale && frame.knownFreshness && !!frame.id
    && !!frame.visionState && !!frame.visionPolicy && frame.visionPolicy !== "diagnostic_default"
    && at !== null && now >= at - 1000 && now - at <= 3000;
}

/** The visible analysis must belong to this run and persisted policy. */
export function calibrationFrameBlock(frame: (Parameters<typeof freshAnalysisFrame>[0] & {analysis?:JsonRecord})|null, stream:string|null, state:JsonRecord, policy:JsonRecord|null):string {
  if(stream!=="venom")return "calibrationShowVision";
  if(!freshAnalysisFrame(frame,stream))return "calibrationFreshFrame";
  const analysis=record(frame?.analysis);
  if(!state.run_id||analysis.run_id!==state.run_id||typeof policy?.revision!=="number"||analysis.config_revision!==policy.revision)return "calibrationMatchingFrame";
  if(analysis.state!=="clear"||analysis.vision_action!=="go")return "calibrationClearFrame";
  return "";
}
/** Diagnostic image GO is presentation only; the Pi reply/permission stays separate. */
export function diagnosticImageCurrent(frame: (Parameters<typeof freshAnalysisFrame>[0] & {analysis?:JsonRecord})|null, policy:JsonRecord|null, detector:JsonRecord):boolean {
  return freshAnalysisFrame(frame,"venom")&&(frameAge(frame)??Infinity)<1000
    &&frame?.analysis?.preview_only===true&&frame.analysis.config_revision===policy?.revision
    &&typeof policy?.revision==="number"&&/^[a-f0-9]{64}$/.test(str(detector.sha256))
    &&frame.analysis.model_sha256===detector.sha256;
}
export function freshPolicyFrame(frame: (Parameters<typeof freshAnalysisFrame>[0] & {analysis?:JsonRecord})|null, stream:string|null, state:JsonRecord, policy:JsonRecord|null):boolean {
  return calibrationFrameBlock(frame,stream,state,policy)==="";
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
    reply:current&&["go","slow","stop","turn_left","turn_right","steer_left","steer_right"].includes(String(decision.action))?String(decision.action):"none",
    reason:current?String(decision.motion_reason??(decision.state!=="clear"?decision.reason:count<required?"clear_frames_pending":state.mode==="observe"?"observe_only":decision.reason)??"waiting_for_decision"):String(state.stop_reason??"waiting_for_decision"),
    floor:typeof decision.floor_fraction==="number"?decision.floor_fraction:null,
    confidence:typeof decision.confidence_fraction==="number"?decision.confidence_fraction:null,
  };
}
export function trialBlock(policy: JsonRecord|null, runtime:JsonRecord, detector:JsonRecord, connected:boolean, dirty:boolean, status:RiderStatus|null, requireAcceptance=true):string {
  if(!connected)return "loopOffline";
  if(!policy)return "loopLoadFailed";
  if(dirty)return "loopUnsaved";
  const avoidance=record(policy.avoidance),geometry=record(avoidance.geometry);
  if(avoidance.enabled===true&&avoidance.protocol_version===3&&geometry.mode==="ground"){
    if(detector.ground_geometry_supported!==true)return "geometryHostUpdate";
    if(!["fx","fy","radius_ratio"].every(key=>typeof geometry[key]==="number"&&Number.isFinite(geometry[key])&&Number(geometry[key])>0))return "geometryCalibrationMissing";
  }
  if(runtime.drive!==true)return runtime.drive_reason==="navigation_executor_update_required"?"loopExecutorUpdate":"loopRuntimeMissing";
  if(detector.configured!==true)return "loopNoModel";
  if(detector.drive_supported===false)return "loopDepthObservation";
  if(requireAcceptance&&(!calibrationRecorded(policy)||detector.loaded===true&&!calibrationMatches(policy,detector)))return "loopUnverified";
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
  const decision=record(state.decision),side=["turn_left","steer_left"].includes(String(decision.action))?"left":"right";
  const turning=view.current&&((decision.protocol_version===2&&["turn_left","turn_right"].includes(String(decision.action)))||(decision.protocol_version===3&&["steer_left","steer_right"].includes(String(decision.action))&&["guard",side].every(k=>record(record(decision.paths)[k]).state==="clear"&&record(record(decision.paths)[k]).coverage_complete===true)))&&decision.state==="obstacle"&&record(policy?.avoidance).enabled===true&&record(policy?.avoidance).sweep_verified===true&&record(record(decision.sectors)[side]).state==="clear"&&record(record(decision.sectors)[side]).coverage_complete===true;
  const slow=slowLeaseFactor(state,policy,safety)!==null;
  const ready=safety.ready!==false&&record(record(safety.capabilities).move).available===true&&!record(safety.execution).inhibit;
  const manual=known?navigationAvailable(status)&&safety.can_move===true&&ready:null;
  let reason="permissionUnknown", allowed=false;
  if(known){
    const geometry=record(record(policy?.avoidance).geometry),reference=record(decision.ground_reference);
    const normal=reference.normal;
    const groundValid=geometry.mode!=="ground"||record(policy?.avoidance).enabled!==true
      ||decision.geometry==="ground_capsule"&&Array.isArray(normal)&&normal.length===3
        &&normal.every(v=>typeof v==="number"&&Number.isFinite(v))
        &&Number(normal[1])>=.2&&normal.reduce((sum:number,v:number)=>sum+v*v,0)>=.9&&normal.reduce((sum:number,v:number)=>sum+v*v,0)<=1.1
        &&typeof reference.height==="number"&&Number.isFinite(reference.height)&&reference.height>0
        &&typeof decision.ground_support==="number"&&Number.isFinite(decision.ground_support)&&decision.ground_support>=.3&&decision.ground_support<=1;
    if(!ready)reason="permissionExecutorBlocked";
    else if(!groundValid)reason="permissionDecisionStop";
    else if(decision.action==="slow"&&!slow)reason="permissionSlowUnconfirmed";
    else if(view.current&&view.vision!=="go"&&!turning&&!slow)reason="permissionDecisionStop";
    else if(goals.drive!==true||state.mode!=="drive")reason=goals.observe===true?"permissionObservation":"permissionIdle";
    else if(safety.state!=="ARMED"||safety.can_move!==true)reason="permissionDisarmed";
    else if(safety.owner!=="navigation")reason="permissionManualOwner";
    else if(!calibrationMatches(policy,detector))reason="permissionParameters";
    else if(!view.current)reason="permissionNoDecision";
    else if(!turning&&!slow&&(view.reply!=="go"||view.vision!=="go"))reason="permissionDecisionStop";
    else if(safety.remote_decision_deadline_supported!==true||!Number.isFinite(state.permit_remaining_ms??record(state.decision).valid_for_ms)||Number(state.permit_remaining_ms??record(state.decision).valid_for_ms)<50)reason="permissionPermitShort";
    else {allowed=true;reason=slow?"permissionSlow":turning?side==="left"?"permissionTurnLeft":"permissionTurnRight":"permissionGo";}
  }
  return {venom:known?allowed:null,manual,reason};
}

export function executionHeadline(state:JsonRecord,goals:JsonRecord,status:RiderStatus|null,permission:{venom:boolean|null}):string {
  if(state.mode==="drive"&&goals.drive===true&&state.paused===true&&state.running===true&&state.stale!==true&&safetyOf(status).state==="ARMED"&&safetyOf(status).owner==="navigation")return "trialVisualPause";
  if(permission.venom===null)return "permissionVenomUnknown";
  if(permission.venom){const action=record(state.decision).action;return action==="slow"?"permissionVenomSlow":action==="steer_left"?"permissionVenomSteerLeft":action==="steer_right"?"permissionVenomSteerRight":action==="turn_left"?"permissionVenomTurnLeft":action==="turn_right"?"permissionVenomTurnRight":"permissionVenomYes";}
  const safety=safetyOf(status);
  if(goals.drive!==true||state.mode!=="drive")return goals.observe===true?"permissionVenomObserve":"permissionVenomIdle";
  if(safety.state==="DISARMED")return "permissionPiDisarmed";
  if(safety.state==="ARMED"&&safety.owner==="manual")return "permissionVenomManual";
  if(record(state.decision).action==="slow")return "permissionVenomSlowUnconfirmed";
  return "permissionVenomNo";
}

/** Preview transport is independent of the Pi navigation producer and its decision. */
export function previewNotice(stale:boolean,selected:string,polling:boolean,transitioning=false):string|null {
  if(selected==="venom"&&transitioning)return "visionPreviewRestarting";
  if(!stale)return null;
  return polling?"previewDelayedNotice":"previewPausedNotice";
}


/** Historical outcome is presentation only; never an authorization for motion. */
export function completedTrial(state: JsonRecord): JsonRecord | null {
  if(state.mode==="drive"&&state.running===true)return null;
  const row=record(state.last_trial);
  return row.mode==="drive"&&row.running===false&&!!str(row.run_id)
    &&timestampMs(row.observed_at)!==null&&!!str(row.stop_reason)?row:null;
}


export type TrialPreparation = {
  requestId: string;
  operatorRequestId: string;
  phase: string;
  state: "preparing" | "failed" | "completed";
  elapsedMs: number | null;
  observedAt: number;
  error: string;
  fresh: boolean;
  source: "pi" | "request";
};
const preparationPhases = new Set([
  "admission", "camera_reservation", "stop_old_producer", "configure",
  "authority_handoff", "arm_ack", "start_navigation", "first_status", "ready",
]);
function preparationRecord(value: unknown, source: "pi" | "request"): TrialPreparation | null {
  const row = record(value), state = row.state, observedAt = timestampMs(row.observed_at);
  if (!["preparing", "failed", "completed"].includes(str(state)) || !str(row.request_id) || observedAt === null) return null;
  return {
    requestId: str(row.request_id),
    operatorRequestId: /^trial-[0-9a-f]{32}-start$/.test(str(row.operator_request_id)) ? str(row.operator_request_id) : "",
    phase: preparationPhases.has(str(row.phase)) ? str(row.phase) : "unknown",
    state: state as TrialPreparation["state"],
    elapsedMs: typeof row.elapsed_ms === "number" && Number.isFinite(row.elapsed_ms) && row.elapsed_ms >= 0 ? row.elapsed_ms : null,
    observedAt,
    error: str(row.error) || str(record(row.error).code),
    fresh: row.fresh === true,
    source,
  };
}
/** Pi preparation is shared across tabs; it never grants movement permission. */
export function trialPreparation(state: JsonRecord, error: unknown = null): TrialPreparation | null {
  const pi = preparationRecord(state.preparation, "pi");
  const details = record(record(record(record(error).data).detail).details);
  const request = preparationRecord(details.preparation, "request");
  // Both timestamps originate on Pi. A just-returned newer failure can precede
  // the next status poll; a fresh newer Pi record replaces older per-tab errors.
  if (pi?.fresh && (!request || pi.observedAt >= request.observedAt)) return pi;
  return request ?? pi;
}

export function trialPreparationTitle(value: TrialPreparation): string {
  return value.fresh ? "trialPreparation.state." + value.state : "trialPreparationHistorical";
}
export function trialPreparationErrorKey(value: TrialPreparation): string {
  const key = "trial.error." + value.error;
  return Object.prototype.hasOwnProperty.call(messages, key) ? key : "requestFailed";
}


/** One stopped-run outcome may supersede a delayed preparation failure. */
export function trialDiagnostics(state: JsonRecord, error: unknown = null) {
  const preparation = trialPreparation(state, error);
  const details = record(record(record(record(error).data).detail).details);
  const responseStatus = record(details.status);
  const terminal = (value: JsonRecord) => completedTrial({last_trial:value});
  const candidates = [terminal(state), completedTrial(state), terminal(responseStatus)]
    .filter((value): value is JsonRecord => value !== null)
    .sort((a,b) => (timestampMs(b.observed_at) ?? 0) - (timestampMs(a.observed_at) ?? 0));
  const outcome = state.mode === "drive" && state.running === true && state.stale !== true ? null : candidates[0] ?? null;
  const responsePreparation = record(details.preparation);
  const matchesPreparation = !!outcome && !!preparation?.operatorRequestId
    && preparation.operatorRequestId === str(outcome.operator_request_id);
  const matchesResponse = !!outcome && details.started === true
    && str(responseStatus.run_id) === str(outcome.run_id);
  const combined = !!preparation && (matchesPreparation || matchesResponse
    && str(responsePreparation.request_id) === preparation.requestId);
  const requestErrorCovered = matchesResponse || combined
    && str(responsePreparation.request_id) === preparation?.requestId;
  return {preparation, outcome, combined, requestErrorCovered};
}

export function trialStopReasonKey(reason: unknown): string {
  const key = "vision.reason." + str(reason);
  return Object.prototype.hasOwnProperty.call(messages, key) ? key : "trialStopped";
}


/** Only an explicitly confirmed Pi completion is a neutral terminal outcome. */
export function trialOutcomeTone(reason: unknown): "neutral" | "error" {
  return reason === "trial_completed" ? "neutral" : "error";
}


export function freshObservation(state: JsonRecord, goals: JsonRecord): boolean {
  return goals.observe === true && state.mode === "observe" && state.running === true && state.stale === false;
}
