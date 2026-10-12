/* eslint-disable @next/next/no-img-element -- Pi face previews use revocable object URLs. */
import { useCallback, useEffect, useRef, useState } from "react";
import { getApiBaseUrl } from "@/lib/env";
import { Play, Plus, Smile, Square } from "lucide-react";
import { gestureBody, gestureFrom, validFaceSettings, validGesture } from "../lib/face";
import { data, errorCode, faceRunning, finite, formatNumber, record, rows, str, type Gesture, type JsonRecord, type Segment } from "../lib/model";
import type { Translator } from "../lib/i18n";
import type { RiderClient } from "../lib/use-rider";
import { Button, Details, Empty, Field, NumberField, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";

function Preview({ gesture, library, client, t, language }: { gesture: Gesture; library: JsonRecord; client: RiderClient; t: Translator; language: string }) {
  const descriptor = record(library.descriptors), settings = record(library.settings);
  const [style, setStyle] = useState<string | null>(null), [skin, setSkin] = useState<string | null>(null);
  const [intensity, setIntensity] = useState(1), [tempo, setTempo] = useState(1), [transition, setTransition] = useState("original");
  const [speech, setSpeech] = useState(false), [at, setAt] = useState(0), [playing, setPlaying] = useState(false), [rendering, setRendering] = useState(false), [image, setImage] = useState<string | null>(null);
  const { canMutate, setNotice } = client;
  const session = client.status?.connection.session ?? "";
  const imageRef = useRef<string | null>(null), abortRef = useRef<AbortController | null>(null), activeRef = useRef(true), renderingRef = useRef(false);
  const selectedStyle = style ?? str(settings.style), selectedSkin = skin ?? str(settings.skin);
  const duration = gesture.duration / tempo;
  const render = useCallback(async (position: number, signal?: AbortSignal) => {
    if (renderingRef.current || !canMutate || !validGesture(gesture, library)) return;
    renderingRef.current = true; setRendering(true);
    const abort = new AbortController(); abortRef.current = abort;
    const signalAbort = () => abort.abort(); signal?.addEventListener("abort", signalAbort, { once: true });
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/rider/face/frame`, { method: "POST", cache: "no-store", signal: abort.signal, headers: { "Content-Type": "application/json", "X-Rider-Session": session }, body: JSON.stringify({ ...gestureBody(gesture, true), style: selectedStyle, skin: selectedSkin, intensity, tempo, transition, simulate_speech: speech, at: Math.min(Math.max(0, position), duration) }) });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw Object.assign(new Error("preview_failed"), { data: body }); }
      if (!response.headers.get("Content-Type")?.startsWith("image/png")) throw new Error("invalid_frame");
      const blob = await response.blob(); if (abort.signal.aborted || !activeRef.current) return;
      const next = URL.createObjectURL(blob); if (imageRef.current) URL.revokeObjectURL(imageRef.current); imageRef.current = next; setImage(next); setAt(position);
    } catch (error) { if (!abort.signal.aborted && activeRef.current) { setPlaying(false); setNotice({ key: errorCode(error) === "preview_busy" ? "previewBusy" : "requestFailed", code: errorCode(error), tone: "error" }); } }
    finally { signal?.removeEventListener("abort", signalAbort); renderingRef.current = false; if (activeRef.current) setRendering(false); }
  }, [canMutate, setNotice, session, duration, gesture, intensity, library, selectedSkin, selectedStyle, speech, tempo, transition]);
  const renderRef = useRef(render);
  useEffect(() => { renderRef.current = render; }, [render]);
  useEffect(() => {
    activeRef.current = true;
    const hidden = () => { if (document.visibilityState === "hidden") { setPlaying(false); abortRef.current?.abort(); } };
    document.addEventListener("visibilitychange", hidden);
    return () => { activeRef.current = false; document.removeEventListener("visibilitychange", hidden); abortRef.current?.abort(); if (imageRef.current) URL.revokeObjectURL(imageRef.current); };
  }, []);
  useEffect(() => {
    if (!playing) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout> | null = null;
    const started = performance.now(), startAt = at;
    const fps = Number(record(descriptor.fps).preview);
    if (!finite(fps) || fps <= 0) return;
    const tick = async () => {
      if (abort.signal.aborted || document.visibilityState === "hidden") return;
      const position = Math.min(duration, startAt + (performance.now() - started) / 1000);
      await renderRef.current(position, abort.signal);
      if (abort.signal.aborted) return;
      if (position >= duration) setPlaying(false);
      else timer = setTimeout(() => { void tick(); }, 1000 / fps);
    };
    void tick();
    return () => { abort.abort(); if (timer) clearTimeout(timer); };
    // Timeline state is captured at the explicit Play action, not each frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, duration, descriptor.fps]);
  const valid = validGesture(gesture, library) && validFaceSettings({ ...settings, style: selectedStyle, skin: selectedSkin, intensity, tempo, transition }, descriptor, library);
  return <Panel title={t("preview")} hint={t("previewHint")}>
    <div className={`${styles.camera} ${styles.facePreview}`}>{image ? <img src={image} alt={t("previewAlt")} /> : <Empty><Smile size={32} />{t("renderPreview")}</Empty>}</div>
    <div className={styles.fieldRow} style={{ marginTop: 18 }}><Field label={t("style")}><select className={styles.select} value={selectedStyle} onChange={event => setStyle(event.target.value)}>{rows(descriptor.styles).map(item => <option key={str(item.id)} value={str(item.id)}>{t(str(item.id))}</option>)}</select></Field><Field label={t("skin")}><select className={styles.select} value={selectedSkin} onChange={event => setSkin(event.target.value)}>{rows(descriptor.skins).map(item => <option key={str(item.id)} value={str(item.id)}>{t(`skin.${str(item.id)}`)}</option>)}</select></Field></div>
    <div className={styles.fieldRow} style={{ marginTop: 14 }}><NumberField label={t("intensity")} value={intensity} descriptor={{ minimum: Number(record(descriptor.intensity).min) / 100, maximum: Number(record(descriptor.intensity).max) / 100, step: Number(record(descriptor.intensity).step) / 100, display_factor: 100, display_unit: "%", display_precision: 0 }} onChange={setIntensity} /><NumberField label={t("tempo")} value={tempo} descriptor={{ minimum: Number(record(descriptor.tempo).min) / 100, maximum: Number(record(descriptor.tempo).max) / 100, step: Number(record(descriptor.tempo).step) / 100, display_factor: 100, display_unit: "%", display_precision: 0 }} onChange={setTempo} /></div>
    <Field label={t("transition")}><select className={styles.select} style={{ marginTop: 7 }} value={transition} onChange={event => setTransition(event.target.value)}>{Array.isArray(descriptor.transitions) && descriptor.transitions.map(item => <option key={String(item)} value={String(item)}>{t(String(item))}</option>)}</select></Field>
    <label className={styles.check} style={{ marginTop: 14 }}><input type="checkbox" checked={speech} onChange={event => setSpeech(event.target.checked)} />{t("speechPreview")}</label>
    <Field label={`${t("previewAt")} · ${formatNumber(at, language, 2)} s`}><input className={styles.range} type="range" min="0" max={Number.isFinite(duration) ? duration : 0} step="0.01" value={Math.min(at, Number.isFinite(duration) ? duration : 0)} onChange={event => { setPlaying(false); setAt(Number(event.target.value)); }} /></Field>
    <div className={styles.flex} style={{ marginTop: 14 }}><Button disabled={!client.canMutate || !valid || rendering || playing} onClick={() => void render(at)}>{t("renderPreview")}</Button><Button tone="primary" disabled={!client.canMutate || !valid || (!playing && rendering)} onClick={() => { if (playing) { setPlaying(false); abortRef.current?.abort(); } else { setAt(0); setPlaying(true); } }}>{playing ? <Square size={14} /> : <Play size={14} />}{t(playing ? "stopPreview" : "playPreview")}</Button></div>
  </Panel>;
}
function Runtime({ client, library, selected, unsaved, t }: { client: RiderClient; library: JsonRecord; selected: Gesture; unsaved: boolean; t: Translator }) {
  const runtime = data(client.status?.face.runtime), settings = record(runtime.settings), descriptors = record(runtime.descriptors ?? library.descriptors);
  const [draft, setDraft] = useState<JsonRecord | null>(null), [intent, setIntent] = useState("mood"), [expression, setExpression] = useState("neutral"), [level, setLevel] = useState(0.5);
  const effective = draft ?? settings;
  const conflict = draft !== null && runtime.settings_revision !== draft.if_revision && draft.if_revision !== undefined;
  const active = Array.isArray(effective.active_gestures) ? effective.active_gestures as string[] : [];
  const patch = (key: string, value: unknown) => setDraft({ ...effective, if_revision: draft?.if_revision ?? runtime.settings_revision, [key]: value });
  const save = async () => { const result = await client.mutate("/face/runtime", { ...effective, if_revision: draft?.if_revision ?? runtime.settings_revision }); if (result) setDraft(null); };
  const validSettings = validFaceSettings(effective, descriptors, library);
  const playing = faceRunning(client.status);
  const eventBody = intent === "gesture" ? { intent, id: selected.id } : intent === "audio" ? { intent, level, playing: true } : { intent, expr: expression };
  return <Panel title={t("runtime")} hint={t("runtimeHint")} extra={<Pill tone={playing ? "good" : undefined}>{t(playing ? "active" : "inactive")}</Pill>}>
    {conflict && <p className={styles.hint}>{t("conflict")}</p>}
    <div className={styles.fieldRow}><Field label={t("style")}><select className={styles.select} value={str(effective.style)} onChange={event => patch("style", event.target.value)} disabled={!client.canMutate}>{rows(descriptors.styles).map(item => <option key={str(item.id)} value={str(item.id)}>{t(str(item.id))}</option>)}</select></Field><Field label={t("skin")}><select className={styles.select} value={str(effective.skin)} onChange={event => patch("skin", event.target.value)} disabled={!client.canMutate}>{rows(descriptors.skins).map(item => <option key={str(item.id)} value={str(item.id)}>{t(`skin.${str(item.id)}`)}</option>)}</select></Field></div>
    <div className={styles.fieldRow} style={{ marginTop: 14 }}><Field label={t("power_mode")}><select className={styles.select} value={str(effective.power_mode)} onChange={event => patch("power_mode", event.target.value)} disabled={!client.canMutate}>{["battery", "cable"].map(value => <option key={value} value={value}>{t(value === "battery" ? "batteryPower" : "cable")}</option>)}</select></Field><label className={styles.check}><input type="checkbox" checked={effective.idle === true} onChange={event => patch("idle", event.target.checked)} disabled={!client.canMutate} />{t("idle")}</label></div>
    <div className={styles.fieldRow} style={{ marginTop: 14 }}><NumberField label={t("intensity")} value={Number(effective.intensity)} descriptor={{ minimum: Number(record(descriptors.intensity).min) / 100, maximum: Number(record(descriptors.intensity).max) / 100, step: Number(record(descriptors.intensity).step) / 100, display_factor: 100, display_unit: "%", display_precision: 0 }} onChange={value => patch("intensity", value)} disabled={!client.canMutate} /><NumberField label={t("tempo")} value={Number(effective.tempo)} descriptor={{ minimum: Number(record(descriptors.tempo).min) / 100, maximum: Number(record(descriptors.tempo).max) / 100, step: Number(record(descriptors.tempo).step) / 100, display_factor: 100, display_unit: "%", display_precision: 0 }} onChange={value => patch("tempo", value)} disabled={!client.canMutate} /></div>
    <Field label={t("transition")}><select className={styles.select} value={str(effective.transition)} onChange={event => patch("transition", event.target.value)} disabled={!client.canMutate}>{Array.isArray(descriptors.transitions) && descriptors.transitions.map(value => <option key={String(value)} value={String(value)}>{t(String(value))}</option>)}</select></Field>
    <details className={styles.details}><summary>{t("activeGestures")}</summary><div className={styles.grid3}>{rows(library.gestures).map(item => <label key={str(item.id)} className={styles.check}><input type="checkbox" checked={active.includes(str(item.id))} onChange={event => patch("active_gestures", event.target.checked ? [...active, str(item.id)] : active.filter(key => key !== item.id))} disabled={!client.canMutate} />{str(item.name)}</label>)}</div></details>
    <div className={styles.flex} style={{ marginTop: 15 }}><Button disabled={!client.canMutate || !draft || !!client.busy || conflict || !validSettings} onClick={() => void save()}>{t("saveRuntime")}</Button>{draft && <Button onClick={() => setDraft(null)}>{t("resetDraft")}</Button>}</div>
    <div className={styles.rule} />
    <p className={styles.hint}>{t("runtimeSavedGesture")}: {selected.name}</p>
    <div className={styles.flex}><Button tone="primary" disabled={!client.canMutate || !!client.busy || !selected.id || !active.includes(selected.id) || !!draft || unsaved} onClick={() => void client.mutate("/face/play", { gesture_id: selected.id, expr: selected.expr, sink: "lcd" })}><Play size={15} />{t("playLcd")}</Button><Button tone="danger" disabled={!client.canMutate || !!client.busy} onClick={() => void client.mutate("/face/stop")}><Square size={14} />{t("stopFace")}</Button></div>
    <div className={styles.fieldRow} style={{ marginTop: 18 }}><Field label={t("event")}><select className={styles.select} value={intent} onChange={event => setIntent(event.target.value)}>{["mood", "listening", "waiting", "gesture", "audio"].map(value => <option key={value} value={value}>{t(value)}</option>)}</select></Field>{!["gesture", "audio"].includes(intent) && <Field label={t("expression")}><select className={styles.select} value={expression} onChange={event => setExpression(event.target.value)}>{Array.isArray(descriptors.expressions) && descriptors.expressions.map(value => <option key={String(value)} value={String(value)}>{t(String(value))}</option>)}</select></Field>}{intent === "audio" && <NumberField label={t("audio")} value={level} descriptor={{ minimum: 0, maximum: 1, display_factor: 100, display_unit: "%", display_precision: 0 }} onChange={setLevel} />}<Button disabled={!client.canMutate || !playing || !!client.busy || (intent === "gesture" && !selected.id)} onClick={() => void client.mutate("/face/event", eventBody)}>{t("sendEvent")}</Button></div>
    <Button style={{ marginTop: 16 }} onClick={() => client.setShowRuntimeFrame(!client.showRuntimeFrame)} disabled={!client.canMutate}>{t("runtimeFrame")} · {t(client.showRuntimeFrame ? "hidePreview" : "showPreview")}</Button>
    {client.showRuntimeFrame && <div className={`${styles.camera} ${styles.facePreview}`} style={{ marginTop: 14 }}>{client.runtimeFrame ? <img src={client.runtimeFrame.url} alt={t("runtimeFrame")} /> : <Empty>{t("frameMissing")}</Empty>}</div>}
    <Details value={runtime} t={t} />
  </Panel>;
}
export function FaceStudio({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
  const library = data(client.status?.face.library), descriptor = record(library.descriptors), gestures = rows(library.gestures);
  const [selectedId, setSelectedId] = useState<string | null>(null), [draft, setDraft] = useState<Gesture | null>(null), [confirmDelete, setConfirmDelete] = useState(false);
  const selected = gestureFrom(gestures.find(item => item.id === (selectedId ?? gestures[0]?.id)));
  const effective = draft ?? selected;
  const fields = record(library.fields ?? descriptor.fields), valid = validGesture(effective, library);
  const edit = (patch: Partial<Gesture>) => setDraft({ ...effective, ...patch });
  const updateSegment = (field: string, index: number, patch: Partial<Segment>) => edit({ tracks: { ...effective.tracks, [field]: (effective.tracks[field] ?? []).map((segment, position) => position === index ? { ...segment, ...patch } : segment) } });
  const save = async () => { const result = await client.mutate("/face/library", gestureBody(effective)); if (result) { setDraft(null); if (typeof result.id === "string") setSelectedId(result.id); } };
  const remove = async () => { const result = await client.mutate(`/face/library/${encodeURIComponent(selected.id ?? "")}`, {}, "DELETE"); if (result) { setSelectedId(null); setDraft(null); setConfirmDelete(false); } };
  if (!library.descriptors) return <Panel title={t("face")}><Empty><Smile size={30} />{t("unavailable")}</Empty></Panel>;
  return <div className={styles.stack}><p className={styles.hint}>{t("faceHint")}</p><div className={styles.faceGrid}><Panel title={t("library")}><Button disabled={!client.canMutate} onClick={() => { setDraft(gestureFrom({ name: t("newGesture"), expr: "neutral", duration: 1, initial: record(descriptor.expression_defaults).neutral ?? {}, tracks: {} })); setConfirmDelete(false); }}><Plus size={15} />{t("newGesture")}</Button><div className={styles.gestureList} style={{ marginTop: 14 }}>{gestures.map(item => <button type="button" key={str(item.id)} className={`${styles.gestureItem} ${selected.id === item.id && !draft ? styles.gestureSelected : ""}`} onClick={() => { setSelectedId(str(item.id)); setDraft(null); setConfirmDelete(false); }}><strong>{item.builtin === true ? t(str(item.name)) : str(item.name)}</strong><span className={styles.small}>{t(item.builtin === true ? "builtin" : "gesture")} · {formatNumber(item.duration, language)} s</span></button>)}</div></Panel>
    <div className={styles.stack}><Panel title={effective.builtin ? t("editCopy") : t("gesture")} extra={draft ? <Pill tone={valid ? "warning" : "danger"}>{t(valid ? "unsaved" : "invalidDraft")}</Pill> : undefined}>
      <div className={styles.grid3}><Field label={t("gestureName")}><input className={styles.input} value={effective.name} maxLength={60} disabled={!client.canMutate} onChange={event => edit({ name: event.target.value })} /></Field><Field label={t("expression")}><select className={styles.select} value={effective.expr} disabled={!client.canMutate} onChange={event => edit({ expr: event.target.value })}>{Array.isArray(descriptor.expressions) && descriptor.expressions.map(value => <option key={String(value)} value={String(value)}>{t(String(value))}</option>)}</select></Field><NumberField label={t("animationDuration")} value={effective.duration} descriptor={{ minimum: Number(record(descriptor.duration).min) / 1000, maximum: Number(record(descriptor.duration).max) / 1000, step: Number(record(descriptor.duration).step) / 1000, display_factor: 1000, display_precision: 0, display_unit: str(record(descriptor.duration).unit) }} onChange={value => edit({ duration: value })} disabled={!client.canMutate} /></div>
      <div className={styles.rule} /><h3 className={styles.panelTitle}>{t("initial")}</h3><div className={styles.grid3} style={{ marginTop: 14 }}>{Object.entries(fields).map(([field, bounds]) => <NumberField key={field} label={t(field)} value={effective.initial[field] ?? 0} descriptor={{ minimum: Array.isArray(bounds) ? Number(bounds[0]) : undefined, maximum: Array.isArray(bounds) ? Number(bounds[1]) : undefined, step: 0.05, display_precision: 2 }} onChange={value => edit({ initial: { ...effective.initial, [field]: value } })} disabled={!client.canMutate} />)}</div>
      <details className={styles.details}><summary>{t("tracks")}</summary>{Object.entries(fields).map(([field, bounds]) => { const segments = effective.tracks[field] ?? []; const last = segments.at(-1)?.t1 ?? 0; return <div key={field}><div className={`${styles.flex} ${styles.space}`}><strong>{t(field)}</strong><Button disabled={!client.canMutate || segments.length >= 32 || last >= effective.duration} onClick={() => edit({ tracks: { ...effective.tracks, [field]: [...segments, { t0: last, t1: effective.duration, v0: effective.initial[field] ?? 0, v1: effective.initial[field] ?? 0, ease: "lin" }] } })}>{t("addTrack")}</Button></div>{segments.map((segment, index) => <div key={index} className={styles.track}><NumberField label={`${t("from")} · ms`} value={segment.t0} descriptor={{ minimum: 0, maximum: effective.duration, display_factor: 1000, display_precision: 0 }} disabled={!client.canMutate} onChange={value => updateSegment(field, index, { t0: value })} /><NumberField label={`${t("to")} · ms`} value={segment.t1} descriptor={{ minimum: 0, maximum: effective.duration, display_factor: 1000, display_precision: 0 }} disabled={!client.canMutate} onChange={value => updateSegment(field, index, { t1: value })} /><NumberField label={t("from")} value={segment.v0} descriptor={{ minimum: Array.isArray(bounds) ? Number(bounds[0]) : undefined, maximum: Array.isArray(bounds) ? Number(bounds[1]) : undefined, step: 0.05 }} disabled={!client.canMutate} onChange={value => updateSegment(field, index, { v0: value })} /><NumberField label={t("to")} value={segment.v1} descriptor={{ minimum: Array.isArray(bounds) ? Number(bounds[0]) : undefined, maximum: Array.isArray(bounds) ? Number(bounds[1]) : undefined, step: 0.05 }} disabled={!client.canMutate} onChange={value => updateSegment(field, index, { v1: value })} /><Field label={t("ease")}><select className={styles.select} value={segment.ease} disabled={!client.canMutate} onChange={event => updateSegment(field, index, { ease: event.target.value })}>{["lin", "in", "out", "in_out"].map(value => <option key={value} value={value}>{t(value)}</option>)}</select></Field><Button disabled={!client.canMutate} aria-label={t("removeTrack")} onClick={() => edit({ tracks: { ...effective.tracks, [field]: segments.filter((_, position) => position !== index) } })}>×</Button></div>)}</div>; })}</details>
      <div className={styles.flex} style={{ marginTop: 18 }}><Button tone="primary" disabled={!client.canMutate || !valid || !!client.busy} onClick={() => void save()}>{t(effective.builtin ? "editCopy" : "save")}</Button>{draft && <Button onClick={() => setDraft(null)}>{t("resetDraft")}</Button>}<Button tone="danger" disabled={!client.canMutate || selected.builtin || !selected.id || !!client.busy} onClick={() => setConfirmDelete(true)}>{t("delete")}</Button></div>
      {confirmDelete && <div className={`${styles.banner} ${styles.demo}`} style={{ marginTop: 14 }}><span className={styles.noticeText}>{t("deleteConfirm")}: {selected.name}</span><Button tone="danger" onClick={() => void remove()}>{t("delete")}</Button><Button onClick={() => setConfirmDelete(false)}>{t("cancel")}</Button></div>}
    </Panel><div className={styles.grid}><Preview gesture={effective} library={library} client={client} t={t} language={language} /><Runtime client={client} library={library} selected={selected} unsaved={!!draft} t={t} /></div></div>
  </div></div>;
}
