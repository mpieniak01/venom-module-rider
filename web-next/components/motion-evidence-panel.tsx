import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { errorCode, formatNumber, record, str, type JsonRecord } from "../lib/model";
import { SAMPLES_BASE } from "../lib/samples";
import type { Translator } from "../lib/i18n";
import { Button, Details } from "./primitives";
import styles from "../rider.module.css";

export function MotionEvidencePanel({session,t,language}:{session:string;t:Translator;language:string}) {
  const [report,setReport]=useState<JsonRecord|null>(null);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const epoch=useRef(0);
  const invalidate=useCallback(()=>++epoch.current,[]);
  useEffect(()=>{invalidate();setReport(null);setError("");setBusy(false);return()=>{invalidate();};},[session,invalidate]);
  const run=async()=>{
    const current=++epoch.current;setBusy(true);setError("");
    try{
      const value=await apiFetch<JsonRecord>(SAMPLES_BASE+"/sessions/"+session+"/motion-evidence");
      if(current===epoch.current)setReport(value);
    }catch(err){if(current===epoch.current)setError(errorCode(err));}
    finally{if(current===epoch.current)setBusy(false);}
  };
  const clocks=record(report?.clocks);
  const gaps=record(clocks.source_gaps_s);
  const samples=record(clocks.samples);
  const raw=record(record(report?.acceleration).raw_norm);
  const norms=Array.isArray(raw.by_unit)?raw.by_unit:[];
  const yaw=record(clocks.yaw_angular_rate);
  return <details className={styles.details} data-testid="motion-evidence-panel">
    <summary>{t("motionEvidence.title")}</summary>
    <Button data-testid="motion-evidence-run" disabled={busy} onClick={()=>void run()}>{t(busy?"motionEvidence.running":"motionEvidence.run")}</Button>
    <p className={styles.hint}>{t("motionEvidence.hint")}</p>
    {error&&<p role="alert">{t("motionEvidence.failure")} · <code>{error}</code></p>}
    {report&&<div data-testid="motion-evidence-report" aria-live="polite">
      <p>{t("motionEvidence.status."+str(report.status))}</p>
      <p className={styles.hint}>{t("motionEvidence.counts",{valid:Number(record(report.input).records_unique??0),rejected:Number(record(report.input).records_rejected??0)})}</p>
      <p>{t("motionEvidence.gaps",{p50:formatNumber(gaps.p50,language,3),p95:formatNumber(gaps.p95,language,3),max:formatNumber(gaps.max,language,3)})}</p>
      <p className={styles.hint}>{t("motionEvidence.sampling")}</p>
      {norms.map((value,index)=>{const item=record(value);const distribution=record(item.distribution);return <p key={index}>{t("motionEvidence.rawNorm",{value:formatNumber(distribution.mean,language,3),unit:str(item.unit)||"—"})}</p>;})}
      <p>{t(yaw.status==="available"?"motionEvidence.yawAvailable":"motionEvidence.yawUnqualified")}</p>
      <p>{t("motionEvidence.speedUnavailable")}</p>
      <Details value={{quality:report.quality,sampling:clocks.sampling,samples,yaw,collection_status:report.collection_status,input:report.input,settings:report.settings}} t={t}/>
    </div>}
  </details>;
}
