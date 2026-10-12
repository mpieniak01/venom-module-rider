"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { getApiBaseUrl } from "@/lib/env";
import { configForConnection } from "./device-session";
import { HoldController, readHoldTiming, type MovementIntent } from "./hold-controller";
import { cameraOn, canRequestArm, commandAccepted, controlIdentity, data, errorCode, faceRunning, featureRows, finite, record, safetyOf, settingsOf, str, timestampMs, type DeviceConfig, type Direction, type JsonRecord, type RiderStatus, type Section } from "./model";

const BASE = "/api/v1/rider";
export type Notice = { key: string; code?: string; tone: "error" | "success" | "info" };
export type Frame = { url: string; stale: boolean; source: string; fileAt: string; id: string; knownFreshness: boolean };
type Clock = { offset: number; rtt: number; at: number };
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
  const [cameraStream, setCameraStream] = useState<"raw" | "proc" | null>(null);
  const [showRuntimeFrame, setShowRuntimeFrame] = useState(false);
  const statusRef = useRef<RiderStatus | null>(null);
  const configRef = useRef<DeviceConfig | null>(null);
  const clockRef = useRef<Clock | null>(null);
  const seqRef = useRef(0);
  const motionBlocked = useRef(0);
  const sessionRef = useRef("");
  const sectionRef = useRef(section);
  const mediaRef = useRef<{ camera: "raw" | "proc" | null; face: boolean }>({ camera: null, face: false });
  const pollPromise = useRef<Promise<void> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const commandRef = useRef<(body: JsonRecord) => Promise<unknown>>(async () => undefined);
  const isConnected = useCallback(() => ["online", "degraded"].includes(statusRef.current?.connection.status ?? ""), []);
  const hasFreshControl = useCallback(() => {
    const clock = clockRef.current;
    const timing = readHoldTiming(settingsOf(statusRef.current).control_modes);
    const safety = safetyOf(statusRef.current);
    const observed = timestampMs(safety.observed_at);
    const cadence = statusRef.current?.polling[`${sectionRef.current}_ms`] ?? 0;
    const freshness = timing ? Math.max(timing.hold_timeout_ms * 2, cadence + timing.hold_timeout_ms) : 0;
    return !!clock && !!timing && observed !== null && safety.stale !== true && clock.rtt < timing.request_ttl_ms && Date.now() - clock.at < freshness && Date.now() + clock.offset - observed < freshness;
  }, []);
  const movementReady = useCallback(() => {
    const safety = safetyOf(statusRef.current);
    return motionBlocked.current === 0 && isConnected() && hasFreshControl() && safety.state === "ARMED" && safety.owner === "manual" && safety.ready === true && safety.can_move === true && safety.panel_motion_enabled === true && record(record(safety.capabilities).move).available === true && !!str(safety.generation) && Number.isInteger(settingsOf(statusRef.current).revision);
  }, [hasFreshControl, isConnected]);
  const [hold] = useState(() => new HoldController({
    timing: () => readHoldTiming(settingsOf(statusRef.current).control_modes),
    canMove: movementReady,
    send: async (intent: MovementIntent) => { await commandRef.current({ action: "move", device_settings: true, ...intent }); },
    onActive: setActiveDirection,
    onError: (error) => setNotice({ key: errorCode(error) === "hold_timed_out" ? "holdExpired" : "requestFailed", code: errorCode(error), tone: "error" }),
  }));
  const replaceFrame = useCallback((kind: "camera" | "face", next: Frame | null) => {
    const setter = kind === "camera" ? setCamera : setRuntimeFrame;
    setter(previous => { if (previous) URL.revokeObjectURL(previous.url); return next; });
  }, []);
  const fetchFrame = useCallback(async (path: string, signal: AbortSignal, kind: "camera" | "face") => {
    const response = await fetch(`${getApiBaseUrl()}${BASE}${path}`, { cache: "no-store", signal });
    if (!response.ok || !response.headers.get("Content-Type")?.startsWith("image/")) { replaceFrame(kind, null); setMediaError("frameMissing"); return; }
    const stale = response.headers.get("X-Rider-Stale");
    const placeholder = response.headers.get("X-Rider-Placeholder") === "true";
    if (stale === "true" || placeholder) { replaceFrame(kind, null); setMediaError("frameStale"); return; }
    const blob = await response.blob();
    if (signal.aborted) return;
    replaceFrame(kind, { url: URL.createObjectURL(blob), stale: false, knownFreshness: stale === "false", source: response.headers.get("X-Rider-Source") || response.headers.get("X-Face-Source") || "", fileAt: response.headers.get("X-Rider-File-Observed-At") || "", id: response.headers.get("X-Rider-Frame-Id") || "" });
    setMediaError(null);
  }, [replaceFrame]);
  const poll = useCallback(async (target?: Section) => {
    while (pollPromise.current) { await pollPromise.current; if (!target) return; }
    const requestedSection = target ?? sectionRef.current;
    const abort = new AbortController(); abortRef.current = abort;
    const task = async () => {
      try {
        if (!configRef.current) {
          const nextConfig = await apiFetch<DeviceConfig>(`${BASE}/config`, { signal: abort.signal });
          configRef.current = nextConfig; sessionRef.current = nextConfig.session; setConfig(nextConfig);
        }
        const started = Date.now();
        const next = await apiFetch<RiderStatus>(`${BASE}/status?section=${requestedSection}`, { signal: abort.signal });
        const received = Date.now();
        if (abort.signal.aborted) return;
        const nextConfig = await configForConnection(configRef.current!, next.connection, () => apiFetch<DeviceConfig>(`${BASE}/config`, { signal: abort.signal }));
        if (abort.signal.aborted) return;
        const previous = statusRef.current;
        if (previous && controlIdentity(previous) !== controlIdentity(next)) { hold.cancel(); seqRef.current = 0; replaceFrame("camera", null); replaceFrame("face", null); }
        if (!["online", "degraded"].includes(next.connection.status)) { hold.cancel(); replaceFrame("camera", null); replaceFrame("face", null); }
        if (sessionRef.current && sessionRef.current !== next.connection.session) { hold.cancel(); setNotice({ key: "conflict", tone: "info" }); }
        sessionRef.current = next.connection.session;
        // A command may explicitly refresh control while another view remains visible.
        const view = requestedSection !== sectionRef.current && previous?.connection.session === next.connection.session
          ? { ...previous, connection: next.connection, control: next.control, settings: next.settings, errors: { ...previous.errors, ...next.errors } }
          : next;
        statusRef.current = view;
        setStatus(view);
        const server = timestampMs(data(next.control).server_time);
        clockRef.current = server === null ? null : { offset: server - (started + received) / 2, rtt: received - started, at: received };
        const last = safetyOf(next).last_seq;
        if (finite(last)) seqRef.current = Math.max(seqRef.current, last);
        if (configRef.current !== nextConfig) { configRef.current = nextConfig; setConfig(nextConfig); }
        if (requestedSection === sectionRef.current && ["online", "degraded"].includes(next.connection.status)) {
          if (requestedSection === "control" && mediaRef.current.camera) {
            if (cameraOn(next)) await fetchFrame(`/camera/${mediaRef.current.camera}`, abort.signal, "camera");
            else replaceFrame("camera", null);
          }
          if (requestedSection === "face" && mediaRef.current.face) {
            if (faceRunning(next)) await fetchFrame("/face/runtime/frame", abort.signal, "face");
            else replaceFrame("face", null);
          }
        }
      } catch (error) {
        if (abort.signal.aborted) return;
        hold.cancel(); clockRef.current = null;
        const previous = statusRef.current;
        if (previous) { const offline = { ...previous, connection: { ...previous.connection, status: "offline" as const } }; statusRef.current = offline; setStatus(offline); }
        replaceFrame("camera", null); replaceFrame("face", null);
        setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" });
      } finally { if (!abort.signal.aborted) setLoading(false); }
    };
    pollPromise.current = task().finally(() => { pollPromise.current = null; if (abortRef.current === abort) abortRef.current = null; });
    return pollPromise.current;
  }, [fetchFrame, hold, replaceFrame]);
  const mutation = useCallback(async (path: string, body: JsonRecord = {}, method = "POST", quiet = false): Promise<JsonRecord> => {
    const session = sessionRef.current;
    if ((!session && !(path === "/control" && body.action === "stop")) || (!isConnected() && path !== "/control" && path !== "/config")) throw Object.assign(new Error("offline"), { data: { error: "offline" } });
    try {
      const response = await apiFetch<JsonRecord>(`${BASE}${path}`, { method, headers: { "X-Rider-Session": session }, body: method === "DELETE" ? undefined : JSON.stringify(body) });
      if (!commandAccepted(response)) { const rejected = new Error(str(data(response).error) || "command_rejected"); Object.assign(rejected, { data: response }); throw rejected; }
      if (session !== sessionRef.current && path !== "/config") throw new Error("session_changed");
      if (!quiet) setNotice({ key: "saved", tone: "success" });
      return data(response);
    } catch (error) { if (!quiet) setNotice({ key: errorCode(error) === "pi_operation_timeout" ? "operationUnconfirmed" : errorCode(error).includes("conflict") ? "conflict" : "requestFailed", code: errorCode(error), tone: "error" }); throw error; }
  }, [isConnected]);
  const makeEnvelope = useCallback((body: JsonRecord, source = "manual"): JsonRecord => {
    const action = str(body.action);
    const stopping = action === "stop" || action === "disarm";
    if (!stopping && (!isConnected() || !hasFreshControl())) throw Object.assign(new Error("stale_control"), { data: { error: "stale_control" } });
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
  const sendControl = useCallback(async (body: JsonRecord): Promise<JsonRecord> => {
    const envelope = makeEnvelope(body);
    const response = await mutation("/control", envelope, "POST", true);
    setNotice({ key: response.pending === true || record(response.operation).status === "pending" ? "commandPending" : "commandAccepted", tone: "info" });
    return response;
  }, [makeEnvelope, mutation]);
  useEffect(() => { commandRef.current = sendControl; }, [sendControl]);
  const command = useCallback(async (body: JsonRecord) => {
    const key = str(body.action); if (key !== "move") hold.cancel();
    const safetyAction = ["arm", "clear_estop", "stop", "disarm"].includes(key);
    if (safetyAction) motionBlocked.current += 1;
    setBusy(key);
    try { if (key === "arm" || key === "clear_estop") await poll("control"); await sendControl(body); if (key !== "move") await poll(); }
    catch (error) { hold.cancel(); setNotice({ key: errorCode(error).includes("stale") ? "staleControl" : "requestFailed", code: errorCode(error), tone: "error" }); }
    finally { if (safetyAction) motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(null); }
  }, [hold, poll, sendControl]);
  const runMutation = useCallback(async (path: string, body: JsonRecord = {}, method = "POST") => {
    setBusy(path);
    try {
      let request = body;
      if (path.startsWith("/goal/") && body.enabled === true) {
        const name = decodeURIComponent(path.slice("/goal/".length));
        const goal = featureRows(statusRef.current).find(item => item.name === name);
        if (goal?.can_move === true) {
          await poll("control");
          request = makeEnvelope(body, str(safetyOf(statusRef.current).owner));
        }
      }
      const result = await mutation(path, request, method); await poll(); return result;
    }
    catch (error) { const code = errorCode(error); setNotice({ key: code === "pi_operation_timeout" ? "operationUnconfirmed" : "requestFailed", code, tone: "error" }); if (code === "pi_operation_timeout") await poll(); return null; }
    finally { setBusy(null); }
  }, [makeEnvelope, mutation, poll]);
  const selectDevice = useCallback(async (device_id: string | null) => {
    hold.release(); motionBlocked.current += 1; setBusy("/config");
    abortRef.current?.abort();
    if (pollPromise.current) await pollPromise.current;
    try {
      const result = await mutation("/config", { device_id }, "POST", true);
      const next = result as unknown as DeviceConfig;
      configRef.current = next; sessionRef.current = next.session; setConfig(next); statusRef.current = null; clockRef.current = null; setStatus(null); seqRef.current = 0;
      replaceFrame("camera", null); replaceFrame("face", null); setNotice(null); await poll();
    } catch (error) { setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" }); }
    finally { motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(null); }
  }, [hold, mutation, poll, replaceFrame]);
  useEffect(() => {
    sectionRef.current = section; mediaRef.current = { camera: cameraStream, face: showRuntimeFrame };
    hold.release();
    if (section !== "control" || !cameraStream) replaceFrame("camera", null);
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
      if (document.hidden) { hold.release(); abortRef.current?.abort(); }
      else void tick();
    };
    const blur = () => { hold.release(); };
    document.addEventListener("visibilitychange", visibility); window.addEventListener("blur", blur);
    void tick();
    return () => { alive = false; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); window.removeEventListener("blur", blur); hold.release(); abortRef.current?.abort(); };
  }, [cameraStream, hold, poll, replaceFrame, section, showRuntimeFrame]);
  useEffect(() => () => { hold.release(); abortRef.current?.abort(); }, [hold]);
  // ARM always refreshes the fast control endpoint before making its envelope.
  // The latency of a diagnostics view must not prevent that refresh request.
  const canArm = canRequestArm(status);
  return { config, status, loading, notice, setNotice, busy, activeDirection, canMutate: isConnected(), canArm, canMove: movementReady(), poll, command, mutate: runMutation, selectDevice, hold, camera, cameraStream, setCameraStream, runtimeFrame, showRuntimeFrame, setShowRuntimeFrame, mediaError };
}
export type RiderClient = ReturnType<typeof useRider>;
