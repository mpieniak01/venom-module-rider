"use client";
import { createBrowserUuid } from "@/lib/optional-modules/transport";
import {retainSystem} from "./system-history";
import { freshnessCadence, previewInterval } from "./loop-timing";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { getApiBaseUrl } from "@/lib/env";
import { readCameraSource, framePipelineCurrent, cameraSourceMatches, cameraPipelineIdentity, cameraPreviewStopped, nativePreview, anchoredPreviewAge, nativeAnalysisMatches } from "./camera-pipeline";
import { useCameraPipeline } from "./use-camera-pipeline";
import { readMjpeg, type MjpegPart } from "./mjpeg";
import { readAnalysis } from "./vision-frame";
import { perceptionPolicy, perceptionResultMatches, previewMatchesSelection, type PerceptionSelection } from "./perception";
import { decodePreview, nextPreviewDelay, previewLifetime, previewRequestCurrent, retainedPreview, expiredNativeCandidate } from "./preview-media";
import { configForConnection } from "./device-session";
import { adoptIntervention } from "./operator-control";
import {heightAvailable, heightInterventionAvailable, heightInterventionSupported, balanceAvailable, balanceInterventionSupported} from "./posture";
import { HoldController, readHoldTiming, type MovementIntent } from "./hold-controller";
import { canRequestArm, commandAccepted, controlIdentity, data, errorCode, faceRunning, featureRows, finite, frameRequested, record, safetyOf, settingsOf, str, type DeviceConfig, type Direction, type JsonRecord, type RiderStatus, type Section } from "./model";

