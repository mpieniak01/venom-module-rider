import { finite, record, safetyOf, str, type JsonRecord, type RiderStatus } from "./model";

export const BEHAVIOR_IDS = ["legacy", "cautious", "balanced", "fluid"] as const;
export type BehaviorId = typeof BEHAVIOR_IDS[number];
export type BehaviorSelection = { version: 1; profile_id: BehaviorId; profile_revision: number };
export type BehaviorProfile = { id: BehaviorId; profile_revision: number; status: "legacy" | "experimental"; parameters: JsonRecord };
export type BehaviorCatalog = { supported: boolean; active: BehaviorSelection; effective_policy_sha256: string; profiles: BehaviorProfile[] };
export type BehaviorDraft = { selection: BehaviorSelection; revision: number; effectiveHash: string; session: string };
const legacy: BehaviorSelection = { version: 1, profile_id: "legacy", profile_revision: 1 };
const knownId = (value: unknown): value is BehaviorId => BEHAVIOR_IDS.includes(value as BehaviorId);
export function behaviorSelection(value: unknown): BehaviorSelection | null {
  const row = record(value);
  return row.version === 1 && knownId(row.profile_id) && Number.isSafeInteger(row.profile_revision) && Number(row.profile_revision) > 0
    ? { version: 1, profile_id: row.profile_id, profile_revision: Number(row.profile_revision) } : null;
}
/** Only the device catalog supplies available revisions and effective parameters. */
export function behaviorCatalog(policy: JsonRecord | null): BehaviorCatalog {
  const raw = record(policy?.behavior_catalog), active = behaviorSelection(raw.active) ?? behaviorSelection(policy?.behavior) ?? legacy;
  const fallback = { supported: false, active, effective_policy_sha256: "", profiles: [{ id: active.profile_id, profile_revision: active.profile_revision, status: active.profile_id === "legacy" ? "legacy" : "experimental", parameters: {} }] } as BehaviorCatalog;
  if (raw.version !== 1 || raw.supported !== true || !Array.isArray(raw.profiles) || !/^[a-f0-9]{64}$/.test(str(raw.effective_policy_sha256))) return fallback;
  const profiles: BehaviorProfile[] = [];
  for (const value of raw.profiles) {
    const row = record(value);
    if (!knownId(row.id) || !Number.isSafeInteger(row.profile_revision) || Number(row.profile_revision) < 1
      || row.status !== (row.id === "legacy" ? "legacy" : "experimental") || profiles.some(item => item.id === row.id)) return fallback;
    profiles.push({ id: row.id, profile_revision: Number(row.profile_revision), status: row.id === "legacy" ? "legacy" : "experimental", parameters: record(row.parameters) });
  }
  if (!behaviorSelection(raw.active) || !profiles.some(row => row.id === active.profile_id && row.profile_revision === active.profile_revision)) return fallback;
  return { supported: true, active, effective_policy_sha256: str(raw.effective_policy_sha256), profiles };
}
export function behaviorDraft(policy: JsonRecord, id: string, session: string): BehaviorDraft | null {
  const catalog = behaviorCatalog(policy), profile = catalog.profiles.find(row => row.id === id);
  if (!catalog.supported || !profile || !Number.isSafeInteger(policy.revision)) return null;
  return { selection: { version: 1, profile_id: profile.id, profile_revision: profile.profile_revision },
    revision: Number(policy.revision), effectiveHash: catalog.effective_policy_sha256, session };
}
export function behaviorDraftStale(draft: BehaviorDraft, policy: JsonRecord | null, session: string): boolean {
  const catalog = behaviorCatalog(policy);
  return !catalog.supported || draft.session !== session || draft.revision !== policy?.revision
    || draft.effectiveHash !== catalog.effective_policy_sha256
    || !catalog.profiles.some(row => row.id === draft.selection.profile_id && row.profile_revision === draft.selection.profile_revision);
}
export function behaviorWrite(draft: BehaviorDraft, policy: JsonRecord | null, session: string): JsonRecord | null {
  return behaviorDraftStale(draft, policy, session) ? null : { revision: draft.revision, values: { behavior: draft.selection } };
}
/** UI guidance; the device repeats the idle and revision checks atomically. */
export function behaviorSwitchBlock(status: RiderStatus | null, controlFresh: boolean, goals: JsonRecord, locked: boolean): string {
  if (!status || !["online", "degraded"].includes(status.connection.status)) return "behavior.block.offline";
  const safety = safetyOf(status);
  if (!controlFresh || status.errors.control || safety.stale === true || !["DISARMED", "ARMED", "ESTOP"].includes(str(safety.state))) return "behavior.block.stale";
  if (safety.moving === true || goals.drive === true || record(goals.preparation).active === true
    || ["active", "qualifying"].includes(str(record(safety.operator_control).state))) return "behavior.block.motion";
  if (goals.observe === true) return "behavior.block.observation";
  return locked ? "behavior.block.busy" : "";
}
export function behaviorError(error: unknown): string {
  const outer = record(error), response = record(outer.data), detail = record(response.detail);
  const code = str(detail.code) || str(response.error) || str(outer.message);
  if (code.includes("stop_goals") || code.includes("stop_loops")) return "behavior.block.observation";
  if (code.includes("conflict") || code.includes("changed")) return "behavior.conflict";
  if (code.includes("active") || code.includes("idle") || code.includes("motion") || code.includes("stopped")) return "behavior.block.motion";
  return "behavior.failed";
}
export type TemporalView = { available: boolean; current: boolean; raw: string; stabilized: string; risk: string; action: string; reason: string; profile: string; revision: string; nearGuard: boolean | null; boundaryPoints: number; evidenceFrames: number | null; speedFactor: number | null };
/** Diagnostics never infer a physical boundary or grant movement permission. */
export function temporalView(state: JsonRecord): TemporalView {
  const decision = record(state.decision), temporal = record(decision.temporal), behavior = record(decision.behavior);
  return { available: typeof temporal.action === "string" && typeof temporal.state === "string", current: state.running === true && state.stale !== true,
    raw: str(decision.raw_state), stabilized: str(temporal.state), risk: str(temporal.risk), action: str(temporal.action), reason: str(temporal.reason),
    profile: str(behavior.profile_id), revision: finite(behavior.profile_revision) ? String(behavior.profile_revision) : "",
    nearGuard: typeof temporal.near_guard_clear === "boolean" ? temporal.near_guard_clear : null,
    boundaryPoints: Array.isArray(record(temporal.spatial).boundary) ? (record(temporal.spatial).boundary as unknown[]).length : 0,
    evidenceFrames: finite(temporal.evidence_frames) ? temporal.evidence_frames : null, speedFactor: finite(temporal.speed_factor) ? temporal.speed_factor : null };
}

