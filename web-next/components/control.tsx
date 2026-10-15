import { CorridorOverlay, type CorridorEditorProps } from "./corridor-overlay";
import { frameAge } from "../lib/vision-frame";
import { previewNotice } from "../lib/loop-controls";
/* eslint-disable @next/next/no-img-element -- Pi binary frames use revocable object URLs. */
import { useState } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Camera, CirclePause, CircleStop } from "lucide-react";
import { actuatorState, displayValue, finite, formatTime, record, safetyOf, settingsOf, type Direction, type NumericDescriptor } from "../lib/model";
import type { Translator } from "../lib/i18n";
import type { RiderClient } from "../lib/use-rider";
import { Button, Empty, Field, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";

const arrows = [
  { direction: "forward" as const, row: 1, column: 2, Icon: ArrowUp },
  { direction: "left" as const, row: 2, column: 1, Icon: ArrowLeft },
  { direction: "right" as const, row: 2, column: 3, Icon: ArrowRight },
  { direction: "backward" as const, row: 3, column: 2, Icon: ArrowDown },
];
export function ManualPad({ client, t, language, compact = false }: { client: RiderClient; t: Translator; language: string; compact?: boolean }) {
  const safety = safetyOf(client.status);
  const settings = settingsOf(client.status);
  const directions = record(settings.directions);
  const schema = record(record(settings.parameter_schema).direction_fields);
  const speedDescriptor = record(schema.speed) as NumericDescriptor;
  const pointerDown = (direction: Direction, event: React.PointerEvent<HTMLButtonElement>) => {
    if (!client.canNavigate || event.button !== 0) return;
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); client.hold.start(direction, event.pointerId);
  };
  return <div>
    {!compact && <p className={styles.hint}>{t("manualHint")}</p>}
    <div className={styles.pad}>
      {arrows.map(({ direction, row, column, Icon }) => <button type="button" key={direction} aria-label={t(direction)} title={t(direction)} disabled={!client.canNavigate} data-testid={`rider-${direction}`} className={`${styles.arrow} ${client.activeDirection === direction ? styles.arrowActive : ""}`} style={{ gridRow: row, gridColumn: column }} onPointerDown={event => pointerDown(direction, event)} onPointerUp={event => client.hold.end(event.pointerId)} onPointerCancel={event => client.hold.release(event.pointerId)} onLostPointerCapture={event => client.hold.release(event.pointerId)} onClick={event => { if (event.detail === 0 && client.canNavigate && !client.activeDirection) { client.hold.start(direction, -2); client.hold.end(-2); } }} onKeyDown={event => { if (["Enter", " "].includes(event.key) && !event.repeat) { event.preventDefault(); client.hold.start(direction, -1); } }} onKeyUp={event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); client.hold.end(-1); } }} onBlur={() => client.hold.release()}>
        <Icon size={compact?20:25} />{!compact&&<span>{t(direction)}</span>}<span className={styles.small}>{displayValue(record(directions[direction]).speed, speedDescriptor, language)} {speedDescriptor.display_unit ?? ""}</span>
      </button>)}
      <button type="button" className={`${styles.padCenter} ${styles.padStop}`} style={{ gridRow: 2, gridColumn: 2 }} aria-label={t("stopMotors")} title={t("manualStopHint")} data-testid="rider-pad-stop" disabled={!client.canNavigate} onClick={() => void client.stopManual()}><CircleStop size={27} /><span>{t("stopShort")}</span></button>
    </div>
    <div className={styles.padStatus}><Pill tone={safety.state === "ARMED" ? "good" : "warning"}>{t(safety.state === "ARMED" ? "armed" : safety.state === "ESTOP" ? "estop" : safety.state === "DISARMED" ? "disarmed" : "unknown")}</Pill>{(client.status?.errors.control || safety.stale === true) && <Pill tone="warning">{t("lastPermission")}</Pill>}{!!record(safety.execution).inhibit && <Pill tone="warning">{t("executionPaused")}</Pill>}{client.activeDirection && <Pill tone="good">{t("held")} · {t(client.activeDirection)}</Pill>}</div>
    <div className={`${styles.flex} ${styles.space}`} style={{ marginTop: 15 }}><span className={styles.small}>{t("owner")}: {typeof safety.owner === "string" ? safety.owner : "—"}</span><Button onClick={() => client.hold.release()} disabled={!client.activeDirection}><CirclePause size={15} />{t("endDirection")}</Button></div>
  </div>;
}
export function Posture({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
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
  return <Panel title={t("posture")}>
    {heightAvailable && height !== undefined ? <><Field label={`${t("height")} · ${descriptor.display_unit ?? descriptor.unit ?? ""}`}><div className={`${styles.flex} ${styles.space}`}><input className={styles.range} type="range" min={descriptor.minimum} max={descriptor.maximum} step={descriptor.step} value={height} onChange={event => setDraftHeight(Number(event.target.value))} disabled={!client.canMutate} /><strong>{displayValue(height, descriptor, language)}</strong></div></Field><p className={styles.hint}>{t("heightHint")}</p><div className={`${styles.flex} ${styles.space}`}><span className={styles.small}>{t("heightTarget")}: {displayValue(target.value, descriptor, language)} {descriptor.display_unit} · {formatTime(target.ts, language)}</span><Button disabled={!client.canMove || client.busy === "height" || !finite(height)} onClick={() => void client.command({ action: "height", height })}>{t("apply")}</Button></div></> : <Empty>{t("unavailable")}</Empty>}
    <div className={styles.rule} />
    <div className={`${styles.flex} ${styles.space}`}><div><strong style={{ fontSize: 13 }}>{t("balance")}</strong><p className={styles.hint} style={{ marginBottom: 0 }}>{t("balanceHint")}</p></div><Button aria-pressed={balance.enabled === true} disabled={!client.canMove || (balanceDescriptor.type !== "boolean" || record(capabilities.balance).available === false) || client.busy === "balance"} onClick={() => void client.command({ action: "balance", enabled: balance.enabled !== true })}>{t(balance.enabled === true ? "disable" : "enable")}</Button></div>
    <div className={styles.meta}>{t("state")}: {t(balance.enabled === true ? "on" : balance.enabled === false ? "off" : "unknown")}</div>
  </Panel>;
}
export function CameraPanel({ client, t, language, compact = false, onObservation, observationDisabled = false, analysisCurrent, analysisRunning, editor }: { client: RiderClient; t: Translator; language: string; compact?: boolean; onObservation?:()=>void; observationDisabled?:boolean; analysisCurrent?:boolean; analysisRunning?:boolean; editor?:CorridorEditorProps }) {
  const [imageSize,setImageSize]=useState<[number,number]>([320,240]);
  const selected = client.cameraStream ?? "raw";
  const transitioning=selected==="venom"&&client.cameraTransition;
  const notice=previewNotice(client.camera?.stale??false,selected,analysisCurrent,analysisRunning,transitioning);
  const streams = compact ? (["raw", "venom"] as const) : (["raw", "venom", "proc"] as const);
  return <Panel className={compact?styles.cameraEmbedded:""} title={t("camera")} hint={compact?undefined:t("cameraHint")} extra={<Button onClick={() => client.setCameraStream(client.cameraStream ? null : "raw")} disabled={!client.canMutate}><Camera size={15} />{t(client.cameraStream ? "hidePreview" : "showPreview")}</Button>}>
    <div className={styles.flex} style={{ marginBottom: 14 }}>{streams.map(stream => <Button key={stream} tone={selected === stream ? "primary" : undefined} aria-pressed={selected === stream} onClick={() => client.setCameraStream(stream)} disabled={!client.canMutate}>{t(stream === "venom" ? "visionVenom" : stream === "proc" ? "visionPi" : stream)}</Button>)}</div>
    <div className={styles.camera}>{client.camera&&notice&&<div className={transitioning?styles.cameraPaused+" "+styles.cameraTransition:styles.cameraPaused} role="status" data-testid="vision-preview-paused">{t(notice!)}</div>}{client.camera ? <><img src={client.camera.url} alt={t("cameraAlt")} onLoad={event=>{setImageSize([event.currentTarget.naturalWidth,event.currentTarget.naturalHeight]);client.markCameraDecoded(event.currentTarget.currentSrc,true);}} onError={event=>client.markCameraDecoded(event.currentTarget.currentSrc,false)} />{editor&&<CorridorOverlay editor={editor} size={imageSize} t={t}/>}</> : <Empty><Camera size={32} />{t(transitioning?"visionPreviewRestarting":client.mediaError ?? (client.cameraStream ? "frameMissing" : "showPreview"))}{onObservation&&<><p className={styles.hint}>{t("loopPreviewHelp")}</p><Button tone="primary" disabled={observationDisabled} onClick={onObservation}>{t("loopPreparePreview")}</Button></>}</Empty>}</div>
    {selected === "venom" && <p className={styles.hint}>{t("visionLegend")}</p>}
    {client.camera?.visionState && <p className={styles.hint}>{t(client.camera.stale?"loopDecision":"state")}: {t("vision." + client.camera.visionState)} · {client.camera.inferenceMs} ms · <code>{client.camera.visionReason}</code>{client.camera.visionPolicy === "diagnostic_default" && <> · {t("visionDefaultZone")}</>}</p>}
    {client.camera?.analysis && <p className={styles.small} data-testid="frame-identity">{t("frameSequence")}: {String(client.camera.analysis.sequence)} · {String(client.camera.analysis.frame_id).slice(0,12)} · {t("frameRelativeAge")}: {Math.round(frameAge(client.camera)??0)} ms</p>}
    {client.camera && <div className={styles.meta}><Pill>{t(client.camera.stale?"framePaused":Number(client.camera.recordedAt)>0 ? "frameReplay" : client.camera.knownFreshness ? "frameFresh" : "frameTimeUnknown")}</Pill>{Number(client.camera.recordedAt)>0&&<span>{t("frameRecorded")}: {formatTime(Number(client.camera.recordedAt),language)}</span>}{client.camera.fileAt && <span>{t("frameAt")}: {formatTime(Number(client.camera.fileAt), language)}</span>}{client.camera.source && <span>{t("source")}: {client.camera.source}</span>}</div>}
  </Panel>;
}
