import {useImageReference,ImageReferenceControls,ImageReferenceOverlay} from "./image-reference";
import { VisionSectors } from "./vision-sectors";
import { journeyOwnsMotion, operatorPrioritySupported } from "../lib/navigation";
import { CameraPipelineControls } from "./camera-pipeline";
import { framePipelineCurrent } from "../lib/camera-pipeline";
import { CorridorOverlay, type CorridorEditorProps } from "./corridor-overlay";
import { manualFeedback } from "../lib/manual-feedback";
import { frameAge } from "../lib/vision-frame";
import { previewNotice } from "../lib/loop-controls";
import { perceptionFrameCurrent } from "../lib/perception";
/* eslint-disable @next/next/no-img-element -- Pi binary frames use revocable object URLs. */
import { useEffect, useId, useState, type ReactNode } from "react";
import { displayedPreview, previewLifetime } from "../lib/preview-media";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Camera, Eye, EyeOff, CirclePause, CircleStop } from "lucide-react";
import { actuatorState, displayValue, finite, formatTime, record, safetyOf, settingsOf, type Direction, type NumericDescriptor } from "../lib/model";
import type { Translator } from "../lib/i18n";
import type { Frame, RiderClient } from "../lib/use-rider";
import { Button, Empty, Field, Panel, Pill, Range, Select } from "./primitives";
import styles from "../rider.module.css";