/** A Pi-reported bounded SLOW lease, never an SDK acknowledgement or distance measurement. */
export function slowLeaseFactor(state: JsonRecord, policy: JsonRecord | null, safety: JsonRecord): number | null {
  const decision = record(state.decision), temporal = record(decision.temporal), catalog = behaviorCatalog(policy);
  const expected = behaviorSelection(policy?.behavior), received = behaviorSelection(decision.behavior);
  const profile = catalog.profiles.find(row => row.id === expected?.profile_id);
  const factor = temporal.speed_factor, permit = state.permit_remaining_ms, age = state.age_s;
  if (!catalog.supported || !expected || expected.profile_id === "legacy" || !received
    || expected.profile_id !== catalog.active.profile_id || expected.profile_revision !== catalog.active.profile_revision
    || received.profile_id !== expected.profile_id || received.profile_revision !== expected.profile_revision
    || decision.effective_policy_sha256 !== catalog.effective_policy_sha256
    || state.running !== true || state.stale === true || state.latched === true || state.paused !== false
    || state.mode !== "drive" || policy?.mode !== "drive" || policy.verified !== true
    || !str(state.run_id) || decision.run_id !== state.run_id || !str(state.generation) || state.generation !== safety.generation
    || !Number.isSafeInteger(policy.revision) || decision.config_revision !== policy.revision || state.config_revision !== policy.revision
    || !Number.isSafeInteger(decision.sequence) || Number(decision.sequence) < 0
    || decision.action !== "slow" || !["clear", "obstacle", "unknown"].includes(str(decision.state))
    || !/^[a-f0-9]{64}$/.test(str(policy.model_sha256)) || decision.model_sha256 !== policy.model_sha256
    || temporal.version !== 1 || temporal.action !== "slow" || temporal.risk !== "caution" || temporal.near_guard_clear !== true
    || !Number.isSafeInteger(temporal.evidence_frames) || Number(temporal.evidence_frames) < 3 || Number(temporal.evidence_frames) > 64
    || !Number.isSafeInteger(decision.clear_count) || Number(decision.clear_count) < 3
    || !finite(factor) || factor <= 0 || factor > 1 || factor !== profile?.parameters.slow_speed_factor
    || !finite(age) || age < 0 || age > 1 || !finite(permit) || permit - age * 1000 < 50
    || !Number.isInteger(decision.valid_for_ms) || Number(decision.valid_for_ms) < 1 || Number(decision.valid_for_ms) > 200)
    return null;
  return factor;
}
