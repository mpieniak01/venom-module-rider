import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { test } from "node:test";
import ts from "typescript";
import { data, errorCode, record } from "../../web-next/lib/model";
import { readCameraPipeline } from "../../web-next/lib/camera-pipeline";

const fixture = JSON.parse(fs.readFileSync(new URL("../../config/contracts/camera_pipeline_v1.examples.json", import.meta.url), "utf8")).stopped_status;
const compiled = ts.transpileModule(fs.readFileSync(new URL("../../web-next/lib/use-camera-pipeline.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

test("revision recovery reads immediately while holding the mutation lock", { timeout: 3000 }, async () => {
  const calls: Array<Record<string, unknown>> = [];
  let reads = 0, release: (() => void) | undefined;
  let recoveryStarted: (() => void) | undefined;
  const started = new Promise<void>(resolve => { recoveryStarted = resolve; });
  const held = new Promise<void>(resolve => { release = resolve; });
  const apiFetch = async () => {
    reads++;
    if (reads === 2) { recoveryStarted?.(); await held; }
    return { ...fixture, revision: reads === 1 ? 1 : 2 };
  };
  const exports: Record<string, unknown> = {};
  const react = {
    useCallback: <T>(callback: T) => callback,
    useEffect: () => undefined,
    useRef: <T>(value: T) => ({ current: value }),
    useState: <T>(value: T) => [value, () => undefined],
  };
  vm.runInNewContext(compiled, {
    exports, performance, AbortController, setTimeout, clearTimeout, crypto: globalThis.crypto,
    require: (name: string) => {
      if (name === "react") return react;
      if (name === "@/lib/api-client") return { apiFetch };
      if (name === "./model") return { data, errorCode, record };
      if (name === "./camera-pipeline") return { readCameraPipeline };
      throw new Error("unexpected dependency " + name);
    },
  });
  const create = exports.useCameraPipeline as (
    section: string, session: string, mutate: (path: string, body: Record<string, unknown>) => Promise<unknown>, invalidate: () => void
  ) => { refresh(): Promise<void>; change(action: string): Promise<void> };
  const hook = create("control", "fixture-session", async (_path, body) => {
    calls.push(body);
    if (calls.length === 1) throw new Error("camera_pipeline_revision_conflict");
    return { ...fixture, revision: 2, state: "active" };
  }, () => undefined);
  await hook.refresh();
  const failed = hook.change("start");
  const rejection = assert.rejects(failed, /camera_pipeline_revision_conflict/);
  await started;
  await hook.change("start");
  assert.equal(calls.length, 1, "another mutation entered during recovery");
  release?.();
  await rejection;
  assert.equal(reads, 2, "recovery was skipped behind its own lock");
  await hook.change("start");
  assert.equal(calls[1].expected_revision, 2, "retry used the rejected revision");
});
