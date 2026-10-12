import { finite, record, rows, str, type JsonRecord } from "./model";

export const COMPUTE_TASKS = ["text.respond", "audio.transcribe", "audio.synthesize", "vision.describe", "conversation.respond"] as const;
export type ComputeTask = typeof COMPUTE_TASKS[number];

export function computeView(status: unknown) {
  const compute = record(record(status).compute);
  const host = record(compute.host), pi = record(compute.pi), config = record(compute.config);
  return {
    host, pi, config,
    configured: config.protocol_version === 1,
    selected: str(config.selected_executor ?? pi.selected_executor),
    revision: finite(config.revision) ? config.revision : null,
    identity: [record(record(status).connection).session, config.device_id, config.revision].join(":"),
    executors: rows(config.executors),
    tasks: COMPUTE_TASKS.map((task): JsonRecord & { task: ComputeTask } => ({ ...record(record(host.tasks)[task]), task })),
    domains: ["vision", "voice"].map((name): JsonRecord & { name: string } => ({ ...record(record(pi.domains)[name]), name })),
    queue: record(host.queue),
    metrics: record(host.metrics),
    jobs: rows(host.jobs).slice(-6).reverse(),
    error: computeError(compute.error),
  };
}

export function computeError(value: unknown): string {
  return str(record(value).code) || str(value);
}

export function computeState(value: unknown): string {
  const state = str(value);
  return ["queued", "running", "succeeded", "failed", "cancelled", "expired", "superseded", "off", "idle", "ready", "connecting", "waiting", "error", "unavailable"].includes(state) ? state : "unknown";
}

export function computeAge(observation: JsonRecord): number | null {
  const value = record(observation.metrics).source_age_ms;
  return finite(value) && value >= 0 ? value : null;
}

export function computeQuality(observation: JsonRecord): "demo" | "stale" | "fresh" | "unknown" {
  if (record(observation.quality).simulated === true) return "demo";
  if (observation.stale === true || ["expired", "superseded"].includes(str(observation.state))) return "stale";
  if (record(observation.quality).timestamp_source === "unknown") return "unknown";
  if (observation.state === "succeeded" && computeAge(observation) !== null) return "fresh";
  return "unknown";
}

export function canSelectExecutor(view: ReturnType<typeof computeView>, value: string): boolean {
  return view.configured && view.revision !== null && (value === "" || view.executors.some(executor => executor.id === value && executor.credential_configured === true));
}

export function executorSelection(view: ReturnType<typeof computeView>, value: string): JsonRecord | null {
  return canSelectExecutor(view, value) ? { executor_id: value || null, revision: view.revision } : null;
}
