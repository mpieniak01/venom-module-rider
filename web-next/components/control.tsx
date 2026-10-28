import {useImageReference,ImageReferenceControls,ImageReferenceOverlay} from "./image-reference";
import { VisionSectors } from "./vision-sectors";
import { CameraPipelineControls } from "./camera-pipeline";
import { framePipelineCurrent } from "../lib/camera-pipeline";
import { CorridorOverlay, type CorridorEditorProps } from "./corridor-overlay";
import { manualFeedback } from "../lib/manual-feedback";
import { frameAge } from "../lib/vision-frame";
import { previewNotice } from "../lib/loop-controls";
import { perceptionFrameCurrent } from "../lib/perception";
/* eslint-disable @next/next/no-img-element -- Pi binary frames use revocable object URLs. */
import { useEffect, useId, useState, type ReactNode, type Dispatch, type SetStateAction } from "react";
import { displayedPreview, previewLifetime } from "../lib/preview-media";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, RotateCcw, RotateCw, Camera, Eye, EyeOff, CirclePause, CircleStop } from "lucide-react";
import { actuatorState, displayValue, finite, formatTime, record, safetyOf, settingsOf, type Direction, type NumericDescriptor, type JsonRecord } from "../lib/model";
import type { Translator } from "../lib/i18n";
import type { Frame, RiderClient } from "../lib/use-rider";
import { Button, Empty, Field, Panel, Pill, Range, Select } from "./primitives";
import styles from "../rider.module.css";

