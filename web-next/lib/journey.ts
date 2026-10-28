import { calibrationMatches, navigationPulseLimit, navigationSpeedLimit, trialBlock } from "./loop-controls";
import { record, safetyOf, str, type JsonRecord, type RiderStatus } from "./model";

/** Guidance only: Pi validates the saved policy before granting motion.
 * requireAcceptance=false checks prerequisites for an explicit confirm-and-start
 * transaction. It never grants motion or changes the persisted policy itself. */
export function journeyBlocks(policy: JsonRecord | null, runtime: JsonRecord, detector: JsonRecord, connected: boolean, dirty: boolean, status: RiderStatus | null, requireAcceptance = true): string[] {
  const blocks: string[] = [];
  const baseline = trialBlock(policy, runtime, detector, connected, dirty, status, requireAcceptance);
  if (baseline) blocks.push(baseline);
  if (safetyOf(status).navigation_journey_version !== 1 || detector.navigation_journey_version !== 1) blocks.push("journeyUpdateRequired");
  if (!policy) return [...new Set(blocks)];
  const avoidance = record(policy.avoidance), geometry = record(avoidance.geometry);
  if (avoidance.enabled !== true || avoidance.protocol_version !== 3) blocks.push("journeyAvoidanceRequired");
  if (requireAcceptance && avoidance.sweep_verified !== true) blocks.push("journeySweepRequired");
  if (geometry.mode === "ground" && (!["fx", "fy", "radius_ratio", "lookahead_ratio", "max_heading_deg"].every(key => typeof geometry[key] === "number" && Number.isFinite(geometry[key]) && Number(geometry[key]) > 0)
    || !["cx", "cy"].every(key => typeof geometry[key] === "number" && Number.isFinite(geometry[key]) && Number(geometry[key]) >= 0 && Number(geometry[key]) <= 1))) blocks.push("geometryCalibrationMissing");
  if (requireAcceptance && str(detector.sha256) && !calibrationMatches(policy, detector)) blocks.push("loopUnverified");
  if (typeof policy.speed !== "number" || !Number.isFinite(policy.speed) || policy.speed <= 0 || policy.speed > navigationSpeedLimit(policy,true)
    || typeof policy.max_motion_ms !== "number" || !Number.isFinite(policy.max_motion_ms) || policy.max_motion_ms <= 0 || policy.max_motion_ms > navigationPulseLimit(policy,true)) blocks.push("journeyMotionLimits");
  return [...new Set(blocks)];
}

/** Preserve the idle/unknown distinction before interpreting a known journey phase. */
function journeyPhase(journey:JsonRecord,supported:boolean,current:boolean,active:boolean):string {
  const known = ["qualifying", "forward", "steer_left", "steer_right", "waiting", "blocked", "stopped", "failed"];
  if(!supported){
  return ("idle");
}
if(!current && active){
  return ("unknown");
}
if(known.includes(str(journey.state))){
  return (str(journey.state));
}
if(active){
  return ("unknown");
}
return ("idle");

}
/** Stale status never represents a currently travelling robot. */
export function journeyView(state: JsonRecord) {
  const currentJourney=record(state.journey), previous=record(record(state.last_trial).journey);
  const historical=!(currentJourney.version===1&&str(currentJourney.id))&&previous.version===1&&previous.active===false&&!!str(previous.id);
  const journey = historical?previous:currentJourney;
  const supported = journey.version === 1 && !!str(journey.id);
  const current = state.stale !== true;
  const active = supported && journey.active === true;
  const phase=journeyPhase(journey,supported,current,active);
  return { active, phase, historical, reason: supported ? str(journey.reason) : "", id: supported ? str(journey.id) : "",
    elapsed: supported && typeof journey.elapsed_s === "number" && Number.isFinite(journey.elapsed_s) ? journey.elapsed_s : null,
    maneuver: supported && Number.isInteger(journey.maneuver_epoch) ? Number(journey.maneuver_epoch) : null };
}
