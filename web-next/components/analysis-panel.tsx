import { createBrowserUuid } from "@/lib/optional-modules/transport";
/* eslint-disable @next/next/no-img-element -- Immutable local source and analysis artifacts. */
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { getApiBaseUrl } from "@/lib/env";
import { errorCode, formatTime, record, str, type JsonRecord } from "../lib/model";
import { SAMPLES_BASE } from "../lib/samples";
import { analysisProfileSettings, semanticProfile, type AnalysisProfile } from "../lib/analysis-profile";
import type { Translator } from "../lib/i18n";
import { Button, Details, Field, Input, Select } from "./primitives";
import styles from "../rider.module.css";

const BASE="/api/v1/rider/analysis";
const PENDING="rider.analysis.pending.v1";
type Run=JsonRecord&{id:string;state:string;done:number;total:number;phase:string;created:number;error?:string};
type Profile=AnalysisProfile;
const terminal=(run:Run|null)=>!!run&&["succeeded","failed","cancelled"].includes(run.state);
const metric=(value:unknown)=>typeof value==="number"?Number(value.toFixed(3)).toString():"N/A";
function remembered(session:string):string {
  try {const value=record(JSON.parse(sessionStorage.getItem(PENDING)||"{}"));return value.session_id===session&&/^[a-f0-9]{32}$/.test(str(value.request_id))?str(value.request_id):"";}
  catch{return "";}
}
export function AnalysisPanel({session,t,language}:{session:string;t:Translator;language:string}) {
  const [profiles,setProfiles]=useState<Profile[]>([]);
  const [profile,setProfile]=useState("da2-default");
  const [windowSize,setWindowSize]=useState(1);
  const [quality,setQuality]=useState("production");
  const [count,setCount]=useState(50);
  const [offset,setOffset]=useState(0);
  const [roi,setRoi]=useState([0.35,0.8,0.65,0.95]);
  const [residual,setResidual]=useState(0.06);
  const [outliers,setOutliers]=useState(0.005);
  const [slope,setSlope]=useState(2);
  const [geometry,setGeometry]=useState("image");
  const [fx,setFx]=useState(Number.NaN);
  const [fy,setFy]=useState(Number.NaN);
  const [radius,setRadius]=useState(Number.NaN);
  const [lookahead,setLookahead]=useState(2);
  const [runs,setRuns]=useState<Run[]>([]);
  const [selected,setSelected]=useState("");
  const [run,setRun]=useState<Run|null>(null);
  const [report,setReport]=useState<JsonRecord|null>(null);
  const [frame,setFrame]=useState(0);
  const [other,setOther]=useState("");
  const [comparison,setComparison]=useState<JsonRecord|null>(null);
  const [pending,setPending]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const refresh=useCallback(async()=>{
    const response=await apiFetch<{runs:Run[]}>(BASE+"/runs?session_id="+session);
    setRuns(response.runs);
  },[session]);
  useEffect(()=>{
    let current=true;
    void apiFetch<{profiles:Profile[]}>(BASE+"/profiles").then(value=>{if(current)setProfiles(value.profiles);}).catch(err=>{if(current)setError(errorCode(err));});
    void refresh().catch(err=>{if(current)setError(errorCode(err));});
    setPending(remembered(session));
    return()=>{current=false;};
  },[refresh,session]);
  const recover=useCallback(async(id:string)=>{
    try {
      const value=await apiFetch<Run>(BASE+"/requests/"+id);
      setSelected(value.id);setRun(value);setPending("");sessionStorage.removeItem(PENDING);setError("");await refresh();
    }catch(err){setError(errorCode(err));}
  },[refresh]);
  useEffect(()=>{if(pending)void recover(pending);},[pending,recover]);
  useEffect(()=>{
    if(!selected){setRun(null);setReport(null);return;}
    let current=true;
    let timer:ReturnType<typeof setTimeout>|undefined;
    const poll=async()=>{
      try{
        const value=await apiFetch<Run>(BASE+"/runs/"+selected);
        if(!current)return;
        setRun(value);setError("");
        if(!terminal(value))timer=setTimeout(()=>void poll(),500);
        else {
          await refresh();
          if(value.state==="succeeded"){
            const data=await apiFetch<JsonRecord>(BASE+"/runs/"+selected+"/artifacts/report.json");
            if(current)setReport(data);
          }
        }
      }catch(err){if(current){setError(errorCode(err));timer=setTimeout(()=>void poll(),1500);}}
    };
    setReport(null);setFrame(0);setComparison(null);void poll();
    return()=>{current=false;if(timer)clearTimeout(timer);};
  },[selected,refresh]);
  const available=profiles.find(item=>item.id===profile);
  const semantic=semanticProfile(available);
  const start=async()=>{
    setBusy(true);setError("");
    const request=createBrowserUuid().replaceAll("-","");
    const settings=analysisProfileSettings(available,windowSize,{mode:geometry,fx:Number.isFinite(fx)?fx:null,fy:Number.isFinite(fy)?fy:null,radius_ratio:Number.isFinite(radius)?radius:null,lookahead_ratio:lookahead},{residual_limit:residual,outlier_limit:outliers,slope_min:slope});
    const intent={session_id:session,request_id:request,spec:{profile,quality_mode:quality,count,offset,corridor:roi,...settings}};
    try{
      sessionStorage.setItem(PENDING,JSON.stringify({session_id:session,request_id:request}));
      const value=await apiFetch<Run>(BASE+"/runs",{method:"POST",body:JSON.stringify(intent)});
      sessionStorage.removeItem(PENDING);setPending("");setReport(null);setSelected(value.id);setRun(value);await refresh();
    }catch(err){setPending(request);setError(errorCode(err));}
    finally{setBusy(false);}
  };
  const cancel=async()=>{
    if(!run)return;
    try{setRun(await apiFetch<Run>(BASE+"/runs/"+run.id+"/cancel",{method:"POST",body:"{}"}));}
    catch(err){setError(errorCode(err));}
  };
  const compare=async()=>{
    try{setComparison(await apiFetch<JsonRecord>(BASE+"/compare?first="+selected+"&second="+other));}
    catch(err){setError(errorCode(err));}
  };
  const number=(label:string,value:number,change:(value:number)=>void,min:number,max:number,step:number=1)=><Field label={t("analysis."+label)}><Input type="number" value={Number.isFinite(value)?value:""} min={min} max={max} step={step} onChange={event=>change(event.target.value===""?NaN:Number(event.target.value))}/></Field>;
  const row=record((Array.isArray(report?.rows)?report.rows:[])[frame]);
  const summary=record(report?.summary);
  const artifact=(name:string)=>getApiBaseUrl()+BASE+"/runs/"+selected+"/artifacts/"+name;
  return <section data-testid="analysis-panel">
    <h3>{t("analysis.title")}</h3><p className={styles.hint}>{t("analysis.hint")}</p>
    {error&&<p role="alert">{t("analysis.failure")} · <code>{error}</code></p>}
    {pending&&<p>{t("analysis.unknownStart")} <Button data-testid="analysis-recover" onClick={()=>void recover(pending)}>{t("analysis.recover")}</Button><Button onClick={()=>{setPending("");sessionStorage.removeItem(PENDING);setError("");}}>{t("analysis.newIntent")}</Button></p>}
    <div className={styles.grid}>
      <Field label={t("analysis.profile")}><Select aria-label={t("analysis.profile")} data-testid="analysis-profile" value={profile} onChange={event=>{setProfile(event.target.value);setWindowSize(1);}}>{profiles.map(item=><option key={item.id} value={item.id} disabled={!item.installed}>{item.id}{!item.installed?" · "+t("analysis.unavailable"):""}</option>)}</Select></Field>
      <Field label={t("analysis.window")}><Select aria-label={t("analysis.window")} value={windowSize} onChange={event=>setWindowSize(Number(event.target.value))}>{(available?.windows??[1]).map(value=><option key={value} value={value}>{value}</option>)}</Select></Field>
    </div>
    <details className={styles.details}><summary>{t("analysis.parameters")}</summary><div className={styles.grid}>
      {number("count",count,setCount,1,200)}{number("offset",offset,setOffset,0,999)}
      {roi.map((value,index)=><span key={index}>{number("roi"+index,value,next=>setRoi(previous=>previous.map((item,i)=>i===index?next:item)),0,1,0.01)}</span>)}
      {!semantic&&<><Field label={t("geometryMode")}><Select aria-label={t("geometryMode")} value={geometry} onChange={event=>setGeometry(event.target.value)}><option value="image">{t("geometryImage")}</option><option value="ground">{t("geometryGround")}</option></Select></Field>
      {geometry==="ground"&&<>{number("fx",fx,setFx,0.1,5,0.01)}{number("fy",fy,setFy,0.1,5,0.01)}{number("radius",radius,setRadius,0.05,5,0.01)}{number("lookahead",lookahead,setLookahead,0.25,8,0.05)}<p className={styles.hint}>{t("analysis.calibrationHint")}</p></>}
      {number("residual",residual,setResidual,0.01,0.15,0.001)}{number("outliers",outliers,setOutliers,0,0.02,0.001)}{number("slope",slope,setSlope,1,4,0.1)}</>}
      <Field label={t("analysis.quality")}><Select aria-label={t("analysis.quality")} value={quality} onChange={event=>setQuality(event.target.value)}><option value="production">{t("analysis.production")}</option><option value="model_probe">{t("analysis.probe")}</option></Select></Field>
    </div><p className={styles.hint}>{t(semantic?"analysis.semanticHint":"analysis.windowHint")}</p></details>
    <div className={styles.flex}><Button data-testid="analysis-start" tone="primary" disabled={busy||!!pending||!available?.installed} onClick={()=>void start()}>{t("analysis.start")}</Button><Button onClick={()=>void refresh().catch(err=>setError(errorCode(err)))}>{t("refresh")}</Button></div>
    <Field label={t("analysis.runs")}><Select aria-label={t("analysis.runs")} data-testid="analysis-run" value={selected} onChange={event=>setSelected(event.target.value)}><option value="">{t("samples.choose")}</option>{runs.map(item=><option key={item.id} value={item.id}>{formatTime(item.created,language)} · {t("analysis.state."+item.state)} · {item.id.slice(0,8)}</option>)}</Select></Field>
    {run&&<div data-testid="analysis-status"><p aria-live="polite">{t("analysis.state."+run.state)} · {t("analysis.phase."+run.phase)} · {run.done}/{run.total}</p><progress max={run.total} value={run.done}/>{run.error&&<p role="alert"><code>{run.error}</code></p>}{!terminal(run)&&<Button data-testid="analysis-cancel" disabled={run.cancel_requested===true} onClick={()=>void cancel()}>{t(run.cancel_requested?"analysis.cancelling":"analysis.cancel")}</Button>}</div>}
    {report&&<>
      <div className={styles.grid}>{["frames","approved_labels","unknown","rejected_inputs","false_go","false_stop","state_flips"].map(name=><p key={name}>{t("analysis.metric."+name)}: <strong>{metric(summary[name])}</strong></p>)}</div>
      <p className={styles.hint}>{t("analysis.naHint")}</p>
      <div className={styles.grid}>{["model_load_ms","queue_ms","peak_rss_mib","peak_vram_mib"].map(name=><p key={name}>{t("analysis.metric."+name)}: {metric(report[name])}</p>)}</div>
      <table className={styles.analysisTable}><thead><tr><th>{t("analysis.stage")}</th><th>p50 ms</th><th>p95 ms</th></tr></thead><tbody>{Object.entries(record(summary.timings_ms)).map(([name,value])=><tr key={name}><td>{t("analysis.timing."+name)}</td><td>{metric(record(value).p50)}</td><td>{metric(record(value).p95)}</td></tr>)}</tbody></table>
      <Field label={t("analysis.frame")}><Select aria-label={t("analysis.frame")} data-testid="analysis-frame" value={frame} onChange={event=>setFrame(Number(event.target.value))}>{(report.rows as JsonRecord[]).map((item,index)=><option key={str(item.sample_id)} value={index}>#{index+1} · {t("samples.value."+str(item.state))} · {str(item.sample_id).slice(0,8)}</option>)}</Select></Field>
      <div className={styles.analysisMedia}><div><p>{t("samples.original")}</p><img className={styles.analysisImage} src={getApiBaseUrl()+SAMPLES_BASE+"/items/"+str(row.sample_id)+"/original"} alt={t("samples.original")}/></div>{["depth.png","overlay.jpg"].map(name=>{const file=String(frame).padStart(4,"0")+"-"+name;return Array.isArray(row.artifact_names)&&row.artifact_names.includes(file)?<div key={file}><p>{t(name==="depth.png"?"analysis.depth":"analysis.overlay")}</p><img className={styles.analysisImage} src={artifact(file)} alt={t(name==="depth.png"?"analysis.depth":"analysis.overlay")}/></div>:null;})}</div>
      <p>{t("samples.value."+str(row.state))} · <code>{str(row.reason)}</code></p><code className={styles.analysisHash}>{str(row.raw_sha256)}</code>
      <a className={styles.button} data-testid="analysis-report" href={artifact("report.json")} download>{t("analysis.download")}</a>
      <Field label={t("analysis.compareWith")}><Select aria-label={t("analysis.compareWith")} value={other} onChange={event=>{setOther(event.target.value);setComparison(null);}}><option value="">{t("samples.choose")}</option>{runs.filter(item=>item.state==="succeeded"&&item.id!==selected).map(item=><option key={item.id} value={item.id}>{formatTime(item.created,language)} · {item.id.slice(0,8)}</option>)}</Select></Field>
      <Button data-testid="analysis-compare" disabled={!other} onClick={()=>void compare()}>{t("analysis.compare")}</Button>
      {comparison&&<div><p>{t(comparison.aligned_inputs_and_labels?"analysis.aligned":"analysis.mismatch")}</p><table className={styles.analysisTable}><thead><tr><th>{t("analysis.metric")}</th><th>A</th><th>B</th><th>Δ B−A</th></tr></thead><tbody>{["false_go_rate","false_stop_rate","flicker_rate"].map(key=><tr key={key}><td>{t("analysis.metric."+key)}</td><td>{metric(record(record(comparison.first).summary)[key])}</td><td>{metric(record(record(comparison.second).summary)[key])}</td><td>{metric(record(comparison.quality_delta_second_minus_first)[key])}</td></tr>)}</tbody></table></div>}
      <Details t={t} value={{profile:record(report.context).profile,spec:record(report.context).spec,dataset_revision:record(report.context).dataset_revision,input_digest:report.input_digest,row}}/>
    </>}
  </section>;
}