const arrows: {direction:Direction;row:number;column:number;Icon:typeof ArrowUp;turnMode?:"in_place"}[] = [
  { direction: "forward" as const, row: 1, column: 2, Icon: ArrowUp },
  { direction: "left" as const, row: 2, column: 1, Icon: ArrowLeft },
  { direction: "right" as const, row: 2, column: 3, Icon: ArrowRight },
  { direction: "backward" as const, row: 3, column: 2, Icon: ArrowDown },
  { direction: "left", row: 3, column: 1, Icon: RotateCcw, turnMode:"in_place" },
  { direction: "right", row: 3, column: 3, Icon: RotateCw, turnMode:"in_place" },
];
type ManualArrowProps={client:RiderClient;t:Translator;language:string;compact:boolean;continuous:boolean;available:boolean;settings:JsonRecord;directions:JsonRecord;speedDescriptor:NumericDescriptor;arrow:(typeof arrows)[number];pointerDown:(direction:Direction,event:React.PointerEvent<HTMLButtonElement>,turnMode?:"in_place")=>void};
/** Keep each direction's pointer and keyboard gesture ownership local to its button. */
function ManualArrow({client,t,language,compact,continuous,available,settings,directions,speedDescriptor,arrow,pointerDown}:ManualArrowProps){
  const {direction,row,column,Icon,turnMode}=arrow;
        const label=turnMode?t(direction==="left"?"turnInPlaceLeft":"turnInPlaceRight"):t(direction);
        const enabled=available&&(!turnMode||record(settings.control_modes).in_place_turn_version===1);
        const active = client.activeDirection === direction && (client.manualTurnMode??null)===(turnMode??null) && client.manualMode === (continuous ? "continuous" : "hold");
        return <button type="button" aria-label={label} title={label} aria-pressed={active} disabled={!enabled} data-testid={turnMode?"rider-spin-"+direction:"rider-"+direction} className={`${styles.arrow} ${active ? styles.arrowActive : ""}`} style={{ gridRow: row, gridColumn: column }}
          onPointerDown={event => pointerDown(direction, event, turnMode)}
          onPointerUp={event => { if (!continuous) client.hold.end(event.pointerId); }}
          onPointerCancel={event => { if (!continuous) client.hold.release(event.pointerId); }}
          onLostPointerCapture={event => { if (!continuous) client.hold.release(event.pointerId); }}
          onClick={event => {
            if (!enabled) return;
            if (continuous) client.hold.toggle(direction, turnMode);
            else if (event.detail === 0 && !client.activeDirection) { client.hold.start(direction, -2, turnMode); client.hold.end(-2); }
          }}
          onKeyDown={event => {
            if (!["Enter", " "].includes(event.key)) return;
            if (event.repeat) { event.preventDefault(); return; }
            if (!continuous && enabled) { event.preventDefault(); client.hold.start(direction, -1, turnMode); }
          }}
          onKeyUp={event => {
            if (!continuous && ["Enter", " "].includes(event.key)) { event.preventDefault(); client.hold.end(-1); }
          }}
          onBlur={() => client.hold.releasePointer()}>
          <Icon size={compact ? 20 : 25} />{turnMode?<span className={styles.small}>{t("turnInPlace")}</span>:!compact && <span>{t(direction)}</span>}{!turnMode&&<span className={styles.small}>{displayValue(record(directions[direction]).speed, speedDescriptor, language)} {speedDescriptor.display_unit ?? ""}</span>}
        </button>;
}
/** Translate only the known safety state values used by the existing badges. */
function safetyLabel(state:unknown):string {
  if(state==="ARMED")return "armed";
  if(state==="ESTOP")return "estop";
  if(state==="DISARMED")return "disarmed";
  return "unknown";
}
/** Render separate legacy and in-place gestures using Pi-owned motion settings. */
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
  const pointerDown = (direction: Direction, event: React.PointerEvent<HTMLButtonElement>, turnMode?: "in_place") => {
    if (continuous || !available || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    client.hold.start(direction, event.pointerId, turnMode);
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
      {arrows.map(arrow => <ManualArrow key={arrow.direction+(arrow.turnMode??"")} client={client} t={t} language={language} compact={compact} continuous={continuous} available={available} settings={settings} directions={directions} speedDescriptor={speedDescriptor} arrow={arrow} pointerDown={pointerDown}/>)}
      <button type="button" className={`${styles.padCenter} ${styles.padStop}`} style={{ gridRow: 2, gridColumn: 2 }} aria-label={t("stopMotors")} title={t("stopHint")} data-testid="rider-pad-stop" onClick={() => void client.stopManual().catch(() => undefined)}><CircleStop size={27} /><span>{t("stopShort")}</span></button>
    </div>
    {safety.state==="ESTOP"&&<Button data-testid="rider-clear-estop" disabled={!!client.busy} onClick={()=>void client.command({action:"clear_estop",operator_consent:true,operator_plan:t("clearStop")})}>{t("clearStop")}</Button>}
    <ManualPadFeedback client={client} t={t} minimal={minimal} continuous={continuous} safety={safety} feedback={feedback} helpId={helpId}>{children}</ManualPadFeedback>
  </div>;
}
/** Render manual status badges without changing their gesture or safety meaning. */
function manualStatus(client:RiderClient,t:Translator,safety:JsonRecord){
  return <div className={styles.padStatus}><Pill tone={safety.state === "ARMED" ? "good" : "warning"}>{t(safetyLabel(safety.state))}</Pill>{(client.status?.errors.control || safety.stale === true) && <Pill tone="warning">{t("lastPermission")}</Pill>}{!!record(safety.execution).inhibit && <Pill tone="warning">{t("executionPaused")}</Pill>}{client.activeDirection && <Pill tone="good">{t(client.manualMode === "continuous" ? "continuousPad" : "held")} · {t(client.activeDirection)}</Pill>}</div>;
}
type ManualFeedbackProps={client:RiderClient;t:Translator;minimal:boolean;continuous:boolean;safety:JsonRecord;feedback:ReturnType<typeof manualFeedback>;helpId:string;children?:ReactNode};
/** Render the same compact or detailed manual readout outside gesture setup. */
function ManualPadFeedback({client,t,minimal,continuous,safety,feedback,helpId,children}:ManualFeedbackProps){
  if(minimal)return <div className={styles.manualReadout} data-testid="manual-feedback">
      <div role="status"><Pill tone={safety.state==="ARMED"?"good":"warning"}>{t(safetyLabel(safety.state))}</Pill>{client.activeDirection&&<span>{t(client.activeDirection)}</span>}</div>
      {children}
    </div>;
  return <div className={styles.padDetails}>
    <div className={styles.padHelp} id={helpId}>
      <p className={styles.hint}>{t(continuous ? "manualModeContinuousHint" : "manualModePulseHint")}</p>
      {continuous && !client.canContinuous && <p className={styles.hint} role="status">{t(record(safety.panel_control).version === 1 ? "continuousTakeover" : "continuousUnsupported")}</p>}
    </div>
    <div className={styles.manualFeedback} data-testid="manual-feedback" aria-live="polite">
      <span>{t("manualIntent")}: <strong>{client.activeDirection ? `${t(client.manualMode === "continuous" ? "continuousPad" : "pulsePad")} · ${t(client.activeDirection)}` : t("manualNoDirection")}</strong></span>
      <span>{t("manualAck")}: <strong>{t(feedback.requestKey)}</strong></span>
      <span>{t("manualOutput")}: <strong>{t(feedback.executionKey)}</strong> · {feedback.output}</span>
    </div>
    {manualStatus(client,t,safety)}
    <div className={`${styles.flex} ${styles.space}`} style={{ marginTop: 15 }}><span className={styles.small}>{t("owner")}: {typeof safety.owner === "string" ? safety.owner : "—"}</span><Button onClick={() => client.hold.release()} disabled={!client.activeDirection}><CirclePause size={15} />{t("endDirection")}</Button></div>
      {children}
    </div>;
}
/** Present posture controls using the current device capability descriptors. */
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
  if (compact) return <CompactPosture client={client} t={t} language={language} draftHeight={draftHeight} setDraftHeight={setDraftHeight} target={target} descriptor={descriptor} heightAvailable={heightAvailable} balance={balance} balanceDescriptor={balanceDescriptor} capabilities={capabilities}/>;
  return <Panel title={t("posture")}>
    {heightAvailable && height !== undefined ? <><Field label={`${t("height")} · ${descriptor.display_unit ?? descriptor.unit ?? ""} (${descriptor.minimum}–${descriptor.maximum})`}><div className={`${styles.flex} ${styles.space}`}><Range type="range" min={descriptor.minimum} max={descriptor.maximum} step={descriptor.step} value={height} onChange={event => setDraftHeight(Number(event.target.value))} disabled={!client.canMutate} /><strong>{displayValue(height, descriptor, language)}</strong></div></Field><p className={styles.hint}>{t("heightHint")}</p><div className={`${styles.flex} ${styles.space}`}><span className={styles.small}>{t("heightTarget")}: {displayValue(target.value, descriptor, language)} {descriptor.display_unit} · {formatTime(target.ts, language)}</span><Button disabled={!(client.canSetHeight ?? client.canMove) || !!client.busy || !finite(height)} onClick={() => void client.command({ action: "height", height })}>{t("apply")}</Button></div></> : <Empty>{t("unavailable")}</Empty>}
    <div className={styles.rule} />
    <div className={`${styles.flex} ${styles.space}`}><div><strong style={{ fontSize: 13 }}>{t("balance")}</strong><p className={styles.hint} style={{ marginBottom: 0 }}>{t("balanceHint")}</p></div><Button aria-pressed={balance.enabled === true} disabled={!(client.canSetBalance ?? client.canMove) || (balanceDescriptor.type !== "boolean" || record(capabilities.balance).available === false) || client.busy === "balance"} onClick={() => void client.command({ action: "balance", enabled: balance.enabled !== true })}>{t(balance.enabled === true ? "disable" : "enable")}</Button></div>
    <div className={styles.meta}>{t("state")}: {t(balance.enabled === true ? "on" : balance.enabled === false ? "off" : "unknown")}</div>
  </Panel>;
}
type CompactPostureProps={client:RiderClient;t:Translator;language:string;draftHeight:number|null;setDraftHeight:(value:number|null)=>void;target:JsonRecord;descriptor:NumericDescriptor;heightAvailable:boolean;balance:JsonRecord;balanceDescriptor:JsonRecord;capabilities:JsonRecord};
/** Keep compact posture editing separate from the ordinary calibration layout. */
function CompactPosture({client,t,language,draftHeight,setDraftHeight,target,descriptor,heightAvailable,balance,balanceDescriptor,capabilities}:CompactPostureProps){
    const value = draftHeight ?? (finite(target.value) ? target.value : descriptor.minimum);
    const chosen = draftHeight !== null || finite(target.value);
    const valid = finite(value) && Number.isInteger(value) && finite(descriptor.minimum) && finite(descriptor.maximum)
      && value >= descriptor.minimum && value <= descriptor.maximum
      && (!finite(descriptor.step) || descriptor.step <= 0 || Number.isInteger((value - descriptor.minimum) / descriptor.step));
    return <div className={styles.operatorPosture} data-testid="operator-posture">
      <div className={styles.operatorHeight}>
        <Field label={t("operator.height") + " · " + (descriptor.display_unit ?? "SDK") + " (" + descriptor.minimum + "–" + descriptor.maximum + ")"}>
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
        <Button data-testid="operator-height-apply" disabled={!(client.canSetHeight ?? client.canMove) || !heightAvailable || !!client.busy || !valid || !chosen}
          onClick={() => { if (valid) void client.command({ action: "height", height: value }).then(response => { if (response) setDraftHeight(null); }); }}>{t("apply")}</Button>
      </div>
      <div className={styles.operatorBalance}>
        <div><span>{t("balance")}</span><strong data-testid="operator-balance-state">{t(balance.enabled === true ? "on" : balance.enabled === false ? "off" : "unknown")}</strong></div>
        <Button data-testid="operator-balance" aria-pressed={balance.enabled === true}
          disabled={!(client.canSetBalance ?? client.canMove) || balanceDescriptor.type !== "boolean" || record(capabilities.balance).available === false || !!client.busy}
          onClick={() => void client.command({ action: "balance", enabled: balance.enabled !== true })}>{t(balance.enabled === true ? "disable" : "enable")}</Button>
      </div>
    </div>;
}
type CameraStream="raw"|"proc"|"venom";
/** Discard only frames outside the captured pipeline/model selection. */
function matchingPreview(frame:Frame|null,pipeline:RiderClient["pipeline"]["status"],modelPreview:boolean,selection:RiderClient["perceptionSelection"]):Frame|null {
  if(!framePipelineCurrent(frame,pipeline)||modelPreview&&!perceptionFrameCurrent(frame,selection))return null;
  return frame;
}
/** Preserve the local, compact and full stream choices. */
function cameraStreams(client:RiderClient,compact:boolean):readonly CameraStream[] {
  if(client.pipeline.status?.profile.analysis_location==="local")return ["raw","proc"];
  return compact?["raw","venom"]:["raw","venom","proc"];
}
type CameraViewContext={client:RiderClient;t:Translator;compact:boolean;selected:CameraStream;modelDetails:string;modelLabel:string;streams:readonly CameraStream[];chooseStream:(stream:CameraStream|null)=>Promise<void>;previewBusy:boolean;imageReference:ReturnType<typeof useImageReference>;imageSize:[number,number];power:RiderClient["pipeline"]["status"];cameraOn:boolean;cameraButtonName:string;cameraButtonLabel:string;changePower:()=>Promise<void>};
/** Keep stream labels stable across the compact/full layouts. */
function streamLabel(stream:CameraStream,compact:boolean):string {
  if(compact){if(stream==="raw")return "workspace.raw";return stream==="venom"?"workspace.analysis":"visionPi";}
  if(stream==="venom")return "visionVenom";
  return stream==="proc"?"visionPi":stream;
}
/** Describe missing native state without treating an accepted command as OFF. */
function cameraPowerHint(context:CameraViewContext):string {
  if(context.power)return "cameraHint";
  if(context.client.pipeline.supported===false)return "pipeline.legacy";
  return context.client.pipeline.error?"pipeline.readFailed":"pipeline.loading";
}
/** Render one source selector without nesting its labels in the toolbar. */
function cameraStreamButton(stream:CameraStream,context:CameraViewContext){
  const {client,t,selected,modelDetails,chooseStream,previewBusy,compact}=context;
  return <Button key={stream} aria-label={t(streamLabel(stream,false))} title={stream==="venom"?modelDetails:undefined} tone={selected===stream?"primary":undefined} aria-pressed={selected===stream} onClick={()=>void chooseStream(stream)} disabled={!client.canMutate||previewBusy}>{t(streamLabel(stream,compact))}</Button>;
}
/** Render camera controls independently of frame decoding and retention state. */
function cameraToolbar(context:CameraViewContext){
  const {client,t,compact,selected,modelDetails,modelLabel,streams,chooseStream,previewBusy,imageReference,imageSize,power,cameraOn,cameraButtonName,changePower,cameraButtonLabel}=context;
  return <div className={styles.cameraToolbar}>
      <div className={styles.cameraSources}>{streams.map(stream=>cameraStreamButton(stream,context))}</div>
      {compact&&<span className={styles.cameraModel} title={modelDetails}>{selected==="venom"?modelLabel:""}</span>}
      <ImageReferenceControls reference={imageReference} client={client} size={imageSize} t={t}/>
      <span data-testid="camera-power-state"><Pill tone={power?.state === "active" ? "good" : "warning"}>{t(power ? "pipeline.state." + power.state : "unknown")}</Pill></span><Button aria-label={cameraButtonName} data-testid="camera-power-toggle" tone={cameraOn ? "caution" : undefined} title={t(cameraPowerHint(context))} onClick={() => void changePower()} disabled={!power||!client.canMutate||client.pipeline.busy||power.state === "stopping"}>{cameraButtonLabel}</Button>
      <Button aria-label={t(client.cameraStream ? "hidePreview" : "showPreview")} data-testid="camera-preview-toggle" title={t(client.cameraStream ? "hidePreview" : "showPreview")} onClick={() => void chooseStream(client.cameraStream ? null : "raw")} disabled={!client.canMutate||previewBusy}>{client.cameraStream ? <EyeOff size={15}/> : <Eye size={15}/>} {!compact && t(client.cameraStream ? "hidePreview" : "showPreview")}</Button>
    </div>;
}
/** Describe model interpretation separately from the actual visible frame. */
function cameraResultMetadata(visible:Frame|null,sourceIdentity:JsonRecord|undefined,selected:CameraStream,t:Translator){
  return <div hidden={selected!=="venom"} className={styles.perceptionPreviewMeta} data-testid="perception-preview-result" aria-live="polite">
      <strong>{visible?.analysis ? String(visible.analysis.model_id || (visible.analysis.perception_engine === "depth_plane" ? "Depth Anything V2" : "SegFormer-B0")) : t("perception.previewWaiting")}</strong>
      {visible?.analysis && <span>{t("frameSequence")}: {String(sourceIdentity?.sequence)} · {String(sourceIdentity?.frame_id).slice(0,12)} · {Math.round(frameAge(visible) ?? 0)} ms · {t("perception.inference")}: {visible.inferenceMs || "N/A"} ms · r{String(visible.analysis.config_revision)}</span>}
      <span title={t(visible?.analysis?.perception_engine === "depth_plane" || String(visible?.analysis?.model_id ?? "").startsWith("depth-anything") ? "perception.geometryLegend" : "perception.semanticLegend")}>{t(visible?.analysis?.perception_engine === "depth_plane" || String(visible?.analysis?.model_id ?? "").startsWith("depth-anything") ? "perception.geometryLegendShort" : "perception.semanticLegendShort")}</span>
    </div>;
}
type CameraSurfaceContext={client:RiderClient;t:Translator;candidate:Frame|null;visible:Frame|null;notice:string|null;transitioning:boolean;selected:CameraStream;imageReference:ReturnType<typeof useImageReference>;imageSize:[number,number];setImageSize:Dispatch<SetStateAction<[number,number]>>;setDisplayed:Dispatch<SetStateAction<Frame|null>>;editor?:CorridorEditorProps;modelPreview:boolean;onObservation?:()=>void;observationDisabled:boolean};
/** Select the empty-preview message without changing camera or model state. */
function emptyPreviewKey(context:CameraSurfaceContext):string {
  if(context.transitioning)return "visionPreviewRestarting";
  if(context.client.mediaError !== null && context.client.mediaError !== undefined)return context.client.mediaError;
  if(context.modelPreview)return "perception.previewWaiting";
  return context.client.cameraStream?"frameMissing":"showPreview";
}
/** Render a decoded candidate and keep captured identity checks in its handlers. */
function cameraSurface(context:CameraSurfaceContext){
  const {client,t,candidate,visible,notice,transitioning,selected,imageReference,imageSize,setImageSize,setDisplayed,editor,onObservation,observationDisabled}=context;
  return <div className={styles.camera} data-testid="camera-surface">{candidate&&notice&&<div className={transitioning?styles.cameraPaused+" "+styles.cameraTransition:styles.cameraPaused} role="status" data-testid="vision-preview-paused">{t(notice!)}</div>}{candidate ? <><img data-capture-key={candidate.captureKey} src={candidate.url} alt={t("cameraAlt")} onLoad={event=>{const {naturalWidth,naturalHeight}=event.currentTarget;setImageSize(previous=>previous[0]===naturalWidth&&previous[1]===naturalHeight?previous:[naturalWidth,naturalHeight]);client.markCameraDecoded(event.currentTarget.currentSrc,true);if(candidate&&event.currentTarget.currentSrc===candidate.url)setDisplayed(candidate);}} onError={event=>{client.markCameraDecoded(event.currentTarget.currentSrc,false);setDisplayed(null);}} />{(selected==="venom"||imageReference.editing)&&<ImageReferenceOverlay reference={imageReference} size={imageSize} t={t} geometry={visible?.analysis?.overlay_geometry}/>}
{editor&&<CorridorOverlay editor={editor} size={imageSize} t={t}/>}</> : <Empty><Camera size={32} />{t(emptyPreviewKey(context))}{onObservation&&<><p className={styles.hint}>{t("loopPreviewHelp")}</p><Button tone="primary" disabled={observationDisabled} onClick={onObservation}>{t("loopPreparePreview")}</Button></>}</Empty>}</div>;
}
/** Read the pipeline power label without inferring an unacknowledged stop. */
function cameraPowerState(client:RiderClient,t:Translator){
  const power=client.pipeline.status;
  const cameraOn=!!power&&["active","starting","waiting_frame"].includes(power.state);
  const cameraButtonLabel=t(!power?"cockpitCameraUnknown":cameraOn?"cameraSwitchOff":"cameraSwitchOn");
  return {power,cameraOn,cameraButtonLabel};
}
/** Prefer an explicit source record while retaining older analysis metadata. */
function analysisSource(visible:Frame|null):JsonRecord|undefined {
  return visible?.analysis&&Object.keys(record(visible.analysis.source)).length?record(visible.analysis.source):visible?.analysis;
}
/** Resolve the model label from the accepted result before the selected draft. */
function previewModelLabel(visible:Frame|null,client:RiderClient,t:Translator):string {
  return String(visible?.analysis?.model_id||client.perceptionSelection?.model_id||t("perception.previewWaiting"));
}
/** Describe the local processed stream without treating a missing frame as a result. */
function localPreviewDetails(selected:CameraStream,candidate:Frame|null,client:RiderClient,t:Translator){
  if(selected!=="proc")return null;
  return <p className={styles.small} data-testid="perception-local-preview">{t("perception.location.local")} · {candidate?client.pipeline.status?.active_profile?.local_engine.toUpperCase()??t("perception.runtime.unavailable"):t("perception.previewWaiting")}</p>;
}
/** Present the current camera candidate, decoded display and operator controls. */
export function CameraPanel({ client, t, language, compact = false, showTools = true, onObservation, observationDisabled = false, editor }: { client: RiderClient; t: Translator; language: string; compact?: boolean; showTools?: boolean; onObservation?:()=>void; observationDisabled?:boolean; editor?:CorridorEditorProps }) {
  const [imageSize,setImageSize]=useState<[number,number]>([320,240]);
  const imageReference=useImageReference(client.currentSession());
  const [displayed,setDisplayed]=useState<Frame|null>(null);
  // Explicit model results disappear on mismatch, expiry or failed inference.
  const modelPreview = client.cameraStream === "venom" && client.perceptionSelection !== undefined;
  const candidate = matchingPreview(client.camera,client.pipeline.status,modelPreview,client.perceptionSelection);
  const displayedModel = matchingPreview(displayed,client.pipeline.status,modelPreview,client.perceptionSelection);
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
  const {power,cameraOn,cameraButtonLabel}=cameraPowerState(client,t);
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
  const sourceIdentity=analysisSource(visible);
  const modelLabel=previewModelLabel(visible,client,t);
  const modelDetails = modelLabel + " · " + t(modelLabel.startsWith("depth-anything") ? "perception.geometryLegendShort" : "perception.semanticLegendShort");
  const streams=cameraStreams(client,compact);
  return <Panel className={compact?styles.cameraEmbedded:""} title={t("camera")} hint={compact?undefined:t("cameraHint")} extra={cameraToolbar({client,t,compact,selected,modelDetails,modelLabel,streams,chooseStream,previewBusy,imageReference,imageSize,power,cameraOn,cameraButtonName,changePower,cameraButtonLabel})}>
    {!compact&&cameraResultMetadata(visible,sourceIdentity,selected,t)}
    {localPreviewDetails(selected,candidate,client,t)}
    {cameraSurface({client,t,candidate,visible,notice,transitioning,selected,imageReference,imageSize,setImageSize,setDisplayed,editor,modelPreview,onObservation,observationDisabled})}
    {previewFailure&&<p role="alert" className={styles.hint}>{t(previewFailure)}</p>}
    {editor&&<p className={styles.hint} data-testid="sector-draft-hint">{t("sectorDraftHint")}</p>}
    {(selected === "venom"||compact) && <VisionSectors frame={selected === "venom" ? visible : null} t={t} compact={compact} concealed={compact&&selected!=="venom"}/>}
    {showTools&&<CameraDetails client={client} t={t} language={language}/>}
  </Panel>;
}


