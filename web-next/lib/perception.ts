import { frameAge } from "./vision-frame";
import { errorCode, finite, record, safetyOf, str, type JsonRecord, type RiderStatus } from "./model";

export type PerceptionSelection = { model_id: string; artifact_sha256: string; revision: number };
export type PerceptionModel = {
  id: string; label: string; engine: string; task: string; revision: string;
  installed: boolean; installation: string; loaded: boolean; selectable: boolean;
  qualification_details: string[]; reason: string; drive_supported: boolean; provider: string; execution_provider: string; sha256: string; job: JsonRecord;
};
export type PerceptionCatalog = {
  models: PerceptionModel[]; selection: PerceptionSelection | null; switching: boolean;
  diagnostics: JsonRecord; provider: string; pipeline_revision: number | null; local_engine: string;
  providers: { id: string; available: boolean }[]; local_models: PerceptionModel[]; local_models_error: string; selection_error: string;
};
const INSTALL_STATES = ["not_installed", "downloading", "verifying", "ready", "failed", "needs_review"];
// Pi reports availability of its existing engines, separately from host provisioning.
const LOCAL_MODEL_STATES = [...INSTALL_STATES, "unavailable"];

/** Venom supplies the catalog; clients never invent a local list or install state. */
export function perceptionCatalog(value: unknown): PerceptionCatalog {
  const row = record(value);
  if (!Array.isArray(row.models)) throw new Error("perception_catalog_invalid");
  const parseModels = (items: unknown[], states = INSTALL_STATES) => items.map(item => {
    const model = record(item);
    if (!str(model.id) || !str(model.label) || !states.includes(str(model.installation)))
      throw new Error("perception_catalog_invalid");
    return {
      id: str(model.id), label: str(model.label), engine: str(model.engine), task: str(model.task),
      revision: str(model.revision), installed: model.installed === true, installation: str(model.installation),
      loaded: model.loaded === true, selectable: model.selectable === true, reason: str(model.reason), qualification_details: Array.isArray(model.qualification_details) ? model.qualification_details.filter((value): value is string => typeof value === "string") : [],
      drive_supported: model.drive_supported === true, provider: str(model.provider), execution_provider: str(model.execution_provider), sha256: str(model.sha256),
      job: record(model.job),
    };
  });
  const models = parseModels(row.models);
  const local_models = parseModels(Array.isArray(row.local_models) ? row.local_models : [], LOCAL_MODEL_STATES);
  if (new Set(models.map(model => model.id)).size !== models.length) throw new Error("perception_catalog_invalid");
  return { models, selection: selectionFrom(row.selection), switching: row.switching === true, diagnostics: record(row.diagnostics), provider: ["local", "venom"].includes(str(row.provider)) ? str(row.provider) : "unknown",
    pipeline_revision: Number.isSafeInteger(row.pipeline_revision) ? Number(row.pipeline_revision) : null,
    local_engine: str(row.local_engine), local_models, local_models_error: str(row.local_models_error), selection_error: str(row.selection_error), providers: Array.isArray(row.providers) ? row.providers.map(value => { const provider = record(value); return { id: str(provider.id), available: provider.available === true }; }).filter(provider => ["local", "venom"].includes(provider.id)) : [{ id: "venom", available: true }], };
}
export function selectionFrom(value: unknown): PerceptionSelection | null {
  const row = record(value);
  return typeof row.model_id === "string" && Number.isSafeInteger(row.revision) && Number(row.revision) >= 0
    ? { model_id: row.model_id, artifact_sha256: str(row.artifact_sha256), revision: Number(row.revision) } : null;
}
export function perceptionPolicy(value: JsonRecord | null): PerceptionSelection | null {
  if (!value || !Number.isSafeInteger(value.revision)) return null;
  const perception = record(value.perception);
  return { model_id: str(perception.model_id), artifact_sha256: str(perception.artifact_sha256), revision: Number(value.revision) };
}

/** Legacy policies have no explicit model ID; preserve their established preview contract.
 * Explicit policies require the artifact reference, not the separate movement approval hash.
 */
export function perceptionResultMatches(analysis: JsonRecord | undefined, expected: PerceptionSelection | null | undefined): boolean {
  if (expected === undefined) return true;
  if (expected === null) return false;
  if (!expected.model_id) return true;
  return /^[a-f0-9]{64}$/.test(expected.artifact_sha256) && !!analysis
    && analysis.model_id === expected.model_id && analysis.artifact_sha256 === expected.artifact_sha256
    && analysis.config_revision === expected.revision;
}
type Preview = { analysis?: JsonRecord; stale: boolean; knownFreshness: boolean; decoded?: boolean; ageMs?: number; receivedAt?: number };
export function perceptionFrameCurrent(frame: Preview | null, expected: PerceptionSelection | null | undefined, now = performance.now()): boolean {
  if (!frame || !perceptionResultMatches(frame.analysis, expected)) return false;
  if (!expected?.model_id) return true;
  const age = frameAge(frame, now);
  return !frame.stale && frame.knownFreshness && frame.decoded === true && age !== null && age < 1000;
}