const arrows = [
  { direction: "forward" as const, row: 1, column: 2, Icon: ArrowUp },
  { direction: "left" as const, row: 2, column: 1, Icon: ArrowLeft },
  { direction: "right" as const, row: 2, column: 3, Icon: ArrowRight },
  { direction: "backward" as const, row: 3, column: 2, Icon: ArrowDown },
];
export function ManualPad({ client, t, language, compact = false, minimal = false, children }: { client: RiderClient; t: Translator; language: string; compact?: boolean; minimal?: boolean; children?: ReactNode }) {
  const [mode, setMode] = useState<"pulse" | "continuous">("pulse");
  const helpId = useId();
  const safety = safetyOf(client.status);
  const settings = settingsOf(client.status);
  const directions = record(settings.directions);
  const schema = record(record(settings.parameter_schema).direction_fields);
  const speedDescriptor = record(schema.speed) as NumericDescriptor;
  const continuous = mode === "continuous";
  const available = continuous ? client.canContinuous : client.canNavigate;
  const pointerDown = (direction: Direction, event: React.PointerEvent<HTMLButtonElement>) => {
    if (continuous || !available || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    client.hold.start(direction, event.pointerId);
  };
  const chooseMode = (next: "pulse" | "continuous") => {
    if (next === mode) return;
    // End the old intent; selecting an input mode never starts a new one.
    client.hold.release();
    setMode(next);
  };
  const feedback = manualFeedback(client.status, client.manualRequest, client.controlFresh);
  return <div className={styles.manualPad} data-testid="manual-pad" data-mode={mode}>
    <label className={styles.padMode}>
      <span>{t("manualDrivingMode")}</span>
      <Select value={mode} data-testid="rider-manual-mode" onChange={event => chooseMode(event.target.value as "pulse" | "continuous")}>
        <option value="pulse">{t("pulsePad")}</option>
        <option value="continuous">{t("continuousPad")}</option>
      </Select>
    </label>
    <div className={styles.pad} role="group" aria-label={t("manual")} aria-describedby={minimal ? undefined : helpId} data-mode={mode}>
      {arrows.map(({ direction, row, column, Icon }) => {
        const active = client.activeDirection === direction && client.manualMode === (continuous ? "continuous" : "hold");
        return <button type="button" key={direction} aria-label={t(direction)} title={t(direction)} aria-pressed={active} disabled={!available} data-testid={`rider-${direction}`} className={`${styles.arrow} ${active ? styles.arrowActive : ""}`} style={{ gridRow: row, gridColumn: column }}
          onPointerDown={event => pointerDown(direction, event)}
          onPointerUp={event => { if (!continuous) client.hold.end(event.pointerId); }}
          onPointerCancel={event => { if (!continuous) client.hold.release(event.pointerId); }}
          onLostPointerCapture={event => { if (!continuous) client.hold.release(event.pointerId); }}
          onClick={event => {
            if (!available) return;
            if (continuous) client.hold.toggle(direction);
            else if (event.detail === 0 && !client.activeDirection) { client.hold.start(direction, -2); client.hold.end(-2); }
          }}
          onKeyDown={event => {
            if (!["Enter", " "].includes(event.key)) return;
            if (event.repeat) { event.preventDefault(); return; }
            if (!continuous && available) { event.preventDefault(); client.hold.start(direction, -1); }
          }}
          onKeyUp={event => {
            if (!continuous && ["Enter", " "].includes(event.key)) { event.preventDefault(); client.hold.end(-1); }
          }}
          onBlur={() => client.hold.releasePointer()}>
          <Icon size={compact ? 20 : 25} />{!compact && <span>{t(direction)}</span>}<span className={styles.small}>{displayValue(record(directions[direction]).speed, speedDescriptor, language)} {speedDescriptor.display_unit ?? ""}</span>
        </button>;
      })}
      <button type="button" className={`${styles.padCenter} ${styles.padStop}`} style={{ gridRow: 2, gridColumn: 2 }} aria-label={t("stopMotors")} title={t(journeyOwnsMotion(client.status) ? "journeyStop" : operatorPrioritySupported(client.status) ? "operatorPauseHint" : "manualStopHint")} data-testid="rider-pad-stop" onClick={() => void client.stopManual().catch(() => undefined)}><CircleStop size={27} /><span>{t("stopShort")}</span></button>
    </div>
    {minimal?<div className={styles.manualReadout} data-testid="manual-feedback">
      <div role="status"><Pill tone={safety.state==="ARMED"?"good":"warning"}>{t(safety.state==="ARMED"?"armed":safety.state==="ESTOP"?"estop":safety.state==="DISARMED"?"disarmed":"unknown")}</Pill>{client.activeDirection&&<span>{t(client.activeDirection)}</span>}</div>
      {children}
    </div>:<div className={styles.padDetails}>
    <div className={styles.padHelp} id={helpId}>
      <p className={styles.hint}>{t(continuous ? "manualModeContinuousHint" : "manualModePulseHint")}</p>
      {continuous && !client.canContinuous && <p className={styles.hint} role="status">{t(record(safety.panel_control).version === 1 ? "continuousTakeover" : "continuousUnsupported")}</p>}
    </div>
    <div className={styles.manualFeedback} data-testid="manual-feedback" aria-live="polite">
      <span>{t("manualIntent")}: <strong>{client.activeDirection ? `${t(client.manualMode === "continuous" ? "continuousPad" : "pulsePad")} · ${t(client.activeDirection)}` : t("manualNoDirection")}</strong></span>
      <span>{t("manualAck")}: <strong>{t(feedback.requestKey)}</strong></span>
      <span>{t("manualOutput")}: <strong>{t(feedback.executionKey)}</strong> · {feedback.output}</span>
    </div>
    <div className={styles.padStatus}><Pill tone={safety.state === "ARMED" ? "good" : "warning"}>{t(safety.state === "ARMED" ? "armed" : safety.state === "ESTOP" ? "estop" : safety.state === "DISARMED" ? "disarmed" : "unknown")}</Pill>{(client.status?.errors.control || safety.stale === true) && <Pill tone="warning">{t("lastPermission")}</Pill>}{!!record(safety.execution).inhibit && <Pill tone="warning">{t("executionPaused")}</Pill>}{client.activeDirection && <Pill tone="good">{t(client.manualMode === "continuous" ? "continuousPad" : "held")} · {t(client.activeDirection)}</Pill>}</div>
    <div className={`${styles.flex} ${styles.space}`} style={{ marginTop: 15 }}><span className={styles.small}>{t("owner")}: {typeof safety.owner === "string" ? safety.owner : "—"}</span><Button onClick={() => client.hold.release()} disabled={!client.activeDirection}><CirclePause size={15} />{t("endDirection")}</Button></div>
      {children}
    </div>}
  </div>;
}
export function Posture({ client, t, language, compact = false }: { client: RiderClient; t: Translator; language: string; compact?: boolean }) {
  const [draftHeight, setDraftHeight] = useState<number | null>(null);
  const settings = settingsOf(client.status);
  const actuator = record(record(settings.parameter_schema).actuator_fields);
  const descriptor = record(actuator.height) as NumericDescriptor;
  const balanceDescriptor = record(actuator.balance);
  const actuators = actuatorState(client.status);
  const target = record(actuators.height);
  const balance = record(actuators.balance);
  const height = draftHeight ?? (finite(target.value) ? target.value : descriptor.minimum);
  const capabilities = record(safetyOf(client.status).capabilities);
  const heightAvailable = record(capabilities.height).available !== false && descriptor.type === "integer" && finite(descriptor.minimum) && finite(descriptor.maximum);
  if (compact) {
    const value = draftHeight ?? (finite(target.value) ? target.value : descriptor.minimum);
    const chosen = draftHeight !== null || finite(target.value);
    const valid = finite(value) && Number.isInteger(value) && finite(descriptor.minimum) && finite(descriptor.maximum)
      && value >= descriptor.minimum && value <= descriptor.maximum
      && (!finite(descriptor.step) || descriptor.step <= 0 || Number.isInteger((value - descriptor.minimum) / descriptor.step));
    return <div className={styles.operatorPosture} data-testid="operator-posture">
      <div className={styles.operatorHeight}>
        <Field label={t("operator.height") + " · " + (descriptor.display_unit ?? "SDK")}>
          <div className={styles.flex}><Range data-testid="operator-height" aria-label={t("operator.height")} value={finite(value) ? value : 0}
            min={descriptor.minimum} max={descriptor.maximum} step={descriptor.step ?? 1}
            disabled={!client.canMutate || !heightAvailable || !!client.busy}
            onPointerDown={event => setDraftHeight(Number(event.currentTarget.value))}
            onKeyDown={event => {
              if (["Home", "End", "Enter", " "].includes(event.key)) {
                if (event.key === "Enter" || event.key === " ") event.preventDefault();
                setDraftHeight(event.key === "Home" ? Number(descriptor.minimum) : event.key === "End" ? Number(descriptor.maximum) : Number(event.currentTarget.value));
              }
            }}
            onChange={event => setDraftHeight(Number(event.target.value))}/><strong data-testid="operator-height-value">{chosen ? displayValue(value, descriptor, language) : "—"}</strong></div>
        </Field>
        <Button data-testid="operator-height-apply" disabled={!client.canMove || !heightAvailable || !!client.busy || !valid || !chosen}
          onClick={() => { if (valid) void client.command({ action: "height", height: value }).then(response => { if (response) setDraftHeight(null); }); }}>{t("apply")}</Button>
      </div>
      <div className={styles.operatorBalance}>
        <div><span>{t("balance")}</span><strong data-testid="operator-balance-state">{t(balance.enabled === true ? "on" : balance.enabled === false ? "off" : "unknown")}</strong></div>
        <Button data-testid="operator-balance" aria-pressed={balance.enabled === true}
          disabled={!client.canMove || balanceDescriptor.type !== "boolean" || record(capabilities.balance).available === false || !!client.busy}
          onClick={() => void client.command({ action: "balance", enabled: balance.enabled !== true })}>{t(balance.enabled === true ? "disable" : "enable")}</Button>
      </div>
    </div>;
  }
  return <Panel title={t("posture")}>
    {heightAvailable && height !== undefined ? <><Field label={`${t("height")} · ${descriptor.display_unit ?? descriptor.unit ?? ""}`}><div className={`${styles.flex} ${styles.space}`}><Range type="range" min={descriptor.minimum} max={descriptor.maximum} step={descriptor.step} value={height} onChange={event => setDraftHeight(Number(event.target.value))} disabled={!client.canMutate} /><strong>{displayValue(height, descriptor, language)}</strong></div></Field><p className={styles.hint}>{t("heightHint")}</p><div className={`${styles.flex} ${styles.space}`}><span className={styles.small}>{t("heightTarget")}: {displayValue(target.value, descriptor, language)} {descriptor.display_unit} · {formatTime(target.ts, language)}</span><Button disabled={!client.canMove || client.busy === "height" || !finite(height)} onClick={() => void client.command({ action: "height", height })}>{t("apply")}</Button></div></> : <Empty>{t("unavailable")}</Empty>}
    <div className={styles.rule} />
    <div className={`${styles.flex} ${styles.space}`}><div><strong style={{ fontSize: 13 }}>{t("balance")}</strong><p className={styles.hint} style={{ marginBottom: 0 }}>{t("balanceHint")}</p></div><Button aria-pressed={balance.enabled === true} disabled={!client.canMove || (balanceDescriptor.type !== "boolean" || record(capabilities.balance).available === false) || client.busy === "balance"} onClick={() => void client.command({ action: "balance", enabled: balance.enabled !== true })}>{t(balance.enabled === true ? "disable" : "enable")}</Button></div>
    <div className={styles.meta}>{t("state")}: {t(balance.enabled === true ? "on" : balance.enabled === false ? "off" : "unknown")}</div>
  </Panel>;
}
export function CameraPanel({ client, t, language, compact = false, showTools = true, onObservation, observationDisabled = false, editor }: { client: RiderClient; t: Translator; language: string; compact?: boolean; showTools?: boolean; onObservation?:()=>void; observationDisabled?:boolean; editor?:CorridorEditorProps }) {
  const [imageSize,setImageSize]=useState<[number,number]>([320,240]);
  const imageReference=useImageReference(client.currentSession());
  const [displayed,setDisplayed]=useState<Frame|null>(null);
  // Explicit model results disappear on mismatch, expiry or failed inference.
  const modelPreview = client.cameraStream === "venom" && client.perceptionSelection !== undefined;
  const candidate = !framePipelineCurrent(client.camera, client.pipeline.status) || modelPreview && !perceptionFrameCurrent(client.camera, client.perceptionSelection) ? null : client.camera;
  const displayedModel = !framePipelineCurrent(displayed, client.pipeline.status) || modelPreview && !perceptionFrameCurrent(displayed, client.perceptionSelection) ? null : displayed;
  // The candidate decodes offscreen before the visible img fires its load event.
  const visible = displayedPreview(candidate, displayedModel);
  useEffect(() => {
    const frame = displayedPreview(candidate, displayedModel);
    if (!frame || frame.stale) return;
    const remaining = previewLifetime(frame);
    if (remaining === null) return;
    const timer = setTimeout(() => setDisplayed(previous => previous?.url === frame.url
      ? { ...previous, stale: true, knownFreshness: false } : previous), remaining);
    return () => clearTimeout(timer);
  }, [candidate, displayedModel]);
  const [previewBusy,setPreviewBusy]=useState(false);
  const [previewFailure,setPreviewFailure]=useState("");
  const chooseStream=async(stream:"raw"|"proc"|"venom"|null)=>{
    if(!stream){client.setCameraStream(null);setDisplayed(null);return;}
    setPreviewBusy(true);setPreviewFailure("");
    try{
      if(!client.cameraStream) {
        if (!client.pipeline.status) await client.mutate("/goal/s2_camera_preview",{enabled:true},"POST",true);
      }
      if(stream==="venom") await client.mutate("/perception/prepare",{},"POST",true);
      client.setCameraStream(stream);
    }catch{setPreviewFailure("requestFailed");}
    finally{setPreviewBusy(false);}
  };
  const power = client.pipeline.status;
  const cameraOn = !!power && ["active", "starting", "waiting_frame"].includes(power.state);
  const cameraButtonLabel = t(!power ? "cockpitCameraUnknown" : cameraOn ? "cameraSwitchOff" : "cameraSwitchOn");
  const cameraButtonName = cameraButtonLabel;
  const changePower = async () => {
    setPreviewFailure("");
    try {
      const next = await client.pipeline.change(cameraOn ? "stop" : "start");
      // A submitted request or a still-active foreign consumer is not OFF.
      if (next?.state === "stopped") { client.setCameraStream(null); setDisplayed(null); }
      else if (!cameraOn && next) client.setCameraStream("raw");
    } catch { setPreviewFailure("pipeline.failure"); }
  };
  const selected = client.cameraStream ?? "raw";
  const transitioning=selected==="venom"&&client.cameraTransition;
  const previewPolling=client.cameraStream!==null&&client.mediaError!=="visionCameraOff"&&!["stopped","failed"].includes(power?.state??"");
  const notice=previewNotice(visible?.stale??client.camera?.stale??false,selected,previewPolling,transitioning);
  const sourceIdentity = visible?.analysis && Object.keys(record(visible.analysis.source)).length
    ? record(visible.analysis.source) : visible?.analysis;
  const modelLabel = String(visible?.analysis?.model_id || client.perceptionSelection?.model_id || t("perception.previewWaiting"));
  const modelDetails = modelLabel + " · " + t(modelLabel.startsWith("depth-anything") ? "perception.geometryLegendShort" : "perception.semanticLegendShort");
  const streams = client.pipeline.status?.profile.analysis_location === "local"
    ? (["raw", "proc"] as const) : compact ? (["raw", "venom"] as const) : (["raw", "venom", "proc"] as const);
  return <Panel className={compact?styles.cameraEmbedded:""} title={t("camera")} hint={compact?undefined:t("cameraHint")} extra={<div className={styles.cameraToolbar}>
      <div className={styles.cameraSources}>{streams.map(stream => <Button key={stream} aria-label={t(stream === "venom" ? "visionVenom" : stream === "proc" ? "visionPi" : stream)} title={stream === "venom" ? modelDetails : undefined} tone={selected === stream ? "primary" : undefined} aria-pressed={selected === stream} onClick={() => void chooseStream(stream)} disabled={!client.canMutate||previewBusy}>{t(compact ? stream === "raw" ? "workspace.raw" : stream === "venom" ? "workspace.analysis" : "visionPi" : stream === "venom" ? "visionVenom" : stream === "proc" ? "visionPi" : stream)}</Button>)}</div>
      {compact&&<span className={styles.cameraModel} title={modelDetails}>{selected==="venom"?modelLabel:""}</span>}
      <ImageReferenceControls reference={imageReference} client={client} size={imageSize} t={t}/>
      <span data-testid="camera-power-state"><Pill tone={power?.state === "active" ? "good" : "warning"}>{t(power ? "pipeline.state." + power.state : "unknown")}</Pill></span><Button aria-label={cameraButtonName} data-testid="camera-power-toggle" tone={cameraOn ? "caution" : undefined} title={t(!power ? client.pipeline.supported === false ? "pipeline.legacy" : client.pipeline.error ? "pipeline.readFailed" : "pipeline.loading" : "cameraHint")} onClick={() => void changePower()} disabled={!power||!client.canMutate||client.pipeline.busy||power.state === "stopping"}>{cameraButtonLabel}</Button>
      <Button aria-label={t(client.cameraStream ? "hidePreview" : "showPreview")} data-testid="camera-preview-toggle" title={t(client.cameraStream ? "hidePreview" : "showPreview")} onClick={() => void chooseStream(client.cameraStream ? null : "raw")} disabled={!client.canMutate||previewBusy}>{client.cameraStream ? <EyeOff size={15}/> : <Eye size={15}/>} {!compact && t(client.cameraStream ? "hidePreview" : "showPreview")}</Button>
    </div>}>
    {!compact&&<div hidden={selected!=="venom"} className={styles.perceptionPreviewMeta} data-testid="perception-preview-result" aria-live="polite">
      <strong>{visible?.analysis ? String(visible.analysis.model_id || (visible.analysis.perception_engine === "depth_plane" ? "Depth Anything V2" : "SegFormer-B0")) : t("perception.previewWaiting")}</strong>
      {visible?.analysis && <span>{t("frameSequence")}: {String(sourceIdentity?.sequence)} · {String(sourceIdentity?.frame_id).slice(0,12)} · {Math.round(frameAge(visible) ?? 0)} ms · {t("perception.inference")}: {visible.inferenceMs || "N/A"} ms · r{String(visible.analysis.config_revision)}</span>}
      <span title={t(visible?.analysis?.perception_engine === "depth_plane" || String(visible?.analysis?.model_id ?? "").startsWith("depth-anything") ? "perception.geometryLegend" : "perception.semanticLegend")}>{t(visible?.analysis?.perception_engine === "depth_plane" || String(visible?.analysis?.model_id ?? "").startsWith("depth-anything") ? "perception.geometryLegendShort" : "perception.semanticLegendShort")}</span>
    </div>}
    {selected === "proc" && <p className={styles.small} data-testid="perception-local-preview">{t("perception.location.local")} · {candidate ? client.pipeline.status?.active_profile?.local_engine.toUpperCase() ?? t("perception.runtime.unavailable") : t("perception.previewWaiting")}</p>}
    <div className={styles.camera} data-testid="camera-surface">{candidate&&notice&&<div className={transitioning?styles.cameraPaused+" "+styles.cameraTransition:styles.cameraPaused} role="status" data-testid="vision-preview-paused">{t(notice!)}</div>}{candidate ? <><img data-capture-key={candidate.captureKey} src={candidate.url} alt={t("cameraAlt")} onLoad={event=>{const {naturalWidth,naturalHeight}=event.currentTarget;setImageSize(previous=>previous[0]===naturalWidth&&previous[1]===naturalHeight?previous:[naturalWidth,naturalHeight]);client.markCameraDecoded(event.currentTarget.currentSrc,true);if(candidate&&event.currentTarget.currentSrc===candidate.url)setDisplayed(candidate);}} onError={event=>{client.markCameraDecoded(event.currentTarget.currentSrc,false);setDisplayed(null);}} />{(selected==="venom"||imageReference.editing)&&<ImageReferenceOverlay reference={imageReference} size={imageSize} t={t} geometry={visible?.analysis?.overlay_geometry}/>}
{editor&&<CorridorOverlay editor={editor} size={imageSize} t={t}/>}</> : <Empty><Camera size={32} />{t(transitioning?"visionPreviewRestarting":client.mediaError ?? (modelPreview ? "perception.previewWaiting" : client.cameraStream ? "frameMissing" : "showPreview"))}{onObservation&&<><p className={styles.hint}>{t("loopPreviewHelp")}</p><Button tone="primary" disabled={observationDisabled} onClick={onObservation}>{t("loopPreparePreview")}</Button></>}</Empty>}</div>
    {previewFailure&&<p role="alert" className={styles.hint}>{t(previewFailure)}</p>}
    {editor&&<p className={styles.hint} data-testid="sector-draft-hint">{t("sectorDraftHint")}</p>}
    {(selected === "venom"||compact) && <VisionSectors frame={selected === "venom" ? visible : null} t={t} compact={compact} concealed={compact&&selected!=="venom"}/>}
    {showTools&&<CameraDetails client={client} t={t} language={language}/>}
  </Panel>;
}


export function CameraDetails({client,t,language}:{client:RiderClient;t:Translator;language:string}) {
  const visible=client.camera, selected=client.cameraStream;
  const sourceIdentity=visible?.analysis&&Object.keys(record(visible.analysis.source)).length?record(visible.analysis.source):visible?.analysis;
  return <details className={styles.workspaceDisclosure} data-testid="camera-settings"><summary>{t("cameraTools")}</summary><div className={styles.disclosureBody}>
    <CameraPipelineControls client={client} t={t}/>
    {selected==="venom"&&<p className={styles.hint}>{t("visionLegend")}</p>}
    {visible?.visionState&&<p className={styles.hint}>{t("state")}: {t("vision."+visible.visionState)} · {visible.inferenceMs} ms · <code>{visible.visionReason}</code></p>}
    {visible?.analysis&&<p className={styles.small} data-testid="frame-identity">{t("frameSequence")}: {String(sourceIdentity?.sequence)} · {String(sourceIdentity?.frame_id).slice(0,12)} · {t("frameRelativeAge")}: {Math.round(frameAge(visible)??0)} ms · r{String(visible.analysis.config_revision)}</p>}
    {visible&&<p className={styles.small}>{t(visible.stale?"framePaused":visible.knownFreshness?"frameFresh":"frameTimeUnknown")} · {formatTime(Number(visible.fileAt),language)}</p>}
  </div></details>;
}
