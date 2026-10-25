/* eslint-disable @next/next/no-img-element -- Stored immutable RAW sources are served by the module. */
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { getApiBaseUrl } from "@/lib/env";
import { errorCode, formatTime, record, str, type JsonRecord } from "../lib/model";
import { SAMPLES_BASE, videoErrorKey, type Collection, type Sample, type SamplePage } from "../lib/samples";
import type { Translator } from "../lib/i18n";
import { EvidenceSummary } from "./evidence-summary";
import { MotionEvidencePanel } from "./motion-evidence-panel";
import { AnalysisPanel } from "./analysis-panel";
import { Button, Details, Empty, Field, Input, Panel, Select, Textarea } from "./primitives";
import styles from "../rider.module.css";

const emptyLabel={class_name:"",scene:"",expected_state:"unknown",obstacle_side:"unknown",review:"pending",note:""};
export function SamplesBrowser({t,language,refreshKey}:{t:Translator;language:string;refreshKey?:string}) {
  const [open,setOpen]=useState(false);
  return <details className={styles.details} onToggle={event=>setOpen(event.currentTarget.open)} data-testid="samples-browser">
    <summary>{t("samples.library")}</summary>{open&&<SampleLibrary t={t} language={language} refreshKey={refreshKey}/>}
  </details>;
}
function SampleLibrary({t,language,refreshKey}:{t:Translator;language:string;refreshKey?:string}) {
  const [sessions,setSessions]=useState<Collection[]>([]);
  const [selected,setSelected]=useState("");
  const [offset,setOffset]=useState(0);
  const [page,setPage]=useState<SamplePage|null>(null);
  const [sample,setSample]=useState<JsonRecord|null>(null);
  const [error,setError]=useState("");
  const [revision,setRevision]=useState(0);
  const [label,setLabel]=useState(emptyLabel);
  const [writing,setWriting]=useState(false);
  const [updated,setUpdated]=useState(0);
  const inspectEpoch=useRef(0);
  const refreshEpoch=useRef(0);
  const refresh=useCallback(async()=>{
    const current=++refreshEpoch.current;
    try{const result=await apiFetch<{sessions:Collection[]}>(SAMPLES_BASE+"/sessions");if(current===refreshEpoch.current){setSessions(result.sessions);setError("");}}
    catch(err){if(current===refreshEpoch.current)setError(errorCode(err));}
  },[]);
  useEffect(()=>{void refresh();},[refresh,refreshKey]);
  useEffect(()=>{
    if(!selected)return;
    let current=true;
    setPage(null);setSample(null);
    void apiFetch<SamplePage>(SAMPLES_BASE+"/sessions/"+selected+"?offset="+offset+"&limit=12").then(result=>{if(current){setPage(result);setError("");}}).catch(err=>{if(current)setError(errorCode(err));});
    return()=>{current=false;};
  },[selected,offset,updated,refreshKey]);
  const inspect=async(id:string)=>{
    const current=++inspectEpoch.current;
    try{
      const item=await apiFetch<JsonRecord>(SAMPLES_BASE+"/items/"+id);
      if(current!==inspectEpoch.current)return;
      setSample(item);setError("");
      const history=Array.isArray(item.labels)?item.labels as JsonRecord[]:[];
      const latest=record(history.at(-1));
      setRevision(Number(latest.revision??0));
      setLabel(Object.fromEntries(Object.entries(emptyLabel).map(([key,value])=>[key,str(latest[key])||value])) as typeof emptyLabel);
    }catch(err){setError(errorCode(err));}
  };
  const save=async()=>{
    if(!sample)return;
    setWriting(true);
    try{
      await apiFetch(SAMPLES_BASE+"/items/"+str(sample.sample_id)+"/labels",{method:"POST",body:JSON.stringify({expected_revision:revision,values:label})});
      await inspect(str(sample.sample_id));
    }catch(err){setError(errorCode(err));}
    finally{setWriting(false);}
  };
  const source=(id:string)=>getApiBaseUrl()+SAMPLES_BASE+"/items/"+id+"/original";
  const enumField=(key:"expected_state"|"obstacle_side"|"review",values:string[])=> <Field label={t("samples.label."+key)}><Select aria-label={t("samples.label."+key)} value={label[key]} onChange={event=>setLabel(previous=>({...previous,[key]:event.target.value}))}>{values.map(value=><option key={value} value={value}>{t("samples.value."+value)}</option>)}</Select></Field>;
  const latest=record((Array.isArray(sample?.labels)?sample.labels:[]).at(-1));
  const qualityText=(quality:JsonRecord)=>Array.isArray(quality.flags)&&quality.flags.length?quality.flags.map(flag=>t("samples.quality."+String(flag))).join(", "):t("samples.quality.unflagged");
  return <Panel title={t("samples.library")} hint={t("samples.libraryHint")} extra={<Button data-testid="samples-refresh" onClick={()=>{void refresh();setUpdated(value=>value+1);}}>{t("refresh")}</Button>}>
    {error&&<p role="alert">{t("samples.failure")} · <code>{error}</code></p>}
    <Field label={t("samples.session")}><Select data-testid="samples-session" value={selected} onChange={event=>{inspectEpoch.current++;setSelected(event.target.value);setOffset(0);setPage(null);setSample(null);}}><option value="">{t("samples.choose")}</option>{sessions.map(row=><option key={row.session_id} value={row.session_id}>{formatTime(row.started_at,language)} · {row.source_device_id} · {t("samples.session."+row.state)} · {row.counts.saved??0} · {row.session_id.slice(0,8)}</option>)}</Select></Field>
    {page&&<>
      <EvidenceSummary value={page.session.evidence} t={t}/>
      {page.session.video&&<section data-testid="archive-video-status" aria-label={t("study.video")}>
        <strong>{t("study.video")}: {t("study.video."+page.session.video.state)}</strong>
        <p>{t("study.videoStats",{frames:page.session.video.frames,seconds:page.session.video.duration_s.toFixed(1),mib:(page.session.video.bytes/1024/1024).toFixed(1),fps:page.session.video.target_fps,gaps:page.session.video.gaps})}</p>
        {page.session.video.error&&<p role="alert">{t(videoErrorKey(page.session.video.error))}</p>}
        {!!page.session.video.frames&&page.session.video.ended_at!==null&&<a data-testid="archive-video-download" className={styles.button} href={getApiBaseUrl()+SAMPLES_BASE+"/sessions/"+page.session.session_id+"/video"} download>{t("study.downloadVideo")}</a>}
      </section>}
      <p className={styles.meta}>{t("samples.savedCount")}: {page.total} · {t("samples.request.failed")}: {page.session.counts.failed??0}</p>
      <div className={styles.samplesGrid}>{page.samples.map((item:Sample)=><button type="button" key={item.sample_id} data-testid="sample-thumbnail" className={styles.sampleThumb} disabled={writing} aria-pressed={sample?.sample_id===item.sample_id} onClick={()=>void inspect(item.sample_id)}><img src={source(item.sample_id)} alt={t("samples.original")} loading="lazy"/><span>#{String(item.ordinal)} · {String(item.frame_id??"—").slice(0,12)}</span>{Array.isArray(item.quality.flags)&&item.quality.flags.length>0&&<span>{t("samples.quality")}: {qualityText(item.quality)}</span>}</button>)}</div>
      {page.total===0&&<Empty>{t("samples.empty")}</Empty>}
      <div className={styles.flex}><Button disabled={offset===0} onClick={()=>setOffset(value=>Math.max(0,value-12))}>{t("samples.previous")}</Button><span className={styles.small}>{page.total?offset+1:0}–{Math.min(offset+12,page.total)} / {page.total}</span><Button disabled={offset+12>=page.total} onClick={()=>setOffset(value=>value+12)}>{t("samples.next")}</Button><a className={styles.button} data-testid="samples-export" href={getApiBaseUrl()+SAMPLES_BASE+"/sessions/"+page.session.session_id+"/export"} download>{t("samples.export")}</a></div>
    </>}
    {selected&&<MotionEvidencePanel key={"motion-"+selected} session={selected} t={t} language={language}/>}
    {selected&&<AnalysisPanel key={selected} session={selected} t={t} language={language}/>}
    {sample&&<div className={styles.sampleEditor} data-testid="sample-editor">
      <div><img className={styles.sampleOriginal} src={source(str(sample.sample_id))} alt={t("samples.original")}/><a className={styles.button} href={source(str(sample.sample_id))} download>{t("samples.original")}</a><p className={styles.small}>{t("samples.quality")}: {qualityText(record(sample.quality))}</p><code className={styles.small}>{str(record(sample.raw).sha256)}</code></div>
      <div><p className={styles.hint}>{t("samples.labelHint")}</p>
        <p className={styles.meta}>{t("samples.labelAuthor")}: {t("samples.author."+(str(record(latest.author).kind)||"unknown"))} · {t("samples.revision")}: {revision}</p>
        <Field label={t("samples.label.class")}><Input value={label.class_name} maxLength={128} onChange={event=>setLabel(previous=>({...previous,class_name:event.target.value}))}/></Field>
        <Field label={t("samples.label.scene")}><Input value={label.scene} maxLength={128} onChange={event=>setLabel(previous=>({...previous,scene:event.target.value}))}/></Field>
        <div className={styles.grid}>{enumField("expected_state",["unknown","clear","obstacle"])}{enumField("obstacle_side",["unknown","none","left","right","centre","both"])}{enumField("review",["pending","approved","rejected"])}</div>
        <Field label={t("samples.label.note")}><Textarea value={label.note} maxLength={1024} onChange={event=>setLabel(previous=>({...previous,note:event.target.value}))}/></Field>
        <Button data-testid="sample-label-save" tone="primary" disabled={writing} onClick={()=>void save()}>{t("samples.saveLabel")}</Button>
        <Details value={sample} t={t}/>
      </div>
    </div>}
  </Panel>;
}