/** Render immutable frame provenance without inferring runtime authority. */
function frameIdentity(visible:Frame,sourceIdentity:JsonRecord|undefined,t:Translator){
  return <p className={styles.small} data-testid="frame-identity">{t("frameSequence")}: {String(sourceIdentity?.sequence)} · {String(sourceIdentity?.frame_id).slice(0,12)} · {t("frameRelativeAge")}: {Math.round(frameAge(visible)??0)} ms · r{String(visible.analysis?.config_revision)}</p>;
}
/** Show accepted frame provenance and its model interpretation details. */
export function CameraDetails({client,t,language}:{client:RiderClient;t:Translator;language:string}) {
  const visible=client.camera, selected=client.cameraStream;
  const sourceIdentity=visible?.analysis&&Object.keys(record(visible.analysis.source)).length?record(visible.analysis.source):visible?.analysis;
  return <details className={styles.workspaceDisclosure} data-testid="camera-settings"><summary>{t("cameraTools")}</summary><div className={styles.disclosureBody}>
    <CameraPipelineControls client={client} t={t}/>
    {selected==="venom"&&<p className={styles.hint}>{t("visionLegend")}</p>}
    {visible?.visionState&&<p className={styles.hint}>{t("state")}: {t("vision."+visible.visionState)} · {visible.inferenceMs} ms · <code>{visible.visionReason}</code></p>}
    {visible?.analysis&&frameIdentity(visible,sourceIdentity,t)}
    {visible&&<p className={styles.small}>{t(visible.stale?"framePaused":visible.knownFreshness?"frameFresh":"frameTimeUnknown")} · {formatTime(Number(visible.fileAt),language)}</p>}
  </div></details>;
}