/** Keep the STOP exception and short-circuit connection probe in their original order. */
function mutationUnavailable(session:string,path:string,body:JsonRecord,connected:()=>boolean):boolean {
  return (!session&&!(path==="/control"&&body.action==="stop"))||(!connected()&&path!=="/control"&&path!=="/config");
}
/** Map transport failures without changing the protocol code displayed to the operator. */
function mutationFailureKey(code:string):string {
  if(code==="pi_operation_timeout")return "operationUnconfirmed";
  return code.includes("conflict")?"conflict":"requestFailed";
}
/** A new navigation intent must omit the manual action and sequence fields. */
function navigationIntent(envelope:JsonRecord):JsonRecord {
  const intent={...envelope};
  delete intent.seq;
  delete intent.action;
  return intent;
}
/** Interpret only validated scalar camera error codes. */
function cameraFailureNotice(code:string,path:string):string {
  if(code==="camera_off")return "visionCameraOff";
  if(code==="vision_busy")return "visionBusy";
  return path==="/camera/venom"?"visionPreviewUnavailable":"frameMissing";
}
type PendingOperation={requestId:string;generation:string}|null;
/** Validate posture takeover before capturing the pending navigation operation. */
function postureTakeover(action:string,status:RiderStatus|null,panelId:string):boolean {
  const intervention=action==="height"&&heightInterventionSupported(status)||action==="balance"&&balanceInterventionSupported(status);
  if(intervention&&!(action==="balance"?balanceAvailable(status,panelId):heightInterventionAvailable(status,panelId)))throw Object.assign(new Error("stale_control"),{data:{error:"stale_control"}});
  return intervention;
}
/** Capture the first superseded operation using the established pending priority. */
function supersededOperation(action:string,heightIntervention:boolean,journey:PendingOperation,resume:PendingOperation,status:RiderStatus|null):PendingOperation {
  if(!(action==="move"||action==="navigation_pause"||heightIntervention))return null;
  return journey??resume??(safetyOf(status).owner==="navigation"?{requestId:str(record(safetyOf(status).operator_control).operation_id),generation:str(safetyOf(status).generation)}:null);
}
/** Determine atomic manual takeover without changing actuator authority. */
function atomicTakeover(body:JsonRecord,action:string,heightIntervention:boolean,status:RiderStatus|null,panelId:string,journey:PendingOperation,resume:PendingOperation):boolean {
  return heightIntervention||action==="move"&&body.mode!=="release"&&manualInterventionAvailable(status,panelId)
    &&(safetyOf(status).state==="DISARMED"||safetyOf(status).owner!=="manual"||record(safetyOf(status).panel_control).holder!==panelId||!!journey||!!resume);
}
/** Preserve the operator plan text used by the intervention protocol. */
function takeoverPlan(body:JsonRecord,action:string,heightIntervention:boolean):string {
  if(!heightIntervention)return `manual ${str(body.direction)}`;
  return action==="height"?`height ${String(body.height)} SDK`:`balance ${String(body.enabled)}`;
}
/** Assemble the captured intervention flags before makeEnvelope assigns identity. */
function interventionBody(body:JsonRecord,action:string,intervene:boolean,atomic:boolean,heightIntervention:boolean,superseding:PendingOperation):JsonRecord {
  return {...body,...(intervene?{intervene:true}:{}),...(atomic?{operator_intervention_version:1,operator_consent:true,operator_plan:takeoverPlan(body,action,heightIntervention),owner:"manual",arming_mode:"operator",...(heightIntervention?{device_settings:true}:{})}:{}),...(superseding?{supersedes_operation_id:superseding.requestId}:{})};
}
/** Compare current ownership before a manual takeover disarms an old owner. */
function manualReleaseNeeded(safety:JsonRecord,panelId:string):boolean {
  return safety.state==="ARMED"&&(safety.owner!=="manual"||record(safety.panel_control).version===1&&record(safety.panel_control).holder!==panelId);
}
/** Read display metadata only after the image has decoded and its identity is current. */
function receivedFrame(response:Response,context:{cameraSource:JsonRecord|undefined;pipelineIdentity:string|undefined;analysis:JsonRecord|undefined;ageMs:number|undefined;receivedAt:number;stale:string|null;kind:"camera"|"face"},url:string,expired:boolean):Frame {
  const {cameraSource,pipelineIdentity,analysis,ageMs,receivedAt,stale,kind}=context;
  return {cameraSource,pipelineIdentity,captureKey:response.headers.get("X-Rider-Capture-Key")??undefined,analysis,ageMs,receivedAt,ageSource:response.headers.get("X-Rider-Frame-Age-Source")??undefined,decoded:kind==="camera",url,stale:expired,knownFreshness:stale==="false"&&!expired,source:response.headers.get("X-Rider-Source")||response.headers.get("X-Face-Source")||"",fileAt:response.headers.get("X-Rider-File-Observed-At")||"",id:response.headers.get("X-Rider-Frame-Id")||"",visionState:response.headers.get("X-Rider-Vision-State")||"",visionReason:response.headers.get("X-Rider-Vision-Reason")||"",inferenceMs:response.headers.get("X-Rider-Vision-Inference-Ms")||"",visionPolicy:response.headers.get("X-Rider-Vision-Policy")||"",recordedAt:response.headers.get("X-Rider-Recorded-At")||""};
}
const BASE = "/api/v1/rider";
export type Notice = { key: string; code?: string; tone: "error" | "success" | "info" };
export type Frame = { cameraSource?: JsonRecord; pipelineIdentity?: string; captureKey?: string; analysis?: JsonRecord; ageMs?: number; receivedAt?: number; ageSource?: string; decoded?: boolean; url: string; stale: boolean; source: string; fileAt: string; id: string; knownFreshness: boolean; visionState: string; visionReason: string; inferenceMs: string; visionPolicy: string; recordedAt: string };
import { continuousAvailable, controlChanged, freshControl, navigationAvailable, navigationView, observationClock, journeyOwnsMotion, manualInterventionAvailable, manualInterventionSupported, operatorPrioritySupported, operatorSessionActive, retainControl, type ControlClock } from "./navigation";
/** Own the current device session, gesture transport and camera subscriptions. */
export function useRider(section: Section) {
  const [config, setConfig] = useState<DeviceConfig | null>(null);
  const [status, setStatus] = useState<RiderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [panelId] = useState(() => createBrowserUuid());
  const [manualTurnMode, setManualTurnMode] = useState<"in_place" | null>(null);
  const [manualMode, setManualMode] = useState<"hold" | "continuous" | null>(null);
  const [manualRequest, setManualRequest] = useState<JsonRecord | null>(null);
  const [activeDirection, setActiveDirection] = useState<Direction | null>(null);
  const [camera, setCamera] = useState<Frame | null>(null);
  const [runtimeFrame, setRuntimeFrame] = useState<Frame | null>(null);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [cameraStream, setCameraStream] = useState<"raw" | "proc" | "venom" | null>(null);
  const [cameraTransition, setCameraTransitionState] = useState(false);
  const cameraTransitionRef = useRef(false);
  const cameraTransitionEpoch = useRef(0);
  const perceptionExpected = useRef<PerceptionSelection | null | undefined>(undefined);
  const [perceptionSelection, setPerceptionSelection] = useState<PerceptionSelection | null | undefined>(undefined);
  const [perceptionEpoch, setPerceptionEpoch] = useState(0);
  const [showRuntimeFrame, setShowRuntimeFrame] = useState(false);
  const statusRef = useRef<RiderStatus | null>(null);
  const cameraRef = useRef<Frame | null>(null);
  useEffect(() => { cameraRef.current = camera; }, [camera]);
  const configRef = useRef<DeviceConfig | null>(null);
  const clockRef = useRef<ControlClock | null>(null);
  const seqRef = useRef(0);
  const motionBlocked = useRef(0);
  const journeyPreparing = useRef(0);
  const pendingJourney = useRef<{requestId:string;generation:string}|null>(null);
  const pendingAtomic = useRef<JsonRecord|null>(null);
  const cancelPendingRef = useRef<() => boolean>(() => false);
  const motionEpoch = useRef(0);
  const operatorStops = useRef(0);
  const pendingHandoff = useRef<{requestId:string;generation:string;session:string;epoch:number;deadline:number;navigationOperation?:string}|null>(null);
  const pendingResume = useRef<{requestId:string;generation:string}|null>(null);
  const sessionRef = useRef("");
  const sectionRef = useRef(section);
  const mediaRef = useRef<{ camera: "raw" | "proc" | "venom" | null; face: boolean }>({ camera: null, face: false });
  const pollPromise = useRef<Promise<void> | null>(null);
  const refreshPromise = useRef<Promise<void> | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const controlPollPromise = useRef<Promise<void> | null>(null);
  const controlAbortRef = useRef<AbortController | null>(null);
  const commandRef = useRef<(body: JsonRecord, current?: () => boolean) => Promise<unknown>>(() => Promise.resolve(undefined));
  const abortReads = useCallback(() => { abortRef.current?.abort(); controlAbortRef.current?.abort(); }, []);
  const isConnected = useCallback(() => ["online", "degraded"].includes(statusRef.current?.connection.status ?? ""), []);
  const hasFreshControl = useCallback(() => freshControl(statusRef.current, clockRef.current, Date.now(), freshnessCadence(sectionRef.current)), []);
  const canNavigate = useCallback(() => (motionBlocked.current === 0 || motionBlocked.current === journeyPreparing.current && manualInterventionAvailable(statusRef.current, panelId)) && navigationAvailable(statusRef.current, panelId), [panelId]);
  const movementReady = useCallback(() => canNavigate() && safetyOf(statusRef.current).owner === "manual", [canNavigate]);
  const [hold] = useState(() => new HoldController({
    timing: () => readHoldTiming(settingsOf(statusRef.current).control_modes),
    canMove: canNavigate,
    cancelPending: () => cancelPendingRef.current(),
    prepare: (current: () => boolean) => {
      if (manualInterventionSupported(statusRef.current)) return;
      if (!operatorPrioritySupported(statusRef.current) || !(safetyOf(statusRef.current).owner === "navigation" || pendingResume.current)) return;
      return commandRef.current({ action:"navigation_pause" }, current).then(() => undefined);
    },
    send: async (intent: MovementIntent, current: () => boolean) => { await commandRef.current({ action: "move", device_settings: true, ...intent }, current); },
    onActive: setActiveDirection,
    onMode: setManualMode,
    onTurnMode: setManualTurnMode,
    onError: (error) => setNotice({ key: errorCode(error) === "hold_timed_out" ? "holdExpired" : "requestFailed", code: errorCode(error), tone: "error" }),
  }));
  const replaceFrame = useCallback((kind: "camera" | "face", next: Frame | null, retain = false, busyRetry = false) => {
    const setter = kind === "camera" ? setCamera : setRuntimeFrame;
    setter(previous => { if (retain && kind === "camera" && !next && previous) return retainedPreview(previous, busyRetry); if (previous) URL.revokeObjectURL(previous.url); return next; });
  }, []);
  const setPerceptionPolicy = useCallback((policy: JsonRecord | null) => {
    const next = perceptionPolicy(policy);
    if (JSON.stringify(next) === JSON.stringify(perceptionExpected.current)) return;
    const keep = previewMatchesSelection(cameraRef.current, next) && framePipelineCurrent(cameraRef.current, pipelineRef.current);
    perceptionExpected.current = next; setPerceptionSelection(next);
    if (keep) return;
    cameraTransitionEpoch.current += 1; setPerceptionEpoch(value => value + 1);
    if (mediaRef.current.camera === "venom") { cameraRef.current = null; replaceFrame("camera", null); setMediaError("perception.previewWaiting"); }
  }, [replaceFrame]);
  const invalidatePerceptionPreview = useCallback(() => setPerceptionPolicy(null), [setPerceptionPolicy]);
  const keepCameraOnFailure = useCallback(() => !perceptionExpected.current?.model_id || mediaRef.current.camera !== "venom", []);
  const setCameraTransition = useCallback((transitioning: boolean) => {
    if (cameraTransitionRef.current !== transitioning) cameraTransitionEpoch.current += 1;
    cameraTransitionRef.current = transitioning; setCameraTransitionState(transitioning);
    if (transitioning && mediaRef.current.camera === "venom") replaceFrame("camera", null, !perceptionExpected.current?.model_id);
  }, [replaceFrame]);
  const markCameraDecoded = useCallback((url: string, decoded: boolean) => {
    setCamera(previous => previous?.url === url && previous.decoded !== decoded ? { ...previous, decoded } : previous);
  }, []);
  /** Decode offscreen and publish only a response that still owns the media request. */
  const applyDecodedFrame=useCallback(async(response:Response,context:{signal:AbortSignal;kind:"camera"|"face";path:string;currentPreview:()=>boolean;pipelineIdentity:string|undefined;analysis:JsonRecord|undefined;cameraSource:JsonRecord|undefined;ageMs:number|undefined;receivedAt:number;stale:string|null})=>{
    const {signal,kind,path,currentPreview,pipelineIdentity,analysis,cameraSource,ageMs,receivedAt,stale}=context;
    const blob = await response.blob();
    if (signal.aborted || !currentPreview() || !frameRequested(kind, path, sectionRef.current, mediaRef.current)) return;
    const url = URL.createObjectURL(blob);
    try {
      if (kind === "camera") await decodePreview(url, signal);
      if (signal.aborted || !currentPreview() || !frameRequested(kind, path, sectionRef.current, mediaRef.current)) {
        URL.revokeObjectURL(url); return;
      }
      if (kind === "camera" && pipelineIdentity !== cameraPipelineIdentity(pipelineRef.current)) { URL.revokeObjectURL(url); return; }
      const expired = kind === "camera" && path === "/camera/venom" && previewLifetime({ageMs,receivedAt}) === 0;
      replaceFrame(kind,receivedFrame(response,{cameraSource,pipelineIdentity,analysis,ageMs,receivedAt,stale,kind},url,expired));
      setMediaError(null);
    } catch (error) {
      URL.revokeObjectURL(url);
      throw error;
    }
  },[replaceFrame]);
  /** Parse a failed media response and retain only an eligible current preview. */
  const failedFrame=useCallback(async(response:Response,path:string,signal:AbortSignal,kind:"camera"|"face",currentPreview:()=>boolean)=>{
      let code = "";
      try { code = str(record(record(await response.json()).detail).code); } catch { /* Non-JSON frame failure. */ }
      if (signal.aborted || !currentPreview() || !frameRequested(kind, path, sectionRef.current, mediaRef.current)) return;
      replaceFrame(kind, null, kind === "camera" && keepCameraOnFailure(), kind === "camera" && path === "/camera/venom" && response.status === 429 && code === "vision_busy");
      setMediaError(cameraFailureNotice(code,path));
  },[keepCameraOnFailure,replaceFrame]);
  /** Fetch one media response while retaining transition and selection ownership. */
  const fetchFrame = useCallback(async (path: string, signal: AbortSignal, kind: "camera" | "face") => {
    const epoch = cameraTransitionEpoch.current;
    const pipelineIdentity = cameraPipelineIdentity(pipelineRef.current);
    const currentPreview = () => previewRequestCurrent(kind, path, cameraTransitionRef.current, epoch, cameraTransitionEpoch.current);
    if (!currentPreview()) return;
    const response = await fetch(`${getApiBaseUrl()}${BASE}${path}`, { cache: "no-store", signal });
    if (signal.aborted || !currentPreview() || !frameRequested(kind, path, sectionRef.current, mediaRef.current)) return;
    if (!response.ok || !response.headers.get("Content-Type")?.startsWith("image/")) {
      await failedFrame(response,path,signal,kind,currentPreview);
      return;
    }
    const receivedAt = performance.now();
    const ageMs = response.headers.has("X-Rider-Frame-Age-Ms") ? Number(response.headers.get("X-Rider-Frame-Age-Ms")) : undefined;
    const stale = response.headers.get("X-Rider-Stale");
    const placeholder = response.headers.get("X-Rider-Placeholder") === "true";
    if (stale === "true" || placeholder) { replaceFrame(kind, null, kind === "camera" && keepCameraOnFailure()); setMediaError("frameStale"); return; }
    const analysis = readAnalysis(response.headers.get("X-Rider-Analysis-Json"));
    const sourceHeader = response.headers.get("X-Rider-Source-Json");
    const cameraSource = readCameraSource(sourceHeader);
    if (kind === "camera" && sourceHeader && (!cameraSource || !cameraSourceMatches(cameraSource, pipelineRef.current))) {
      replaceFrame("camera", null); setMediaError("perception.previewWaiting"); return;
    }
    if (kind === "camera" && path === "/camera/venom" && !perceptionResultMatches(analysis, perceptionExpected.current)) {
      replaceFrame("camera", null); setMediaError("perception.previewWaiting"); return;
    }
    await applyDecodedFrame(response,{signal,kind,path,currentPreview,pipelineIdentity,analysis,cameraSource,ageMs,receivedAt,stale});
  }, [applyDecodedFrame,failedFrame,replaceFrame,keepCameraOnFailure]);
  /** Recognize only the pending handoff that owns this response's session and epoch. */
  const ownPolledHandoff=useCallback((previous:RiderStatus|null,next:RiderStatus)=>{
          const pending = pendingHandoff.current, nextSafety = safetyOf(next);
          const ownTransfer = nextSafety.state === "ARMED" && nextSafety.owner === "manual" && str(nextSafety.generation)
            && nextSafety.generation !== pending?.generation && record(nextSafety.panel_control).holder === panelId
            && record(nextSafety.operator_control).operation_id === pending?.requestId
            && record(nextSafety.operator_control).generation === nextSafety.generation;
          const ownPreparation = manualInterventionSupported(next) && nextSafety.owner === "navigation"
            && !!pending?.navigationOperation && record(nextSafety.operator_control).operation_id === pending.navigationOperation;
          const ownPending = pending && Date.now() < pending.deadline && pending.epoch === motionEpoch.current
            && pending.session === next.connection.session && pending.session === previous?.connection.session
            && (ownTransfer || ownPreparation);
    return ownPending;
  },[panelId]);
  /** Retire old gestures and media only after checking captured handoff ownership. */
  const reconcilePolledControl=useCallback((previous:RiderStatus|null,next:RiderStatus)=>{
        if (controlChanged(previous, next)) {
          const ownPending=ownPolledHandoff(previous,next);
          if (!ownPending) hold.cancel();
          if ((previous?.connection.transport?.generation ?? 0) !== (next.connection.transport?.generation ?? 0)) {
            motionEpoch.current += 1; pendingHandoff.current = null; pendingResume.current = null;
            clockRef.current = null; replaceFrame("camera", null, true); replaceFrame("face", null);
          }
          seqRef.current = 0;
          if (previous?.connection.session !== next.connection.session || previous?.connection.device_id !== next.connection.device_id) {
            replaceFrame("camera", null); replaceFrame("face", null);
            perceptionExpected.current = undefined; setPerceptionSelection(undefined); cameraTransitionEpoch.current += 1;
            mediaRef.current = { camera: null, face: false }; setCameraStream(null); setShowRuntimeFrame(false);
          }
        }
  },[hold,ownPolledHandoff,replaceFrame]);
  /** Retain the visible section while applying clocks from the observed response. */
  const applyPolledView=useCallback((next:RiderStatus,previous:RiderStatus|null,observed:RiderStatus,nextConfig:DeviceConfig,requestedSection:Section,fast:boolean,timing:{started:number;received:number})=>{
    const {started,received}=timing;
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
  },[]);
  /** Apply one observed response before any subsequent face-frame await. */
  const applyPolledStatus=useCallback((next:RiderStatus,nextConfig:DeviceConfig,requestedSection:Section,fast:boolean,started:number,received:number)=>{
        const previous = statusRef.current;
        const observed = next;
        next = retainSystem(previous, next);
        next = retainControl(previous, next);
        reconcilePolledControl(previous,next);
        if (!["online", "degraded"].includes(next.connection.status)) { replaceFrame("camera", null); replaceFrame("face", null); }
        if (next.errors.control) hold.release();
        if (!["online", "degraded"].includes(next.connection.status) || next.control && !next.errors.control && !navigationAvailable(next, panelId)) hold.cancel();
        if (previous && settingsOf(previous).revision !== settingsOf(next).revision) hold.cancel();
        if (sessionRef.current && sessionRef.current !== next.connection.session) { hold.cancel(); setNotice({ key: "conflict", tone: "info" }); }
        sessionRef.current = next.connection.session;
        applyPolledView(next,previous,observed,nextConfig,requestedSection,fast,{started,received});
    return next;
  },[applyPolledView,hold,panelId,reconcilePolledControl,replaceFrame]);
  /** Test the captured poll target before introducing any face-frame await. */
  const polledFaceRequested=useCallback((next:RiderStatus,requestedSection:Section,fast:boolean)=>{
    return !fast&&requestedSection===sectionRef.current&&["online","degraded"].includes(next.connection.status)
      &&requestedSection==="face"&&mediaRef.current.face;
  },[]);
  /** Serialize section reads and publish only the still-current observed control. */
  const poll = useCallback(async (target?: Section, fast = false, explicit = false) => {
    if (refreshPromise.current && !explicit) return;
    const pending = fast ? controlPollPromise : pollPromise;
    const controller = fast ? controlAbortRef : abortRef;
    while (pending.current) { await pending.current; if (!target || fast) return; }
    const requestedSection = target ?? sectionRef.current;
    const statusSection = requestedSection === "samples" ? "overview" : requestedSection;
    const abort = new AbortController(); controller.current = abort;
    const timeout = fast ? setTimeout(() => abort.abort(), readHoldTiming(settingsOf(statusRef.current).control_modes)?.request_ttl_ms ?? 500) : null;
    const task = async () => {
      try {
        if (!configRef.current) {
          const nextConfig = await apiFetch<DeviceConfig>(`${BASE}/config`, { signal: abort.signal });
          configRef.current = nextConfig; sessionRef.current = nextConfig.session; setConfig(nextConfig);
        }
        const started = Date.now();
        let next = await apiFetch<RiderStatus>(`${BASE}/status?section=${fast ? "navigation" : statusSection}`, { signal: abort.signal, cache: "no-store" });
        const received = Date.now();
        if (abort.signal.aborted) return;
        const nextConfig = await configForConnection(configRef.current!, next.connection, () => apiFetch<DeviceConfig>(`${BASE}/config`, { signal: abort.signal }));
        if (abort.signal.aborted) return;
        next=applyPolledStatus(next,nextConfig,requestedSection,fast,started,received);
        if(polledFaceRequested(next,requestedSection,fast)){
          if(faceRunning(next))await fetchFrame("/face/runtime/frame",abort.signal,"face");
          else replaceFrame("face",null);
        }
      } catch (error) {
        if (abort.signal.aborted) return;
        hold.release();
        const previous = statusRef.current;
        if (previous) { const offline = retainSystem(previous, { ...previous, connection: { ...previous.connection, status: "offline" as const } }); statusRef.current = offline; setStatus(offline); }
        replaceFrame("camera", null); replaceFrame("face", null);
        setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" });
      } finally { if (timeout) clearTimeout(timeout); if (!abort.signal.aborted) setLoading(false); }
    };
    pending.current = task().finally(() => { pending.current = null; if (controller.current === abort) controller.current = null; });
    return pending.current;
  }, [applyPolledStatus,fetchFrame,hold,polledFaceRequested,replaceFrame]);
  const refresh = useCallback((): Promise<void> => {
    if (refreshPromise.current) return refreshPromise.current;
    if (busy) return Promise.resolve();
    motionEpoch.current += 1; motionBlocked.current += 1;
    hold.cancel(); pendingHandoff.current = null; pendingResume.current = null;
    abortReads(); setRefreshing(true); setBusy("/connection/refresh");
    setNotice({ key: "connectionRefreshing", tone: "info" });
    const task = async () => {
      let failure = "", readOnly = false;
      const controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 20000);
      try {
        await Promise.all([pollPromise.current, controlPollPromise.current]);
        clockRef.current = null;
        const next = await apiFetch<DeviceConfig>(`${BASE}/config`, { cache: "no-store", signal: controller.signal });
        configRef.current = next; sessionRef.current = next.session; setConfig(next);
        if (next.device_id) {
          const response = await apiFetch<JsonRecord>(`${BASE}/connection/refresh`, {
            method: "POST", headers: { "X-Rider-Session": next.session }, body: "{}", signal: controller.signal,
          });
          const recovery = record(data(response).recovery);
          if (recovery.state === "failed") failure = str(recovery.error) || "pi_unreachable";
        }
      } catch (error) {
        const code = errorCode(error);
        if (code === "environment_read_only") readOnly = true;
        else failure = code;
      }
      finally {
        clearTimeout(deadline);
        await poll(undefined, false, true);
        const online = ["online", "degraded"].includes(statusRef.current?.connection.status ?? "");
        setNotice(readOnly
          ? { key: online ? "connectionReadOnlyRefreshed" : "connectionReadOnlyUnavailable", tone: online ? "info" : "error" }
          : online && !failure
            ? { key: "connectionRefreshed", tone: "success" }
            : { key: "connectionRefreshFailed", code: failure || str(statusRef.current?.connection.error), tone: "error" });
        motionBlocked.current = Math.max(0, motionBlocked.current - 1);
        setRefreshing(false); setBusy(null);
      }
    };
    refreshPromise.current = task().finally(() => { refreshPromise.current = null; });
    return refreshPromise.current;
  }, [abortReads, busy, hold, poll]);
  /** Send a session-bound mutation and preserve its rejection for the caller. */
  const mutation = useCallback(async (path: string, body: JsonRecord = {}, method = "POST", quiet = false): Promise<JsonRecord> => {
    const session = sessionRef.current;
    if (mutationUnavailable(session,path,body,isConnected)) throw Object.assign(new Error("offline"), { data: { error: "offline" } });
    try {
      const response = await apiFetch<JsonRecord>(`${BASE}${path}`, { method, headers: { "X-Rider-Session": session }, body: method === "DELETE" ? undefined : JSON.stringify(body) });
      if (!commandAccepted(response)) { const rejected = new Error(str(data(response).error) || "command_rejected"); Object.assign(rejected, { data: response }); throw rejected; }
      if (session !== sessionRef.current && path !== "/config") throw new Error("session_changed");
      if (!quiet) setNotice({ key: path === "/conversation/config" ? "conversationConfigured" : "saved", tone: "success" });
      return data(response);
    } catch (error) {
      if (!quiet) {
        setNotice({ key: mutationFailureKey(errorCode(error)), code: errorCode(error), tone: "error" });
      }
      throw error;
    }
  }, [isConnected]);
  const invalidatePipelineMedia = useCallback(() => {
    cameraTransitionEpoch.current += 1; replaceFrame("camera", null);
  }, [replaceFrame]);
  const pipelineSession = status?.connection.session ? `${status.connection.session}:${status.connection.transport?.generation ?? 0}` : undefined;
  const pipeline = useCameraPipeline(section, pipelineSession, mutation, invalidatePipelineMedia);
  const pipelineRef = useRef(pipeline.status); pipelineRef.current = pipeline.status;
  const makeEnvelope = useCallback((body: JsonRecord, source = "manual"): JsonRecord => {
    const action = str(body.action);
    const stopping = action === "stop" || action === "disarm";
    const manualIntent = ["move", "height", "balance", "arm", "clear_estop", "navigation_pause", "navigation_resume"].includes(action);
    if (!stopping && (manualIntent ? !clockRef.current || !str(safetyOf(statusRef.current).generation) : !isConnected() || !hasFreshControl())) throw Object.assign(new Error("stale_control"), { data: { error: "stale_control" } });
    const current = statusRef.current;
    const safety = safetyOf(current);
    const settings = settingsOf(current);
    const timing = readHoldTiming(settings.control_modes);
    const clock = clockRef.current;
    const envelope: JsonRecord = { ...body, request_id: createBrowserUuid(), source, execution_context: "panel" };
    if (record(safety.panel_control).version === 1 && (["move", "height", "balance", "navigation_pause", "navigation_resume"].includes(action) || action === "arm" && body.owner === "manual")) envelope.panel_id = panelId;
    if (!stopping) {
      seqRef.current = Math.max(seqRef.current, finite(safety.last_seq) ? safety.last_seq : 0) + 1;
      Object.assign(envelope, { generation: safety.generation, seq: seqRef.current, ts: (Date.now() + (clock?.offset ?? 0)) / 1000, ttl_ms: timing?.request_ttl_ms, settings_revision: settings.revision });
    }
    return envelope;
  }, [hasFreshControl, isConnected, panelId]);
  /** Capture lane and identity synchronously before an optional fresh-control read. */
  const controlOrigin=useCallback((body:JsonRecord,action:string)=>{
    if (journeyOwnsMotion(statusRef.current) && ["move", "navigation_pause", "navigation_resume"].includes(action) && !(action === "move" && manualInterventionSupported(statusRef.current))) throw Object.assign(new Error("journey_manual_takeover_required"), {data:{error:"journey_manual_takeover_required"}});
    const stopping = ["stop", "disarm"].includes(action);
    if (["stop","disarm","clear_estop","height","balance","navigation_pause"].includes(action) || action==="arm"&&body.owner!=="navigation" || action==="move"&&body.mode!=="release") motionEpoch.current += 1;
    const origin = controlIdentity(statusRef.current);
    const revision = settingsOf(statusRef.current).revision;
    const manualIntent = ["move", "height", "balance", "arm", "clear_estop", "navigation_pause"].includes(str(body.action));
    return {stopping,origin,revision,manualIntent};
  },[]);
  /** Capture takeover state and register the exact envelope before sending it. */
  const controlIntervention=useCallback((body:JsonRecord,action:string)=>{
    const heightIntervention=postureTakeover(action,statusRef.current,panelId);
    const superseding=supersededOperation(action,heightIntervention,pendingJourney.current,pendingResume.current,statusRef.current);
    const atomic=atomicTakeover(body,action,heightIntervention,statusRef.current,panelId,pendingJourney.current,pendingResume.current);
    const intervene = atomic || action === "move" && body.mode !== "release" && operatorPrioritySupported(statusRef.current) && (safetyOf(statusRef.current).owner === "navigation" || !!superseding);
    const interventionEpoch = motionEpoch.current, interventionSession = sessionRef.current;
    const adopting = intervene || action === "navigation_pause";
    if (adopting) abortReads();
    const envelope = makeEnvelope(interventionBody(body,action,intervene,atomic,heightIntervention,superseding));
    const interventionDeadline = Date.now() + Number(envelope.ttl_ms);
    if (atomic && action === "move") pendingAtomic.current = envelope;
    if (adopting) pendingHandoff.current = {requestId:str(envelope.request_id),generation:str(envelope.generation),session:interventionSession,epoch:interventionEpoch,deadline:Date.now()+Number(envelope.ttl_ms),navigationOperation:atomic?str(envelope.supersedes_operation_id):undefined};
    if (action === "navigation_resume") pendingResume.current = { requestId:str(envelope.request_id), generation:str(envelope.generation) };
    const movement = action === "move";
    if (movement) setManualRequest({ ...envelope, phase: "pending" });
    return {atomic,envelope,interventionDeadline,interventionEpoch,interventionSession,adopting,movement};
  },[abortReads,makeEnvelope,panelId]);
  /** Retire only the failed envelope's pending UI state and captured handoffs. */
  const failedControl=useCallback((action:string,envelope:JsonRecord,movement:boolean,atomic:boolean,current:()=>boolean)=>{
      if (pendingAtomic.current === envelope) pendingAtomic.current = null;
      if (movement && current()) setManualRequest({ ...envelope, phase: "failed" });
      if (pendingHandoff.current?.requestId === envelope.request_id) pendingHandoff.current = null;
      if (action === "navigation_resume" && pendingResume.current?.requestId === envelope.request_id) pendingResume.current = null;
      return atomic && !current();
  },[]);
  /** Publish an already validated intervention before clearing superseded operations. */
  const publishAdoptedControl=useCallback((adopted:RiderStatus,envelope:JsonRecord)=>{
        statusRef.current = adopted; setStatus(adopted);
        const last = safetyOf(adopted).last_seq;
        seqRef.current = finite(last) ? last : Number(envelope.seq);
        if (pendingResume.current?.requestId === envelope.supersedes_operation_id) pendingResume.current = null;
        if (pendingJourney.current?.requestId === envelope.supersedes_operation_id) pendingJourney.current = null;
  },[]);
  /** Adopt only a response that still owns the same request, epoch and session. */
  const adoptControl=useCallback((response:JsonRecord,envelope:JsonRecord,action:string,current:()=>boolean,adopting:boolean,interventionEpoch:number,interventionSession:string)=>{
    if (adopting && current() && interventionEpoch === motionEpoch.current && interventionSession === sessionRef.current) {
      const adopted = adoptIntervention(statusRef.current, response, envelope, panelId);
      if (adopted) {
        publishAdoptedControl(adopted,envelope);
      } else {
        if (pendingHandoff.current?.requestId === envelope.request_id) pendingHandoff.current = null;
        hold.cancel();
        throw Object.assign(new Error("operator_intervention_unconfirmed"), {data:{error:"operator_intervention_unconfirmed"}});
      }
    }
    if (pendingHandoff.current?.requestId === envelope.request_id) pendingHandoff.current = null;
    if (action === "navigation_resume" && pendingResume.current?.requestId === envelope.request_id) pendingResume.current = null;
  },[hold,panelId,publishAdoptedControl]);
  /** Resolve only this response's pending movement and enforce its captured deadline. */
  const resolvePendingControl=useCallback((response:JsonRecord,envelope:JsonRecord,action:string,atomic:boolean,movement:boolean,interventionDeadline:number,current:()=>boolean)=>{
      if (atomic && action === "move" && pendingAtomic.current === envelope && Date.now() >= interventionDeadline) {
        cancelPendingRef.current();
        throw Object.assign(new Error("hold_timed_out"), {data:{error:"hold_timed_out"}});
      }
      if (pendingAtomic.current === envelope) pendingAtomic.current = null;
      if (movement && current()) setManualRequest({ ...envelope, phase: response.pending === true ? "pending" : "accepted" });
  },[]);
  /** Send the captured gesture after freshness checks and adopt only its own response. */
  const sendControl = useCallback(async (body: JsonRecord, current: () => boolean = () => true, announce = true): Promise<JsonRecord> => {
    if (!current()) return {};
    const action = str(body.action);
    const {stopping,origin,revision,manualIntent}=controlOrigin(body,action);
    if (!stopping && !manualIntent && !hasFreshControl()) await poll("control", true);
    // Never send an old press after STOP, replacement, device or generation change.
    if (!current()) return {};
    if (!stopping && (origin !== controlIdentity(statusRef.current) || revision !== settingsOf(statusRef.current).revision)) throw Object.assign(new Error("stale_control"), { data: { error: "stale_control" } });
    const {atomic,envelope,interventionDeadline,interventionEpoch,interventionSession,adopting,movement}=controlIntervention(body,action);
    let response: JsonRecord;
    try {
      response = await mutation("/control", envelope, "POST", true);
      resolvePendingControl(response,envelope,action,atomic,movement,interventionDeadline,current);
    } catch (error) {
      if(failedControl(action,envelope,movement,atomic,current))return {};
      throw error;
    }
    adoptControl(response,envelope,action,current,adopting,interventionEpoch,interventionSession);
    if (announce && current()) setNotice({ key: response.pending === true || record(response.operation).status === "pending" ? "commandPending" : "commandAccepted", tone: "info" });
    return response;
  }, [adoptControl, controlIntervention, controlOrigin, failedControl, hasFreshControl, mutation, poll, resolvePendingControl]);
  useEffect(() => { commandRef.current = sendControl; }, [sendControl]);
  cancelPendingRef.current = () => {
    const pending = pendingAtomic.current;
    if (!pending) return false;
    pendingAtomic.current = null; motionEpoch.current += 1;
    // Independent cancellation must not queue behind the takeover request it invalidates.
    try {
      const envelope = makeEnvelope({action:"move", mode:"release", direction:pending.direction, device_settings:true,
        intervene:true, operator_intervention_version:1, supersedes_operation_id:pending.request_id,
        ...(str(pending.supersedes_operation_id) ? {supersedes_navigation_operation_id:pending.supersedes_operation_id} : {})});
      envelope.generation = pending.generation;
      void mutation("/control", envelope, "POST", true).catch(error => {
        setNotice({key:"requestFailed",code:errorCode(error),tone:"error"});
      });
    } catch (error) { setNotice({key:"requestFailed",code:errorCode(error),tone:"error"}); }
    return true;
  };
  /** Cancel stale local holds and report command failures without changing rejection. */
  const commandFailure=useCallback((error:unknown,epoch:number,key:string)=>{
    if (epoch === motionEpoch.current || key !== "navigation_resume") hold.cancel();
    if(errorCode(error)!=="navigation_trial_cancelled") setNotice({ key: errorCode(error).includes("stale") ? "staleControl" : "requestFailed", code: errorCode(error), tone: "error" });
  },[hold]);
  /** Coordinate command UI state without weakening STOP or cancellation ordering. */
  const command = useCallback(async (body: JsonRecord, throwOnError = false, quiet = false) => {
    const key = str(body.action); if (key !== "move") hold.cancel();
    const safetyAction = ["arm", "clear_estop", "stop", "disarm"].includes(key);
    if (safetyAction) motionBlocked.current += 1;
    setBusy(key);
    const epoch = motionEpoch.current;
    try { if ((key === "arm" || key === "clear_estop") && !clockRef.current) await poll("control", true); const response = await sendControl(body, () => true, !quiet); if (key !== "move") await poll(undefined, key === "navigation_resume" || key === "navigation_pause"); return response; }
    catch (error) {
      commandFailure(error,epoch,key);
      if (throwOnError) {
        throw error;
      }
      return null;
    }
    finally { if (safetyAction) motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(value => value === key ? null : value); }
  }, [commandFailure, hold, poll, sendControl]);
  const stopManual = useCallback(async () => {
    operatorStops.current += 1;
    motionEpoch.current += 1;
    hold.cancel();
    return command({action:"stop"},true);

  }, [command, hold]);
  const startNavigationTrial = useCallback(async (plan: string, revision: number) => {
    const origin = sessionRef.current, epoch = motionEpoch.current;
    const priority = operatorPrioritySupported(statusRef.current);
    hold.cancel(); if (!priority) motionBlocked.current += 1; setBusy("trial");
    try {
      await poll("control", true);
      if (sessionRef.current !== origin || motionEpoch.current !== epoch || document.hidden) throw new Error("navigation_trial_cancelled");
      const envelope = makeEnvelope({action:"arm"});
      const intent = navigationIntent(envelope);
      const result = await mutation("/navigation/trial", {...intent, ...(priority ? {panel_id:panelId,operator_control_version:1} : {}), config_revision:revision, operator_consent:true, operator_plan:plan}, "POST", true);
      if (sessionRef.current !== origin || motionEpoch.current !== epoch) throw new Error("navigation_trial_cancelled");
      await poll("control", true);
      return result;
    } finally { if (!priority) motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(null); }
  }, [hold, poll, makeEnvelope, mutation, panelId]);
  const startNavigationJourney = useCallback(async (plan: string, revision: number, prepare?: () => Promise<number>) => {
    const origin = sessionRef.current, epoch = motionEpoch.current;
    hold.cancel(); motionBlocked.current += 1; journeyPreparing.current += 1; setBusy("journey");
    try {
      // Include explicit image confirmation in the cancellable Start operation.
      // STOP must take this path before any journey HTTP request exists.
      const acceptedRevision = prepare ? await prepare() : revision;
      if (sessionRef.current !== origin || motionEpoch.current !== epoch || document.hidden) throw new Error("navigation_trial_cancelled");
      // Loading/warming the selected model is part of this explicit Start,
      // before creating the short-lived Pi ARM intent or enabling motion.
      await mutation("/perception/prepare", {}, "POST", true);
      if (sessionRef.current !== origin || motionEpoch.current !== epoch || document.hidden) throw new Error("navigation_trial_cancelled");
      await poll("control", true);
      if (sessionRef.current !== origin || motionEpoch.current !== epoch || document.hidden) throw new Error("navigation_trial_cancelled");
      if (safetyOf(statusRef.current).navigation_journey_version !== 1) throw new Error("navigation_journey_update_required");
      const intent = navigationIntent(makeEnvelope({action:"arm"}));
      pendingJourney.current = {requestId:str(intent.request_id),generation:str(intent.generation)};
      const result = await mutation("/navigation/journey", {...intent, panel_id:panelId, operator_control_version:1, journey_version:1, config_revision:acceptedRevision, operator_consent:true, operator_plan:plan}, "POST", true);
      if (sessionRef.current !== origin || motionEpoch.current !== epoch) throw new Error("navigation_trial_cancelled");
      await poll("control", true);
      return result;
    } finally { pendingJourney.current = null; journeyPreparing.current = Math.max(0, journeyPreparing.current - 1); motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(value => value === "journey" ? null : value); }
  }, [hold, poll, makeEnvelope, mutation, panelId]);
  const startPulseTrial = useCallback(async (profile:string,plan:string,context:string,requestId:string,revision:number,generation:string,onDispatch:()=>void) => {
    const origin=sessionRef.current,epoch=motionEpoch.current;
    hold.cancel();motionBlocked.current+=1;setBusy("pulse-trial");
    try{
      await poll("control",true);
      if(sessionRef.current!==origin||motionEpoch.current!==epoch||document.hidden)throw new Error("navigation_trial_cancelled");
      if(settingsOf(statusRef.current).revision!==revision||safetyOf(statusRef.current).generation!==generation)throw new Error("stale_control");
      const envelope=makeEnvelope({action:"arm",owner:"manual",arming_mode:"operator",operator_consent:true,operator_plan:plan,trial_profile_id:profile,trial_version:1,trial_context:context});
      envelope.request_id=requestId;
      onDispatch();const result=await mutation("/motion/trials",envelope,"POST",true);
      if(sessionRef.current!==origin||motionEpoch.current!==epoch)throw new Error("navigation_trial_cancelled");
      await poll("control",true);return result;
    }finally{motionBlocked.current=Math.max(0,motionBlocked.current-1);setBusy(value=>value==="pulse-trial"?null:value);}
  },[hold,poll,makeEnvelope,mutation]);
  const startFigure = useCallback(async (identifier:string,plan:string,signal?:AbortSignal):Promise<string> => {
    hold.cancel();
    const origin=sessionRef.current,stops=operatorStops.current;
    let epoch:number|null=null,dispatched=false,blocked=false;
    const onAbort=()=>{
      // Only stop the still-current start; a newer gesture or device owns its own motion.
      if(dispatched&&epoch===motionEpoch.current&&sessionRef.current===origin)
        void stopManual().catch(()=>undefined);
    };
    if(signal?.aborted)throw new Error("figure_cancelled");
    signal?.addEventListener("abort",onAbort,{once:true});
    try{
      await command({action:"stop"},true,true);
      if(signal?.aborted||operatorStops.current!==stops||sessionRef.current!==origin||document.hidden)throw new Error("figure_cancelled");
      epoch=motionEpoch.current;
      motionBlocked.current+=1;blocked=true;setBusy("figure-start");
      await poll("control",true);
      if(signal?.aborted||sessionRef.current!==origin||motionEpoch.current!==epoch||document.hidden)throw new Error("figure_cancelled");
      const envelope=makeEnvelope({action:"arm",owner:"manual",arming_mode:"operator",operator_consent:true,operator_plan:plan,figure_id:identifier,figure_version:1});
      dispatched=true;
      await mutation("/motion/figures/runs",envelope,"POST",true);
      if(signal?.aborted||sessionRef.current!==origin||motionEpoch.current!==epoch||document.hidden)throw new Error("figure_cancelled");
      await poll("control",true);
      if(signal?.aborted||sessionRef.current!==origin||motionEpoch.current!==epoch||document.hidden)throw new Error("figure_cancelled");
      return str(envelope.request_id);
    }finally{
      signal?.removeEventListener("abort",onAbort);
      if(blocked){motionBlocked.current=Math.max(0,motionBlocked.current-1);setBusy(value=>value==="figure-start"?null:value);}
    }
  },[hold,command,poll,makeEnvelope,mutation,stopManual]);
  const keepFigure = useCallback(async (runId:string,identifier:string,plan:string) => {
    if(document.hidden)throw new Error("figure_contact_lost");
    const envelope=makeEnvelope({action:"arm",owner:"manual",arming_mode:"operator",operator_consent:true,operator_plan:plan,figure_id:identifier,figure_version:1,figure_run_id:runId});
    return mutation("/motion/figures/runs",envelope,"POST",true);
  },[makeEnvelope,mutation]);
  const currentManualSettings=useCallback(()=>settingsOf(statusRef.current),[]);
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
  /** End legacy loop producers in order while retaining the operator's preview. */
  const stopLegacyProducers=useCallback(async(assertCurrent:()=>void)=>{
      // Keep the preview when handing motor authority back to the operator.
      assertCurrent();
      await mutation("/goal/s2_camera_preview", { enabled: true }, "POST", true);
      const goals = featureRows(statusRef.current);
      for (const name of ["s14_venom_navigation", "s15_venom_perception"]) {
        assertCurrent();
        if (goals.some(goal => goal.name === name)) await mutation("/goal/" + name, { enabled: false }, "POST", true);
      }
  },[mutation]);
  /** Transfer control in sequential confirmed steps under the captured session and epoch. */
  const takeManual = useCallback(async (plan: string) => {
    let epoch = ++motionEpoch.current;
    const origin = sessionRef.current;
    const assertCurrent = () => {
      if (sessionRef.current !== origin || motionEpoch.current !== epoch || document.hidden) throw Object.assign(new Error("navigation_trial_cancelled"), {data:{error:"navigation_trial_cancelled"}});
    };
    hold.cancel(); setBusy("takeover");
    const fromJourney = journeyOwnsMotion(statusRef.current);
    try {
      if (fromJourney) {
        await command({ action:"stop" }, true, true);
        epoch += 1;
        await poll("control", true);
        assertCurrent();
        if (statusRef.current?.errors.control || safetyOf(statusRef.current).stale === true || safetyOf(statusRef.current).state !== "DISARMED") throw new Error("journey_takeover_stop_unconfirmed");
      }
      await poll("control", true);
      assertCurrent();
      if (operatorSessionActive(statusRef.current) && safetyOf(statusRef.current).state === "ARMED"
        && record(safetyOf(statusRef.current).panel_control).holder === panelId) {
        if (safetyOf(statusRef.current).owner === "navigation") await command({ action: "navigation_pause" }, true, true);
        return;
      }
      if (!operatorPrioritySupported(statusRef.current)) {
        await stopLegacyProducers(assertCurrent);
      }
      await poll("control", true);
      assertCurrent();
      let safety = safetyOf(statusRef.current);
      if (manualReleaseNeeded(safety,panelId)) {
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
      await poll();
    } catch(error) { setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" }); }
    finally { setBusy(null); }
  }, [hold, poll, command, panelId,stopLegacyProducers]);
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
      perceptionExpected.current = undefined; setPerceptionSelection(undefined); cameraTransitionEpoch.current += 1;
      mediaRef.current = { camera: null, face: false }; setCameraStream(null); setShowRuntimeFrame(false); setMediaError(null);
      replaceFrame("camera", null); replaceFrame("face", null); setNotice(next.selection_warning ? { key: next.selection_warning, tone: "info" } : null); await poll();
    } catch (error) { setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" }); }
    finally { motionBlocked.current = Math.max(0, motionBlocked.current - 1); setBusy(null); }
  }, [abortReads, hold, mutation, poll, replaceFrame]);
  useEffect(() => { hold.release(); }, [hold, section]);
  useEffect(() => {
    sectionRef.current = section; mediaRef.current = { camera: cameraStream, face: showRuntimeFrame };
    if (!["control", "vision", "samples"].includes(section) || !cameraStream) replaceFrame("camera", null);
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
    return () => { alive = false; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); window.removeEventListener("blur", blur); abortReads(); };
  }, [abortReads, cameraStream, hold, poll, replaceFrame, section, showRuntimeFrame]);
  const mediaSession = status?.connection.session;
  const processedTransition = cameraStream === "venom" && cameraTransition;
  const nativeStreaming = nativePreview(pipeline.status, cameraStream);
  const pipelineLocation = pipeline.status?.profile.analysis_location;
  useEffect(() => {
    if (pipelineLocation === "local" && cameraStream === "venom") setCameraStream("proc");
    if (pipelineLocation === "venom" && cameraStream === "proc") setCameraStream("venom");
  }, [pipelineLocation, cameraStream]);
  const pipelineIdentity = pipeline.status ? [pipeline.status.revision, pipeline.status.transport_epoch, pipeline.status.camera_session].join(":") : "";
  const pipelineBusy = pipeline.busy;
  const pipelineStopped = cameraPreviewStopped(pipeline.status);
  useEffect(() => {
    if (pipelineStopped) { replaceFrame("camera", null, true); setMediaError("visionCameraOff"); }
  }, [pipelineStopped, replaceFrame]);
  const nativePart = useCallback(async (part: MjpegPart, signal: AbortSignal, current: () => boolean) => {
    const headers = part.headers;
    const source = record(JSON.parse(headers.get("X-Rider-Source-Json") ?? "{}"));
    const anchor = pipelineRef.current?.previewAnchor;
    if (!anchor?.latest || anchor.latest.camera_session !== source.camera_session
      || anchor.latest.transport_epoch !== source.transport_epoch || anchor.latest.pipeline_revision !== source.pipeline_revision
      || !Number.isFinite(anchor.latest.captured_monotonic_ns)) throw new Error("stream_age_unavailable");
    const headerAge = Number(headers.get("X-Rider-Frame-Age-Ms"));
    const ageMs = anchoredPreviewAge(source, headerAge, part.receivedAt, pipelineRef.current?.previewAnchor);
    const freshness = headers.get("X-Rider-Stale");
    if (!["true", "false"].includes(freshness ?? "") || headers.get("X-Rider-Placeholder") === "true"
      || !headers.has("X-Rider-Frame-Age-Ms") || !Number.isFinite(ageMs) || ageMs < 0
      || source.contract_version !== 1 || !source.camera_session || !Number.isSafeInteger(source.sequence) || Number(source.sequence) < 1
      || headers.get("X-Rider-Frame-Id") !== source.frame_id) throw new Error("stream_source_invalid");
    // A structurally valid, late part is not corruption of the prior image.
    // Keep aging that image to its own deadline; this part renews nothing.
    if (freshness === "true") throw new Error("stream_source_expired");
    const analysis = readAnalysis(headers.get("X-Rider-Analysis-Json"));
    if (headers.get("X-Rider-Source") === "venom" && !nativeAnalysisMatches(analysis, source)) throw new Error("stream_analysis_mismatch");
    if (headers.get("X-Rider-Source") === "venom" && !perceptionResultMatches(analysis, perceptionExpected.current)) {
      replaceFrame("camera", null); setMediaError("perception.previewWaiting"); return false;
    }
    const receivedAt = part.receivedAt;
    if ((previewLifetime({ageMs, receivedAt}) ?? 0) <= 0) throw new Error("stream_source_expired");
    const url = URL.createObjectURL(new Blob([Uint8Array.from(part.bytes)], { type: "image/jpeg" }));
    try {
      await decodePreview(url, signal);
      if (signal.aborted || !current()) { URL.revokeObjectURL(url); return false; }
      const expired = (previewLifetime({ageMs, receivedAt}) ?? 0) <= 0;
      replaceFrame("camera", {
        cameraSource: source, pipelineIdentity: [source.pipeline_revision, source.transport_epoch, source.camera_session].join(":"),
        url, decoded: true, stale: expired, knownFreshness: !expired, ageMs, receivedAt,
        ageSource: headers.get("X-Rider-Frame-Age-Source") ?? undefined,
        captureKey: headers.get("X-Rider-Capture-Key") ?? undefined,
        analysis,
        source: headers.get("X-Rider-Source") ?? "", id: String(source.frame_id),
        fileAt: headers.get("X-Rider-File-Observed-At") ?? "",
        visionState: headers.get("X-Rider-Vision-State") ?? "",
        visionReason: headers.get("X-Rider-Vision-Reason") ?? "",
        inferenceMs: headers.get("X-Rider-Vision-Inference-Ms") ?? "",
        visionPolicy: headers.get("X-Rider-Vision-Policy") ?? "",
        recordedAt: "",
      });
      setMediaError(expired ? "frameStale" : null);
      return !expired;
    } catch (error) { URL.revokeObjectURL(url); throw error; }
  }, [replaceFrame]);
  useEffect(() => {
    if (!cameraStream || !["control", "vision", "samples"].includes(section) || processedTransition || nativeStreaming || pipelineBusy || pipelineStopped) return;
    let alive = true, cycle = 0, timer: ReturnType<typeof setTimeout> | undefined, abort: AbortController | undefined;
    const tick = async () => {
      if (!alive || document.hidden) return;
      const current = ++cycle, started = performance.now(), epoch = cameraTransitionEpoch.current; abort = new AbortController();
      try { await fetchFrame("/camera/" + cameraStream, abort.signal, "camera"); }
      catch { if (!abort.signal.aborted && alive && previewRequestCurrent("camera", "/camera/" + cameraStream, cameraTransitionRef.current, epoch, cameraTransitionEpoch.current)) { replaceFrame("camera", null, keepCameraOnFailure()); setMediaError("frameMissing"); } }
      if (alive && current === cycle && !document.hidden) timer = setTimeout(() => void tick(), nextPreviewDelay(started, performance.now(), previewInterval(statusRef.current?.loop_timing)));
    };
    const visibility = () => { cycle++; clearTimeout(timer); abort?.abort(); if (!document.hidden) void tick(); };
    document.addEventListener("visibilitychange", visibility); void tick();
    return () => { alive=false; cycle++; clearTimeout(timer); abort?.abort(); document.removeEventListener("visibilitychange", visibility); };
  }, [cameraStream, section, mediaSession, fetchFrame, replaceFrame, processedTransition, nativeStreaming, pipelineBusy, pipelineStopped, pipelineIdentity, perceptionEpoch, keepCameraOnFailure]);
  useEffect(() => {
    if (!nativeStreaming || !cameraStream || !["control", "vision", "samples"].includes(section) || processedTransition || pipelineBusy || pipelineStopped) return;
    let alive = true, cycle = 0, retry: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined;
    const connect = async () => {
      if (!alive || document.hidden) return;
      const ticket = ++cycle, epoch = cameraTransitionEpoch.current;
      const abort = new AbortController(); controller = abort;
      let latest: MjpegPart | null = null, decoding = false, lastSequence = -1, gap: ReturnType<typeof setTimeout>;
      const current = () => alive && ticket === cycle && !abort.signal.aborted && !document.hidden
        && epoch === cameraTransitionEpoch.current;
      const expire = () => { replaceFrame("camera", null, keepCameraOnFailure()); setMediaError("frameStale"); abort.abort(); };
      const pump = async () => {
        if (decoding) return; decoding = true;
        try {
          while (latest && current()) {
            const part = latest; latest = null;
            if (await nativePart(part, abort.signal, current) && current()) {
              // Bytes/sequence progress is not proof of a usable image. Rejected
              // or undecodable parts must not keep a dead preview alive forever.
              clearTimeout(gap); gap = setTimeout(expire, 3000);
            }
          }
        }
        catch (error) { if (current()) { if (!expiredNativeCandidate(error)) replaceFrame("camera", null, keepCameraOnFailure()); setMediaError("frameStale"); } }
        finally { decoding = false; }
      };
      gap = setTimeout(expire, 5000);
      try {
        const response = await fetch(getApiBaseUrl() + BASE + "/camera/stream/" + cameraStream, { cache: "no-store", signal: abort.signal });
        await readMjpeg(response, abort.signal, part => {
          if (!current()) return;
          const source = record(JSON.parse(part.headers.get("X-Rider-Source-Json") ?? "{}"));
          const sequence = Number(source.sequence);
          if (!Number.isSafeInteger(sequence) || sequence <= lastSequence) return;
          lastSequence = sequence;
          latest = part; void pump();
        });
      } catch { if (current()) { replaceFrame("camera", null, keepCameraOnFailure()); setMediaError("pipeline.streamFailed"); } }
      finally {
        clearTimeout(gap); latest = null; abort.abort();
        if (alive && ticket === cycle && !document.hidden) {
          replaceFrame("camera", null, keepCameraOnFailure()); retry = setTimeout(() => void connect(), 1000);
        }
      }
    };
    const visibility = () => {
      cycle++; clearTimeout(retry); controller?.abort(); replaceFrame("camera", null, keepCameraOnFailure());
      if (!document.hidden) void connect();
    };
    document.addEventListener("visibilitychange", visibility); void connect();
    return () => { alive = false; cycle++; clearTimeout(retry); controller?.abort(); document.removeEventListener("visibilitychange", visibility); };
  }, [cameraStream, nativeStreaming, pipelineBusy, pipelineStopped, pipelineIdentity, processedTransition, section, mediaSession, nativePart, replaceFrame, perceptionEpoch, keepCameraOnFailure]);
  useEffect(() => {
    if ((!nativeStreaming && cameraStream !== "venom") || !camera || camera.stale) return;
    const remaining = previewLifetime(camera);
    if (remaining === null) return;
    const url = camera.url;
    const timer = setTimeout(() => {
      setCamera(previous => previous?.url === url ? { ...previous, stale: true, knownFreshness: false } : previous);
    }, remaining);
    return () => clearTimeout(timer);
  }, [camera, cameraStream, nativeStreaming]);
  useEffect(() => () => { hold.release(); abortReads(); }, [abortReads, hold]);
  // ARM always refreshes the fast control endpoint before making its envelope.
  // The latency of a diagnostics view must not prevent that refresh request.
  const canArm = canRequestArm(status);
  return { startFigure, keepFigure, currentManualSettings, startPulseTrial, pipeline, config, status, loading, notice, setNotice, busy, activeDirection, manualMode, manualTurnMode, manualRequest, panelId, canContinuous: continuousAvailable(status, panelId) && canNavigate(), controlFresh: hasFreshControl(), canMutate: isConnected(), canArm, canNavigate: canNavigate(), canMove: movementReady(), canSetHeight: motionBlocked.current === 0 && heightAvailable(status,panelId), canSetBalance: motionBlocked.current === 0 && balanceAvailable(status,panelId), poll, refresh, refreshing, command, stopManual, startNavigationTrial, startNavigationJourney, currentSession, currentMotionEpoch, currentGeneration, currentCamera, setPerceptionPolicy, invalidatePerceptionPreview, perceptionSelection, mutate: runMutation, takeManual, selectDevice, hold, camera, cameraStream, setCameraStream, cameraTransition, setCameraTransition, markCameraDecoded, runtimeFrame, showRuntimeFrame, setShowRuntimeFrame, mediaError };
}
export type RiderClient = ReturnType<typeof useRider>;