/** A delayed policy read may confirm the image already being shown.
 * Keep that exact fresh capture; changed revisions/artifacts still invalidate it.
 */
export function previewMatchesSelection(frame: Preview | null, expected: PerceptionSelection | null, now = performance.now()): boolean {
  const age = frameAge(frame, now);
  return !!expected && !!frame && frame.analysis?.config_revision === expected.revision
    && perceptionResultMatches(frame.analysis, expected)
    && !frame.stale && frame.knownFreshness && frame.decoded === true && age !== null && age < 1000;
}

/** UI guidance only. Pi and host independently enforce the atomic switch reservation. */
export function perceptionSwitchBlock(status: RiderStatus | null, controlFresh: boolean, goals: JsonRecord, policy: JsonRecord | null, busy: boolean): string {
  if (!status || !["online", "degraded"].includes(status.connection.status)) return "perception.block.offline";
  const safety = safetyOf(status);
  if (!controlFresh || status.errors.control || safety.stale === true || !["DISARMED", "ESTOP", "ARMED"].includes(str(safety.state)))
    return "perception.block.stale";
  if (safety.state === "ARMED" || safety.can_move === true || safety.moving === true || ["manual", "navigation"].includes(str(safety.owner))
    || goals.drive === true || record(goals.preparation).active === true)
    return "perception.block.motion";
  if (goals.observe === true) return "perception.block.loop";
  if (busy) return "perception.block.busy";
  if (!policy || !Number.isSafeInteger(policy.revision)) return "perception.block.policy";
  return "";
}
export function perceptionRuntime(catalog: PerceptionCatalog | null): string {
  if (!catalog) return "unavailable";
  if (catalog.switching) return "loading";
  const runtime = record(catalog.diagnostics.runtime);
  const state = str(runtime.state) || str(catalog.diagnostics.state);
  return ["unloaded", "loading", "warming", "active", "unavailable"].includes(state) ? state : "unavailable";
}
export function perceptionMetric(value: unknown, language: string, precision = 1): string {
  return finite(value) ? value.toLocaleString(language, { maximumFractionDigits: precision }) : "N/A";
}
export function perceptionReason(code: string): string {
  const keys: Record<string, string> = {
    model_needs_review: "perception.reason.review", model_runtime_unavailable: "perception.reason.runtime",
    model_not_installed: "perception.reason.notInstalled", model_verification_required: "perception.reason.verify", model_hash_mismatch: "perception.reason.hash",
    model_busy: "perception.block.busy", model_selection_unavailable: "perception.block.policy",
    model_selection_conflict: "perception.reason.conflict", navigation_config_conflict: "perception.reason.conflict",
    model_selection_motion_active: "perception.block.motion", model_selection_requires_idle: "perception.block.motion",
    model_selection_reserved: "perception.block.busy", navigation_model_stop_loops_first: "perception.block.loop",
    navigation_model_requires_idle_disarmed: "perception.block.motion", navigation_goal_state_unavailable: "perception.block.stale", model_selection_update_required: "perception.reason.update",
    perception_selection_unsupported: "perception.reason.update", offline: "perception.block.offline",
  };
  return code.includes("conflict") ? "perception.reason.conflict" : keys[code] ?? "perception.reason.failed";
}

export function perceptionSelectionKey(catalog: PerceptionCatalog | null): string {
  return JSON.stringify([catalog?.provider, catalog?.pipeline_revision, catalog?.local_engine, catalog?.selection?.model_id, catalog?.selection?.artifact_sha256, catalog?.selection?.revision]);
}

export function perceptionError(error: unknown): string {
  const code = errorCode(error);
  return code === "request_failed" && error instanceof Error && /^(perception_|model_selection_|camera_pipeline_|session_changed)/.test(error.message) ? error.message : code;
}

export function perceptionApplyNeeded(catalog: PerceptionCatalog | null, provider: string, modelId: string, selectedId: string | undefined): boolean {
  return !!catalog && (provider !== catalog.provider || modelId !== selectedId || provider === "venom" && (!catalog.selection?.artifact_sha256 || ["unloaded", "unavailable"].includes(perceptionRuntime(catalog))));
}


export function perceptionInstallAction(model: PerceptionModel | undefined): "start" | "cancel" | null {
  if (!model || ["needs_review", "unavailable"].includes(model.installation)) return null;
  if (model.job.id && ["queued", "waiting", "running", "downloading", "verifying", "cancelling"].includes(str(model.job.state))) return "cancel";
  return model.installation !== "ready" ? "start" : null;
}
