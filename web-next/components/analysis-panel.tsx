import { createBrowserUuid } from "@/lib/optional-modules/transport";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { apiFetch } from "@/lib/api-client";
import { getApiBaseUrl } from "@/lib/env";
import { data, errorCode, formatTime, record, str, type JsonRecord } from "../lib/model";
import type { SessionVideo } from "../lib/samples";
import { reportChunks, readAnalysisChunk, sameAnalysisSettings, videoRun, type AnalysisRow } from "../lib/analysis-video";
import {SampleFacts} from "./sample-facts";
import { SourceVideoPlayer,type SourcePlaybackRate } from "./source-video-player";
import { AnalysisTimeline } from "./analysis-timeline";
import { AnalysisFrameMedia } from "./analysis-frame-media";
import { analysisProfileSettings, LEGACY_ANALYSIS_BEHAVIOR, riderAnalysisSettings, semanticProfile, type AnalysisProfile } from "../lib/analysis-profile";
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
export function AnalysisPanel({session,t,language,sessionVideo,sessionPicker,sessionSummary}:{session:string;t:Translator;language:string;sessionVideo?:SessionVideo|null;sessionPicker?:ReactNode;sessionSummary?:ReactNode}) {
  const [profiles,setProfiles]=useState<Profile[]>([]);
  const [profile,setProfile]=useState("da2-default");
  const [windowSize,setWindowSize]=useState(1);
  const [quality,setQuality]=useState("production");
  const [count,setCount]=useState(50);
  const [offset,setOffset]=useState(0);
  const [inputSource,setInputSource]=useState<"samples"|"video">("samples");
  const sourceChosen=useRef(false);
  const canVideo=!!sessionVideo&&sessionVideo.frames>0&&typeof sessionVideo.ended_at==="number";
  const [sourcePlayback,setSourcePlayback]=useState({index:0,playing:false});
  const [playbackRate,setPlaybackRate]=useState<SourcePlaybackRate>(1);
  useEffect(()=>{if(canVideo&&!sourceChosen.current)setInputSource("video");},[canVideo]);
  const [roi,setRoi]=useState([0.35,0.8,0.65,0.95]);
  const [residual,setResidual]=useState(0.06);
  const [outliers,setOutliers]=useState(0.005);
  const [slope,setSlope]=useState(2);
  const [behavior,setBehavior]=useState(LEGACY_ANALYSIS_BEHAVIOR);
  const [semanticModels,setSemanticModels]=useState<JsonRecord>({});
  const [importedRevision,setImportedRevision]=useState<number|null>(null);
  const [importing,setImporting]=useState(false), [importError,setImportError]=useState("");
  const importEpoch=useRef(0);
  const invalidateImport=useCallback(()=>{importEpoch.current++;},[]);
  useEffect(()=>{setImporting(false);setImportError("");return invalidateImport;},[session,invalidateImport]);

  const [geometry,setGeometry]=useState("image");
  const [fx,setFx]=useState(Number.NaN);
  const [fy,setFy]=useState(Number.NaN);
  const [radius,setRadius]=useState(Number.NaN);
  const [lookahead,setLookahead]=useState(2);
  const [runs,setRuns]=useState<Run[]>([]);
  const [selected,setSelected]=useState("");
  const [run,setRun]=useState<Run|null>(null);
  const [report,setReport]=useState<JsonRecord|null>(null);
  const [videoRows,setVideoRows]=useState<AnalysisRow[]|null>(null);
  const [rowsError,setRowsError]=useState("");
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
  const activeReport=report?.run_id===selected?report:null;
  useEffect(()=>{
    const abort=new AbortController();
    setVideoRows(null);setRowsError("");
    if(!activeReport||!videoRun(activeReport))return;
    void(async()=>{
      try{
        const names=reportChunks(activeReport,selected),rows:AnalysisRow[]=[];
        for(let offset=0;offset<names.length;offset+=4){
          const batch=await Promise.all(names.slice(offset,offset+4).map(async(name,index)=>{
            const value=await apiFetch<unknown>(BASE+"/runs/"+selected+"/artifacts/"+name,{signal:abort.signal});
            const chunk=readAnalysisChunk(value,(offset+index)*128);
            if(offset+index+1<names.length&&chunk.length!==128)throw new Error("analysis_video_report_invalid");
            return chunk;
          }));
          if(abort.signal.aborted)return;
          rows.push(...batch.flat());
        }
        if(rows.length!==Number(record(activeReport.summary).frames))throw new Error("analysis_video_report_invalid");
        if(!abort.signal.aborted){setVideoRows(rows);}
      }catch(err){if(!abort.signal.aborted)setRowsError(errorCode(err));}
    })();
    return()=>abort.abort();
  },[activeReport,selected]);
  const available=profiles.find(item=>item.id===profile);
  const semantic=semanticProfile(available);
  const loadRiderSettings=async()=>{
    const epoch=++importEpoch.current;
    setImporting(true);setImportError("");
    try {
      const response=await apiFetch<JsonRecord>("/api/v1/rider/navigation/config",{cache:"no-store"});
      if(epoch!==importEpoch.current)return;
      const snapshot=riderAnalysisSettings(data(response).config);
      setBehavior(snapshot.behavior);setSemanticModels(record(snapshot.perception.semantic_models));setRoi(snapshot.corridor);
      setResidual(Number(snapshot.perception.residual_limit));setOutliers(Number(snapshot.perception.outlier_limit));setSlope(Number(snapshot.perception.slope_min));
      setImportedRevision(snapshot.revision);
    } catch(err){if(epoch===importEpoch.current)setImportError(errorCode(err));}
    finally{if(epoch===importEpoch.current)setImporting(false);}
  };
  const resetRiderSettings=()=>{
    importEpoch.current++;setImporting(false);setImportError("");setImportedRevision(null);
    setBehavior({...LEGACY_ANALYSIS_BEHAVIOR});setSemanticModels({});setRoi([.35,.8,.65,.95]);setResidual(.06);setOutliers(.005);setSlope(2);
  };
  const settings=analysisProfileSettings(available,windowSize,{mode:geometry,fx:Number.isFinite(fx)?fx:null,fy:Number.isFinite(fy)?fy:null,radius_ratio:Number.isFinite(radius)?radius:null,lookahead_ratio:lookahead},{residual_limit:residual,outlier_limit:outliers,slope_min:slope,...(Object.keys(semanticModels).length?{semantic_models:semanticModels}:{})});
  const currentSpec:JsonRecord={profile,quality_mode:quality,...(inputSource==="samples"?{count,offset}:{}),input_source:inputSource,corridor:roi,behavior:{...behavior},...settings};
  const start=async()=>{
    setBusy(true);setError("");
    const request=createBrowserUuid().replaceAll("-","");
    const intent={session_id:session,request_id:request,spec:currentSpec};
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
  const rows:AnalysisRow[]=activeReport?(videoRun(activeReport)?videoRows??[]:Array.isArray(activeReport.rows)?activeReport.rows as AnalysisRow[]:[]):[];
  const row=record(rows[frame]);
  const summary=record(activeReport?.summary);
  const frozen=record(record(activeReport?.context).spec),savedBehavior=record(frozen.behavior);
  const settingsChanged=!!activeReport&&!sameAnalysisSettings(activeReport,currentSpec);
  const showVideoFrame=useCallback((index:number)=>setFrame(index),[]);
  const videoComparison=useMemo(()=>activeReport&&videoRun(activeReport)&&videoRows?{report:activeReport,runId:selected,rows:videoRows}:null,[activeReport,selected,videoRows]);
  const chooseFrame=(index:number)=>{
    if(activeReport&&videoRun(activeReport)&&Number.isSafeInteger(rows[index]?.source_index))setSourcePlayback({index:Number(rows[index].source_index),playing:false});
    else setFrame(index);
  };
  const artifact=(name:string)=>getApiBaseUrl()+BASE+"/runs/"+selected+"/artifacts/"+name;
  return <section data-testid="analysis-panel">
    {canVideo&&sessionVideo&&(!activeReport||videoRun(activeReport))&&<SourceVideoPlayer key={session+":"+selected} session={session} video={sessionVideo} position={sourcePlayback} onPositionChange={setSourcePlayback} rate={playbackRate} onRateChange={setPlaybackRate} comparison={videoComparison} onDisplayed={showVideoFrame} t={t}/>}
    {activeReport&&!videoRun(activeReport)&&<>
      {!!rows.length&&<>
      <AnalysisFrameMedia key={selected+":"+frame} report={activeReport} runId={selected} session={session} row={row} index={frame} t={t}/>
      <Field label={t("analysis.frame")}>
        {videoRun(activeReport)?<Input aria-label={t("analysis.frame")} data-testid="analysis-frame" type="number" min={1} max={rows.length} value={frame+1} onChange={event=>{const next=Number(event.target.value)-1;if(Number.isSafeInteger(next)&&next>=0&&next<rows.length)chooseFrame(next);}}/>
          :<Select aria-label={t("analysis.frame")} data-testid="analysis-frame" value={frame} onChange={event=>chooseFrame(Number(event.target.value))}>{rows.map((item,index)=><option key={str(item.sample_id)} value={index}>#{index+1} · {t("samples.value."+str(item.state))} · {str(item.sample_id).slice(0,8)}</option>)}</Select>}
      </Field>
      </>}
    </>}
    <section className={styles.sampleReviewGrid} data-testid="sample-review-info" aria-label={t("samples.reviewInfo")}>
      <div className={styles.sampleReviewBox} data-testid="recording-summary-box">{sessionPicker}{sessionSummary}</div>
      <div className={styles.sampleReviewBox} data-testid="analysis-actions-box">
    <Field label={t("analysis.runs")}><Select aria-label={t("analysis.runs")} data-testid="analysis-run" value={selected} onChange={event=>{setSourcePlayback(previous=>({...previous,playing:false}));setSelected(event.target.value);}}><option value="">{t("analysis.chooseRun")}</option>{runs.map(item=><option key={item.id} value={item.id}>{formatTime(item.created,language)} · {t("analysis.state."+item.state)} · {item.id.slice(0,8)}</option>)}</Select></Field>
    {run&&<div data-testid="analysis-status" className={styles.sampleRunStatus}><span aria-live="polite">{t("analysis.state."+run.state)} · {run.done}/{run.total}</span>{!terminal(run)&&<progress max={run.total} value={run.done}/>}{run.error&&<p role="alert"><code>{run.error}</code></p>}{!terminal(run)&&<Button data-testid="analysis-cancel" disabled={run.cancel_requested===true} onClick={()=>void cancel()}>{t(run.cancel_requested?"analysis.cancelling":"analysis.cancel")}</Button>}</div>}
    <div className={styles.sampleAnalysisActions}><Button data-testid="analysis-start" tone="primary" disabled={busy||!!pending||!available?.installed||(inputSource==="video"&&!canVideo)} onClick={()=>void start()}>{t(inputSource==="video"?"analysis.startVideo":"analysis.start")}</Button><Button onClick={()=>void refresh().catch(err=>setError(errorCode(err)))}>{t("refresh")}</Button></div>
      </div>
    </section>
    {activeReport&&<>
      <div data-testid="analysis-result-snapshot" className={styles.sampleInfoCard}><SampleFacts items={[{label:t("analysis.runs"),value:selected.slice(0,8)},{label:t("analysis.profile"),value:str(frozen.profile)||str(record(record(activeReport.context).profile).id)},{label:t("behavior.title"),value:t("analysis.savedPolicy",{profile:t("behavior.profile."+str(savedBehavior.profile_id)),revision:String(savedBehavior.profile_revision??"—")})}]}/><details className={styles.details}><summary>{t("analysis.resultScopeHelp")}</summary><p className={styles.hint} data-testid="analysis-result-scope">{t("analysis.resultScope")}</p></details>{settingsChanged&&<p role="status" data-testid="analysis-result-different">{t("analysis.resultDifferent")} <Button data-testid="analysis-new-from-settings" disabled={busy||!!pending||!available?.installed||(inputSource==="video"&&!canVideo)} onClick={()=>void start()}>{t("analysis.newRun")}</Button></p>}</div>
      {rowsError&&<p role="alert">{t("analysis.rowsFailure")} · <code>{rowsError}</code></p>}
      {!!rows.length&&<AnalysisTimeline rows={rows} index={frame} onSelect={chooseFrame} t={t} language={language} controls={!videoRun(activeReport)}/>}
    </>}
    {error&&<p role="alert">{t("analysis.failure")} · <code>{error}</code></p>}
    {pending&&<p>{t("analysis.unknownStart")} <Button data-testid="analysis-recover" onClick={()=>void recover(pending)}>{t("analysis.recover")}</Button><Button onClick={()=>{setPending("");sessionStorage.removeItem(PENDING);setError("");}}>{t("analysis.newIntent")}</Button></p>}
    <details className={`${styles.details} ${styles.sampleInfoCard}`} data-testid="analysis-settings"><summary>{t("analysis.newStudy")}</summary>
    <Field label={t("analysis.source")}><Select data-testid="analysis-input-source" value={inputSource} onChange={event=>{sourceChosen.current=true;setInputSource(event.target.value as "samples"|"video");}}><option value="samples">{t("analysis.source.samples")}</option><option value="video" disabled={!canVideo}>{t("analysis.source.video")}</option></Select></Field>
    {inputSource==="video"&&<p className={styles.hint}>{t("analysis.wholeVideoHint")}</p>}
    <div className={styles.grid}>
      <Field label={t("analysis.profile")}><Select aria-label={t("analysis.profile")} data-testid="analysis-profile" value={profile} onChange={event=>{setProfile(event.target.value);setWindowSize(1);}}>{profiles.map(item=><option key={item.id} value={item.id} disabled={!item.installed}>{item.id}{!item.installed?" · "+t("analysis.unavailable"):""}</option>)}</Select></Field>
      <Field label={t("analysis.window")}><Select aria-label={t("analysis.window")} value={windowSize} onChange={event=>setWindowSize(Number(event.target.value))}>{(available?.windows??[1]).map(value=><option key={value} value={value}>{value}</option>)}</Select></Field>
    </div>
    <div className={styles.stack} data-testid="analysis-rider-settings">
      <div className={styles.flex}><Button data-testid="analysis-load-rider" disabled={importing||busy} onClick={()=>void loadRiderSettings()}>{t(importing?"analysis.loadingRider":"analysis.loadRider")}</Button><Button data-testid="analysis-reset-rider" disabled={busy} onClick={resetRiderSettings}>{t("analysis.resetRider")}</Button></div>
      <p className={styles.hint} data-testid="analysis-rider-source">{t(importedRevision===null?"analysis.riderDefaults":"analysis.riderLoaded",{revision:importedRevision??"—"})}</p>
      <p className={styles.small} data-testid="analysis-rider-effective">{t("analysis.riderSettingsState",{profile:behavior.profile_id,surface:semantic?str(record(semanticModels[profile]).surface)||"floor_only":t("analysis.depthSurface")})}</p>
      <p className={styles.hint}>{t("analysis.classificationScope")}</p>
      {importError&&<p className={styles.loopError} role="alert" data-testid="analysis-rider-error">{t("analysis.loadRiderFailed")} · <code>{importError}</code></p>}
    </div>
    <details className={styles.details}><summary>{t("analysis.parameters")}</summary><div className={styles.grid}>
      {inputSource==="samples"&&<>{number("count",count,setCount,1,200)}{number("offset",offset,setOffset,0,999)}</>}
      {roi.map((value,index)=><span key={index}>{number("roi"+index,value,next=>setRoi(previous=>previous.map((item,i)=>i===index?next:item)),0,1,0.01)}</span>)}
      {!semantic&&<><Field label={t("geometryMode")}><Select aria-label={t("geometryMode")} value={geometry} onChange={event=>setGeometry(event.target.value)}><option value="image">{t("geometryImage")}</option><option value="ground">{t("geometryGround")}</option></Select></Field>
      {geometry==="ground"&&<>{number("fx",fx,setFx,0.1,5,0.01)}{number("fy",fy,setFy,0.1,5,0.01)}{number("radius",radius,setRadius,0.05,5,0.01)}{number("lookahead",lookahead,setLookahead,0.25,8,0.05)}<p className={styles.hint}>{t("analysis.calibrationHint")}</p></>}
      {number("residual",residual,setResidual,0.01,0.15,0.001)}{number("outliers",outliers,setOutliers,0,0.02,0.001)}{number("slope",slope,setSlope,1,4,0.1)}</>}
      <Field label={t("analysis.quality")}><Select aria-label={t("analysis.quality")} value={quality} onChange={event=>setQuality(event.target.value)}><option value="production">{t("analysis.production")}</option><option value="model_probe">{t("analysis.probe")}</option></Select></Field>
    </div><p className={styles.hint}>{t(semantic?"analysis.semanticHint":"analysis.windowHint")}</p></details>
    </details>

    {activeReport&&<details className={styles.details} data-testid="analysis-statistics"><summary>{t("analysis.statistics")}</summary>
      <div className={styles.grid}>{["frames","approved_labels","unknown","rejected_inputs","false_go","false_stop","state_flips"].map(name=><p key={name}>{t("analysis.metric."+name)}: <strong>{metric(summary[name])}</strong></p>)}</div>
      <p className={styles.hint}>{t("analysis.naHint")}</p>
      <div className={styles.grid}>{["model_load_ms","queue_ms","peak_rss_mib","peak_vram_mib"].map(name=><p key={name}>{t("analysis.metric."+name)}: {metric(activeReport[name])}</p>)}</div>
      <table className={styles.analysisTable}><thead><tr><th>{t("analysis.stage")}</th><th>p50 ms</th><th>p95 ms</th></tr></thead><tbody>{Object.entries(record(summary.timings_ms)).map(([name,value])=><tr key={name}><td>{t("analysis.timing."+name)}</td><td>{metric(record(value).p50)}</td><td>{metric(record(value).p95)}</td></tr>)}</tbody></table>
      <p>{t("samples.value."+str(row.state))} · <code>{str(row.reason)}</code></p><code className={styles.analysisHash}>{str(row.raw_sha256)}</code>
      <a className={styles.button} data-testid="analysis-report" href={artifact("report.json")} download>{t("analysis.download")}</a>
      <Field label={t("analysis.compareWith")}><Select aria-label={t("analysis.compareWith")} value={other} onChange={event=>{setOther(event.target.value);setComparison(null);}}><option value="">{t("samples.choose")}</option>{runs.filter(item=>item.state==="succeeded"&&item.id!==selected).map(item=><option key={item.id} value={item.id}>{formatTime(item.created,language)} · {item.id.slice(0,8)}</option>)}</Select></Field>
      <Button data-testid="analysis-compare" disabled={!other} onClick={()=>void compare()}>{t("analysis.compare")}</Button>
      {comparison&&<div><p>{t(comparison.aligned_inputs_and_labels?"analysis.aligned":"analysis.mismatch")}</p><table className={styles.analysisTable}><thead><tr><th>{t("analysis.metric")}</th><th>A</th><th>B</th><th>Δ B−A</th></tr></thead><tbody>{["false_go_rate","false_stop_rate","flicker_rate"].map(key=><tr key={key}><td>{t("analysis.metric."+key)}</td><td>{metric(record(record(comparison.first).summary)[key])}</td><td>{metric(record(record(comparison.second).summary)[key])}</td><td>{metric(record(comparison.quality_delta_second_minus_first)[key])}</td></tr>)}</tbody></table></div>}
      <Details t={t} value={{profile:record(activeReport.context).profile,spec:record(activeReport.context).spec,dataset_revision:record(activeReport.context).dataset_revision,input_digest:activeReport.input_digest,row}}/>
    </details>}
  </section>;
}
