import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { canSelectExecutor, computeAge, computeError, computeQuality, computeState, computeView, executorSelection } from "../../web-next/lib/compute";
import { translate } from "../../web-next/lib/i18n";

const snapshot = {
  connection: { session: "panel-session" },
  compute: {
    host: {
      tasks: {
        "text.respond": { available: true, installed: true, configured: true, ready: false, runtime: { provider: "ollama", model: "scene-model" } },
        arm: { available: true },
      },
      queue: { running: false, pending: 0 },
      metrics: { succeeded: 0, failed: 0 },
      jobs: [{ job_id: "first", task: "text.respond" }, { job_id: "second", task: "audio.transcribe" }],
    },
    pi: { selected_executor: "venom", domains: { vision: { state: "succeeded", data: { text: "A wall" }, metrics: { source_age_ms: 250 } } } },
    config: { protocol_version: 1, revision: 7, device_id: "demo", selected_executor: "venom", executors: [{ id: "venom", credential_configured: true }, { id: "unconfigured", credential_configured: false }] },
  },
};

test("compute projection exposes bounded tasks and separate installed/ready states", () => {
  const view = computeView(snapshot);
  assert.deepEqual(view.tasks.map(task => task.task), ["text.respond", "audio.transcribe", "audio.synthesize", "vision.describe", "conversation.respond"]);
  assert.equal(view.tasks[0].installed, true);
  assert.equal(view.tasks[0].ready, false);
  assert.equal(view.tasks[0].available, true);
  assert.equal(view.queue.pending, 0);
  assert.equal(view.metrics.succeeded, 0);
  assert.equal(view.domains[0].name, "vision");
  assert.equal(view.jobs[0].job_id, "second");
  assert.equal(view.selected, "venom");
});

test("absent or incompatible compute remains unavailable and cannot mutate", () => {
  for (const status of [null, {}, { compute: { config: { protocol_version: 2, revision: 1 } } }]) {
    const view = computeView(status);
    assert.equal(view.configured, false);
    assert.equal(canSelectExecutor(view, ""), false);
    assert.equal(executorSelection(view, "venom"), null);
  }
  const view = computeView({ compute: { config: { protocol_version: 1, revision: null } } });
  assert.equal(view.revision, null);
  assert.equal(canSelectExecutor(view, ""), false);
});

test("executor selection carries authoritative revision and no credentials or endpoint", () => {
  const view = computeView(snapshot);
  assert.deepEqual(executorSelection(view, "venom"), { executor_id: "venom", revision: 7 });
  assert.deepEqual(executorSelection(view, ""), { executor_id: null, revision: 7 });
  assert.equal(executorSelection(view, "unconfigured"), null);
  assert.equal(executorSelection(view, "invented"), null);
  assert.notEqual(view.identity, computeView({ ...snapshot, connection: { session: "another" } }).identity);
  assert.notEqual(view.identity, computeView({ ...snapshot, compute: { ...snapshot.compute, config: { ...snapshot.compute.config, revision: 8 } } }).identity);
});

test("missing capture age stays unknown and never uses browser receipt time", () => {
  for (const value of [undefined, null, -1, Number.NaN, "100"]) assert.equal(computeAge({ metrics: { source_age_ms: value } }), null);
  assert.equal(computeAge({ captured_at: 1, received_at: Date.now() }), null);
  assert.equal(computeAge({ metrics: { source_age_ms: 0 } }), 0);
  assert.equal(computeAge({ metrics: { source_age_ms: 8.33 } }), 8.33);
  assert.equal(computeQuality({ state: "succeeded", metrics: {} }), "unknown");
  assert.equal(computeQuality({ state: "succeeded", metrics: { source_age_ms: 0 } }), "fresh");
  assert.equal(computeQuality({ state: "succeeded", quality: { timestamp_source: "unknown" }, metrics: { source_age_ms: 0 } }), "unknown");
  assert.equal(computeQuality({ state: "succeeded", stale: true, metrics: { source_age_ms: 0 } }), "stale");
  assert.equal(computeQuality({ state: "expired" }), "stale");
  assert.equal(computeQuality({ state: "superseded" }), "stale");
  assert.equal(computeQuality({ state: "succeeded", quality: { simulated: true }, metrics: { source_age_ms: 0 } }), "demo");
});

test("state and error labels preserve explicit failures without invented success", () => {
  assert.equal(computeError({ code: "compute_saturated" }), "compute_saturated");
  assert.equal(computeError("engine_inference_failed"), "engine_inference_failed");
  assert.equal(computeError({}), "");
  assert.equal(computeState("failed"), "failed");
  assert.equal(computeState("invented_success"), "unknown");
  assert.equal(computeState(undefined), "unknown");
  for (const state of ["queued", "running", "succeeded", "failed", "cancelled", "expired", "superseded", "off", "idle", "ready", "connecting", "waiting", "error", "unavailable", "unknown"]) {
    for (const language of ["pl", "en", "de"] as const) assert.notEqual(translate(language, `compute.state.${state}`), `compute.state.${state}`);
  }
});

test("new view is observational on mount and uses one deliberate executor mutation", () => {
  const source = fs.readFileSync(new URL("../../web-next/components/compute.tsx", import.meta.url), "utf8");
  assert.equal(/\b(?:fetch|apiFetch|useEffect|setInterval)\s*\(/.test(source), false);
  assert.equal(source.includes('client.mutate("/compute/executor", body)'), true);
  assert.equal(/client\.(?:command|hold)|\/(?:control|goal|camera|face\/runtime\/play)/.test(source), false);
  assert.equal(source.includes('href="/models"'), true);
  assert.equal(source.includes("navigation_clearance"), false);
  for (const language of ["pl", "en", "de"] as const) {
    assert.notEqual(translate(language, "computeVisionHint"), "computeVisionHint");
    assert.notEqual(translate(language, "s12_venom_vision"), "s12_venom_vision");
    assert.notEqual(translate(language, "s13_venom_voice"), "s13_venom_voice");
  }
});
