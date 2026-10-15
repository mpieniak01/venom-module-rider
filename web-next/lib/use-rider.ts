"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { getApiBaseUrl } from "@/lib/env";
import { readAnalysis } from "./vision-frame";
import { configForConnection } from "./device-session";
import { HoldController, readHoldTiming, type MovementIntent } from "./hold-controller";
import { canRequestArm, commandAccepted, controlIdentity, data, errorCode, faceRunning, featureRows, finite, frameRequested, record, safetyOf, settingsOf, str, type DeviceConfig, type Direction, type JsonRecord, type RiderStatus, type Section } from "./model";

const BASE = "/api/v1/rider";
export type Notice = { key: string; code?: string; tone: "error" | "success" | "info" };
export type Frame = { analysis?: JsonRecord; ageMs?: number; receivedAt?: number; ageSource?: string; decoded?: boolean; url: string; stale: boolean; source: string; fileAt: string; id: string; knownFreshness: boolean; visionState: string; visionReason: string; inferenceMs: string; visionPolicy: string; recordedAt: string };
import { controlChanged, freshControl, navigationAvailable, navigationView, observationClock, retainControl, type ControlClock } from "./navigation";
export function useRider(section: Section) {
  const [config, setConfig] = useState<DeviceConfig | null>(null);
  const [status, setStatus] = useState<RiderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [activeDirection, setActiveDirection] = useState<Direction | null>(null);
  const [camera, setCamera] = useState<Frame | null>(null);
  const [runtimeFrame, setRuntimeFrame] = useState<Frame | null>(null);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [cameraStream, setCameraStream] = useState<"raw" | "proc" | "venom" | null>(null);
  const [showRuntimeFrame, setShowRuntimeFrame] = useState(false);
  const statusRef = useRef<RiderStatus | null>(null);
  const cameraRef = useRef<Frame | null>(null);
  useEffect(() => { cameraRef.current = camera; }, [camera]);
  const configRef = useRef<DeviceConfig | null>(null);
  const clockRef = useRef<ControlClock | null>(null);
  const seqRef = useRef(0);
  const motionBlocked = useRef(0);
  const motionEpoch = useRef(0);
  const sessionRef = useRef("");
  const sectionRef = useRef(section);
  const mediaRef = useRef<{ camera: "raw" | "proc" | "venom" | null; face: boolean }>({ camera: null, face: false });
  const pollPromise = useRef<Promise<void> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const controlPollPromise = useRef<Promise<void> | null>(null);
  const controlAbortRef = useRef<AbortController | null>(null);
  const commandRef = useRef<(body: JsonRecord, current?: () => boolean) => Promise<unknown>>(async () => undefined);
  const abortReads = useCallback(() => { abortRef.current?.abort(); controlAbortRef.current?.abort(); }, []);
  const isConnected = useCallback(() => ["online", "degraded"].includes(statusRef.current?.connection.status ?? ""), []);
  const hasFreshControl = useCallback(() => freshControl(statusRef.current, clockRef.current, Date.now(), statusRef.current?.polling[`${sectionRef.current}_ms`] ?? 0), []);
  const canNavigate = useCallback(() => motionBlocked.current === 0 && navigationAvailable(statusRef.current), []);
  const movementReady = canNavigate;
  const [hold] = useState(() => new HoldController({
    timing: () => readHoldTiming(settingsOf(statusRef.current).control_modes),
    canMove: canNavigate,
    send: async (intent: MovementIntent, current: () => boolean) => { await commandRef.current({ action: "move", device_settings: true, ...intent }, current); },
    onActive: setActiveDirection,
    onError: (error) => setNotice({ key: errorCode(error) === "hold_timed_out" ? "holdExpired" : "requestFailed", code: errorCode(error), tone: "error" }),
  }));
  const replaceFrame = useCallback((kind: "camera" | "face", next: Frame | null, retain = false) => {
    const setter = kind === "camera" ? setCamera : setRuntimeFrame;
    setter(previous => { if (retain && kind === "camera" && !next && previous) return previous.stale ? previous : { ...previous, stale: true, knownFreshness: false }; if (previous) URL.revokeObjectURL(previous.url); return next; });
  }, []);
  const markCameraDecoded = useCallback((url: string, decoded: boolean) => {
    setCamera(previous => previous?.url === url && previous.decoded !== decoded ? { ...previous, decoded } : previous);
  }, []);
  const fetchFrame = useCallback(async (path: string, signal: AbortSignal, kind: "camera" | "face") => {
    const response = await fetch(`${getApiBaseUrl()}${BASE}${path}`, { cache: "no-store", signal });
    if (signal.aborted || !frameRequested(kind, path, sectionRef.current, mediaRef.current)) return;
    if (!response.ok || !response.headers.get("Content-Type")?.startsWith("image/")) {
      let code = "";
      try { code = String(record(record(await response.json()).detail).code ?? ""); } catch { /* Non-JSON frame failure. */ }
      replaceFrame(kind, null, kind === "camera");
      setMediaError(code === "camera_off" ? "visionCameraOff" : code === "vision_busy" ? "visionBusy" : path === "/camera/venom" ? "visionPreviewUnavailable" : "frameMissing");
      return;
    }
    const stale = response.headers.get("X-Rider-Stale");
    const placeholder = response.headers.get("X-Rider-Placeholder") === "true";
    if (stale === "true" || placeholder) { replaceFrame(kind, null, kind === "camera"); setMediaError("frameStale"); return; }
    const blob = await response.blob();
    if (signal.aborted || !frameRequested(kind, path, sectionRef.current, mediaRef.current)) return;
    replaceFrame(kind, { analysis: readAnalysis(response.headers.get("X-Rider-Analysis-Json")), ageMs: response.headers.has("X-Rider-Frame-Age-Ms") ? Number(response.headers.get("X-Rider-Frame-Age-Ms")) : undefined, receivedAt: performance.now(), ageSource: response.headers.get("X-Rider-Frame-Age-Source") ?? undefined, decoded: false, url: URL.createObjectURL(blob), stale: false, knownFreshness: stale === "false", source: response.headers.get("X-Rider-Source") || response.headers.get("X-Face-Source") || "", fileAt: response.headers.get("X-Rider-File-Observed-At") || "", id: response.headers.get("X-Rider-Frame-Id") || "", visionState: response.headers.get("X-Rider-Vision-State") || "", visionReason: response.headers.get("X-Rider-Vision-Reason") || "", inferenceMs: response.headers.get("X-Rider-Vision-Inference-Ms") || "", visionPolicy: response.headers.get("X-Rider-Vision-Policy") || "", recordedAt: response.headers.get("X-Rider-Recorded-At") || "" });
    setMediaError(null);
  }, [replaceFrame]);
  const poll = useCallback(async (target?: Section, fast = false) => {
    const pending = fast ? controlPollPromise : pollPromise;
    const controller = fast ? controlAbortRef : abortRef;
    while (pending.current) { await pending.current; if (!target || fast) return; }
    const requestedSection = target ?? sectionRef.current;
    const abort = new AbortController(); controller.current = abort;
    const timeout = fast ? setTimeout(() => abort.abort(), readHoldTiming(settingsOf(statusRef.current).control_modes)?.request_ttl_ms ?? 500) : null;
    const task = async () => {
      try {
        if (!configRef.current) {
          const nextConfig = await apiFetch<DeviceConfig>(`${BASE}/config`, { signal: abort.signal });
          configRef.current = nextConfig; sessionRef.current = nextConfig.session; setConfig(nextConfig);
        }
        const started = Date.now();
        let next = await apiFetch<RiderStatus>(`${BASE}/status?section=${fast ? "navigation" : requestedSection}`, { signal: abort.signal });
        const received = Date.now();
        if (abort.signal.aborted) return;
        const nextConfig = await configForConnection(configRef.current!, next.connection, () => apiFetch<DeviceConfig>(`${BASE}/config`, { signal: abort.signal }));
        if (abort.signal.aborted) return;
        const previous = statusRef.current;
        const observed = next;
        next = retainControl(previous, next);
        if (controlChanged(previous, next)) { hold.cancel(); seqRef.current = 0; replaceFrame("camera", null, previous?.connection.session===next.connection.session && previous?.connection.device_id===next.connection.device_id); replaceFrame("face", null); if (previous?.connection.session !== next.connection.session) { mediaRef.current = { camera: null, face: false }; setCameraStream(null); setShowRuntimeFrame(false); } }
        if (!["online", "degraded"].includes(next.connection.status)) { replaceFrame("camera", null); replaceFrame("face", null); }
        if (next.control && !next.errors.control && !navigationAvailable(next)) hold.cancel();
        if (sessionRef.current && sessionRef.current !== next.connection.session) { hold.cancel(); setNotice({ key: "conflict", tone: "info" }); }
        sessionRef.current = next.connection.session;
        // A command may explicitly refresh control while another view remains visible.
        const view = (fast || requestedSection !== sectionRef.current) && previous?.connection.session === next.connection.session
          ? navigationView(previous, next)
          : next;
        statusRef.current = view;
        setStatus(view);
        if (next.control === observed.control) clockRef.current = observationClock(clockRef.current, next, started, received);
        const last = safetyOf(next).last_seq;
        if (finite(last)) seqRef.current = Math.max(seqRef.current, last);
        if (configRef.current !== nextConfig) { configRef.current = nextConfig; setConfig(nextConfig); }
        if (!fast && requestedSection === sectionRef.current && ["online", "degraded"].includes(next.connection.status)) {
          if (requestedSection === "face" && mediaRef.current.face) {
            if (faceRunning(next)) await fetchFrame("/face/runtime/frame", abort.signal, "face");
            else replaceFrame("face", null);
          }
        }
      } catch (error) {
        if (abort.signal.aborted) return;
        const previous = statusRef.current;
        if (previous) { const offline = { ...previous, connection: { ...previous.connection, status: "offline" as const } }; statusRef.current = offline; setStatus(offline); }
        replaceFrame("camera", null); replaceFrame("face", null);
        setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" });
      } finally { if (timeout) clearTimeout(timeout); if (!abort.signal.aborted) setLoading(false); }
    };
    pending.current = task().finally(() => { pending.current = null; if (controller.current === abort) controller.current = null; });
    return pending.current;
  }, [fetchFrame, hold, replaceFrame]);
  const mutation = useCallback(async (path: string, body: JsonRecord = {}, method = "POST", quiet = false): Promise<JsonRecord> => {
    const session = sessionRef.current;
    if ((!session && !(path === "/control" && body.action === "stop")) || (!isConnected() && path !== "/control" && path !== "/config")) throw Object.assign(new Error("offline"), { data: { error: "offline" } });
    try {
      const response = await apiFetch<JsonRecord>(`${BASE}${path}`, { method, headers: { "X-Rider-Session": session }, body: method === "DELETE" ? undefined : JSON.stringify(body) });
      if (!commandAccepted(response)) { const rejected = new Error(str(data(response).error) || "command_rejected"); Object.assign(rejected, { data: response }); throw rejected; }
      if (session !== sessionRef.current && path !== "/config") throw new Error("session_changed");
      if (!quiet) setNotice({ key: path === "/conversation/config" ? "conversationConfigured" : "saved", tone: "success" });
      return data(response);
    } catch (error) { if (!quiet) setNotice({ key: errorCode(error) === "pi_operation_timeout" ? "operationUnconfirmed" : errorCode(error).includes("conflict") ? "conflict" : "requestFailed", code: errorCode(error), tone: "error" }); throw error; }
  }, [isConnected]);
  const makeEnvelope = useCallback((body: JsonRecord, source = "manual"): JsonRecord => {
    const action = str(body.action);
    const stopping = action === "stop" || action === "disarm";
    const manualIntent = ["move", "height", "balance", "arm", "clear_estop"].includes(action);
    if (!stopping && (manualIntent ? !clockRef.current || !str(safetyOf(statusRef.current).generation) : !isConnected() || !hasFreshControl())) throw Object.assign(new Error("stale_control"), { data: { error: "stale_control" } });
    const current = statusRef.current;
    const safety = safetyOf(current);
    const settings = settingsOf(current);
    const timing = readHoldTiming(settings.control_modes);
    const clock = clockRef.current;
    const envelope: JsonRecord = { ...body, request_id: crypto.randomUUID(), source, execution_context: "panel" };
    if (!stopping) {
      seqRef.current = Math.max(seqRef.current, finite(safety.last_seq) ? safety.last_seq : 0) + 1;
      Object.assign(envelope, { generation: safety.generation, seq: seqRef.current, ts: (Date.now() + (clock?.offset ?? 0)) / 1000, ttl_ms: timing?.request_ttl_ms, settings_revision: settings.revision });
    }
    return envelope;
  }, [hasFreshControl, isConnected]);
  const sendControl = useCallback(async (body: JsonRecord, current: () => boolean = () => true, announce = true): Promise<JsonRecord> => {
    if (!current()) return {};
    const action = str(body.action);
    const stopping = ["stop", "disarm"].includes(action);
    if (["stop","disarm","clear_estop","height","balance"].includes(action) || action==="arm"&&body.owner!=="navigation" || action==="move"&&body.mode!=="release") motionEpoch.current += 1;
    const origin = controlIdentity(statusRef.current);
    const revision = settingsOf(statusRef.current).revision;
    const manualIntent = ["move", "height", "balance", "arm", "clear_estop"].includes(str(body.action));
    if (!stopping && !manualIntent && !hasFreshControl()) await poll("control", true);
    // Never send an old press after STOP, replacement, device or generation change.
    if (!current()) return {};
    if (!stopping && (origin !== controlIdentity(statusRef.current) || revision !== settingsOf(statusRef.current).revision)) throw Object.assign(new Error("stale_control"), { data: { error: "stale_control" } });
    const envelope = makeEnvelope(body);
    const response = await mutation("/control", envelope, "POST", true);
    if (announce) setNotice({ key: response.pending === true || record(response.operation).status === "pending" ? "commandPending" : "commandAccepted", tone: "info" });
    return response;
  }, [hasFreshControl, makeEnvelope, mutation, poll]);
  useEffect(() => { commandRef.current = sendControl; }, [sendControl]);
  const command = useCallback(async (body: JsonRecord, throwOnError = false, quiet = false) => {
    const key = str(body.action); if (key !== "move") hold.cancel();
    const safetyAction = ["arm", "clear_estop", "stop", "disarm"].includes(key);
    if (safetyAction) motionBlocked.current += 1;
    setBusy(key);
    try { if ((key === "arm" || key === "clear_estop") && !clockRef.current) await poll("control", true); const response = await sendControl(body, () => true, !quiet); if (key !== "move") await poll(); return response; }
    catch (error) { hold.cancel(); setNotice({ key: errorCode(error).includes("stale") ? "staleControl" : "requestFailed", code: errorCode(error), tone: "error" }); if (throwOnError) throw error; return null; }
    finally { if (safetyAction) motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(null); }
  }, [hold, poll, sendControl]);
  const stopManual = useCallback(async () => {
    if (!canNavigate()) return null;
    motionEpoch.current += 1;
    hold.cancel();
    return command({ action: "move", device_settings: true, direction: "forward", mode: "release" });
  }, [canNavigate, command, hold]);
  const startNavigationTrial = useCallback(async (plan: string, revision: number) => {
    const origin = sessionRef.current, epoch = motionEpoch.current;
    hold.cancel(); motionBlocked.current += 1; setBusy("trial");
    try {
      await poll("control", true);
      if (sessionRef.current !== origin || motionEpoch.current !== epoch || document.hidden) throw new Error("navigation_trial_cancelled");
      const envelope = makeEnvelope({action:"arm"});
      const {seq: _seq, action: _action, ...intent} = envelope;
      void _seq; void _action;
      const result = await mutation("/navigation/trial", {...intent, config_revision:revision, operator_consent:true, operator_plan:plan}, "POST", true);
      if (sessionRef.current !== origin || motionEpoch.current !== epoch) throw new Error("navigation_trial_cancelled");
      await poll("control", true);
      return result;
    } finally { motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(null); }
  }, [hold, poll, makeEnvelope, mutation]);
  const currentSession = useCallback(() => sessionRef.current, []);
  const currentMotionEpoch = useCallback(() => motionEpoch.current, []);
  const currentGeneration = useCallback(() => str(safetyOf(statusRef.current).generation), []);
  const currentCamera = useCallback(() => ({ frame: cameraRef.current, stream: mediaRef.current.camera }), []);
  const runMutation = useCallback(async (path: string, body: JsonRecord = {}, method = "POST", throwOnError = false) => {
    setBusy(path);
    try {
      let request = body;
      if (path.startsWith("/goal/") && body.enabled === true) {
        const name = decodeURIComponent(path.slice("/goal/".length));
        const goal = featureRows(statusRef.current).find(item => item.name === name);
        if (goal?.can_move === true) {
          await poll("control",true);
          request = makeEnvelope(body, str(safetyOf(statusRef.current).owner));
        }
      }
      const result = await mutation(path, request, method, throwOnError); await poll(); return result;
    }
    catch (error) { const code = errorCode(error); if(path!=="/goal/s14_venom_navigation")setNotice({ key: code === "pi_operation_timeout" ? "operationUnconfirmed" : "requestFailed", code, tone: "error" }); if (code === "pi_operation_timeout") await poll(); if (throwOnError) throw error; return null; }
    finally { setBusy(null); }
  }, [makeEnvelope, mutation, poll]);
  const takeManual = useCallback(async (plan: string) => {
    let epoch = ++motionEpoch.current;
    const origin = sessionRef.current;
    const assertCurrent = () => {
      if (sessionRef.current !== origin || motionEpoch.current !== epoch || document.hidden) throw Object.assign(new Error("navigation_trial_cancelled"), {data:{error:"navigation_trial_cancelled"}});
    };
    hold.cancel(); setBusy("takeover");
    try {
      // Keep the preview when handing motor authority back to the operator.
      assertCurrent();
      await mutation("/goal/s2_camera_preview", { enabled: true }, "POST", true);
      const goals = featureRows(statusRef.current);
      for (const name of ["s14_venom_navigation", "s15_venom_perception"]) {
        assertCurrent();
        if (goals.some(goal => goal.name === name)) await mutation("/goal/" + name, { enabled: false }, "POST", true);
      }
      await poll("control", true);
      assertCurrent();
      let safety = safetyOf(statusRef.current);
      if (safety.state === "ARMED" && safety.owner !== "manual") {
        await command({action:"disarm", owner:safety.owner}, true, true);
        epoch += 1;
        await poll("control", true);
        assertCurrent();
        safety = safetyOf(statusRef.current);
      }
      if (!(safety.state === "ARMED" && safety.owner === "manual")) {
        assertCurrent();
        await command({ action: "arm", owner: "manual", arming_mode: "operator", operator_consent: true, operator_plan: plan }, true, true);
        epoch += 1;
        assertCurrent();
      }
      setCameraStream("raw"); await poll();
    } catch(error) { setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" }); }
    finally { setBusy(null); }
  }, [hold, mutation, poll, command]);
  const selectDevice = useCallback(async (device_id: string | null) => {
    motionEpoch.current += 1;
    hold.release(); motionBlocked.current += 1; setBusy("/config");
    abortReads();
    if (controlPollPromise.current) await controlPollPromise.current;
    if (pollPromise.current) await pollPromise.current;
    try {
      const result = await mutation("/config", { device_id }, "POST", true);
      const next = result as unknown as DeviceConfig;
      configRef.current = next; sessionRef.current = next.session; setConfig(next); statusRef.current = null; clockRef.current = null; setStatus(null); seqRef.current = 0;
      mediaRef.current = { camera: null, face: false }; setCameraStream(null); setShowRuntimeFrame(false); setMediaError(null);
      replaceFrame("camera", null); replaceFrame("face", null); setNotice(next.selection_warning ? { key: next.selection_warning, tone: "info" } : null); await poll();
    } catch (error) { setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" }); }
    finally { motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(null); }
  }, [abortReads, hold, mutation, poll, replaceFrame]);
  useEffect(() => {
    sectionRef.current = section; mediaRef.current = { camera: cameraStream, face: showRuntimeFrame };
    hold.release();
    if (!["control", "vision"].includes(section) || !cameraStream) replaceFrame("camera", null);
    if (section !== "face" || !showRuntimeFrame) replaceFrame("face", null);
    let alive = true; let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      if (!alive || document.hidden) return;
      await poll();
      if (!alive || document.hidden) return;
      const interval = statusRef.current?.polling[`${section}_ms`] ?? (section === "control" ? 500 : 2000);
      timer = setTimeout(() => { void tick(); }, Math.max(250, interval));
    };
    const visibility = () => {
      if (timer) clearTimeout(timer); timer = null;
      if (document.hidden) { hold.release(); abortReads(); }
      else void tick();
    };
    const blur = () => { hold.release(); };
    document.addEventListener("visibilitychange", visibility); window.addEventListener("blur", blur);
    void tick();
    return () => { alive = false; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); window.removeEventListener("blur", blur); hold.release(); abortReads(); };
  }, [abortReads, cameraStream, hold, poll, replaceFrame, section, showRuntimeFrame]);
  const mediaSession = status?.connection.session;
  useEffect(() => {
    if (!cameraStream || !["control", "vision"].includes(section)) return;
    let alive = true, cycle = 0, timer: ReturnType<typeof setTimeout> | undefined, abort: AbortController | undefined;
    const tick = async () => {
      if (!alive || document.hidden) return;
      const current = ++cycle; abort = new AbortController();
      try { await fetchFrame("/camera/" + cameraStream, abort.signal, "camera"); }
      catch { if (!abort.signal.aborted && alive) { replaceFrame("camera", null, true); setMediaError("frameMissing"); } }
      if (alive && current === cycle && !document.hidden) timer = setTimeout(() => void tick(), 500);
    };
    const visibility = () => { cycle++; clearTimeout(timer); abort?.abort(); if (!document.hidden) void tick(); };
    document.addEventListener("visibilitychange", visibility); void tick();
    return () => { alive=false; cycle++; clearTimeout(timer); abort?.abort(); document.removeEventListener("visibilitychange", visibility); };
  }, [cameraStream, section, mediaSession, fetchFrame, replaceFrame]);
  useEffect(() => () => { hold.release(); abortReads(); }, [abortReads, hold]);
  // ARM always refreshes the fast control endpoint before making its envelope.
  // The latency of a diagnostics view must not prevent that refresh request.
  const canArm = canRequestArm(status);
  return { config, status, loading, notice, setNotice, busy, activeDirection, canMutate: isConnected(), canArm, canNavigate: canNavigate(), canMove: movementReady(), poll, command, stopManual, startNavigationTrial, currentSession, currentMotionEpoch, currentGeneration, currentCamera, mutate: runMutation, takeManual, selectDevice, hold, camera, cameraStream, setCameraStream, markCameraDecoded, runtimeFrame, showRuntimeFrame, setShowRuntimeFrame, mediaError };
}
export type RiderClient = ReturnType<typeof useRider>;
