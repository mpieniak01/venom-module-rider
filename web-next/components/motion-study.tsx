import {PulseSeriesComparison} from "./pulse-series-comparison";
import {AttitudeComparison} from "./attitude-view";
import {replayStep} from "../lib/attitude";
import {useEffect,useMemo,useState} from "react";
import {apiFetch} from "@/lib/api-client";
import {getApiBaseUrl} from "@/lib/env";
import { scalarText,errorCode,formatNumber,formatTime,record} from "../lib/model";
import {angleStream,axisNumber,readTimeline,pointAt,profileAt,streamTimes,nearestTime,trialRequests,alignTrial,pulseEvidence,type MotionStream,type MotionTimeline} from "../lib/motion-timeline";
import {SAMPLES_BASE,readSessions,type Collection} from "../lib/samples";
import type {StudyRecording} from "../lib/use-study-recording";
import type {Translator} from "../lib/i18n";
import {Button,Details,Empty,Field,Input,Panel,Range,Select} from "./primitives";
import {CommandTrace,MotionTrace,outputStream} from "./motion-trace";
import styles from "../rider.module.css";

function useTimeline(id:string,refresh:number|string){
  const key=id+":"+refresh;
  const [result,setResult]=useState<{key:string;value:MotionTimeline|null;error:string}>({key:"",value:null,error:""});
  useEffect(()=>{
    if(!id)return;
    const controller=new AbortController();
    void apiFetch(SAMPLES_BASE+"/sessions/"+id+"/motion-timeline",{signal:controller.signal})
      .then(r=>{if(!controller.signal.aborted)setResult({key,value:readTimeline(r),error:""});})
      .catch(err=>{if(!controller.signal.aborted)setResult({key,value:null,error:errorCode(err)});});
    return()=>controller.abort();
  },[id,refresh,key]);
  return result.key===key?result:{value:null,error:""};
}

function trialSelection(source:MotionStream|null,id:string){
  if(id==="first"||trialRequests(source).some(event=>event.operation_id===id)||source?.trials?.some(run=>"series:"+run.run_id===id))return id;
  return source?.trials?.[0]?"series:"+source.trials[0].run_id:"first";
}

export function MotionStudy({recording,t,language}:{recording:StudyRecording;t:Translator;language:string}){
  const {surface,setSurface,capture}=recording;
  const [sessions,setSessions]=useState<Collection[]>([]);
  const [selectedA,setA]=useState<{id:string;captureId:string|undefined}|null>(null),[bId,setB]=useState("");
  const [aStream,setAStream]=useState(""),[bStream,setBStream]=useState("");
  const [aTrial,setATrial]=useState(""),[bTrial,setBTrial]=useState("");
  const [revision,refresh]=useState(0);
  const [error,setError]=useState("");
  const active=capture.session?.state==="collecting";
  const retired=record(capture.session?.evidence).retired===true;
  const currentId=capture.session?.session_id;
  const aId=(selectedA&&selectedA.captureId===currentId?selectedA.id:currentId)??"";
  useEffect(()=>{
    const controller=new AbortController();
    void apiFetch<{sessions:Collection[]}>(SAMPLES_BASE+"/sessions",{signal:controller.signal}).then(r=>{if(!controller.signal.aborted){setSessions(readSessions(r));setError("");}}).catch(err=>{if(!controller.signal.aborted)setError(errorCode(err));});
    return()=>controller.abort();
  },[revision,currentId,active]);
  const a=useTimeline(aId,revision+":"+capture.session?.state),b=useTimeline(bId,revision);
  const sourceA=a.value?.streams.find(s=>s.id===aStream)??a.value?.streams[0]??null;
  const sourceB=b.value?.streams.find(s=>s.id===bStream)??b.value?.streams[0]??null;
  const streamA=useMemo(()=>alignTrial(sourceA,trialSelection(sourceA,aTrial)),[sourceA,aTrial]),streamB=useMemo(()=>alignTrial(sourceB,trialSelection(sourceB,bTrial)),[sourceB,bTrial]);
  return <Panel title={t("study.title")} hint={t("study.hint")}>
    <div data-testid="motion-study">
      <Field label={t("study.surface")}><Input value={surface} maxLength={80} disabled={active||capture.busy} onChange={e=>setSurface(e.target.value)}/></Field>
      <div className={styles.flex} style={{marginTop:12}}>
        <Button data-testid="study-refresh" onClick={()=>refresh(r=>r+1)}>{t("refresh")}</Button>
      </div>
      <p role="status" className={styles.hint}>{t(retired?"study.retired":active?"study.recording":"study.globalHint")}</p>
      {(error||capture.error||a.error||b.error)&&<p role="alert">{t("requestFailed")} · {error||capture.error||a.error||b.error}</p>}
      <div className={styles.grid}>{(["A","B"] as const).map(name=><Field key={name} label={t("study.session")+" "+name}>
        <Select data-testid={"study-session-"+name} value={name==="A"?aId:bId} onChange={e=>{if(name==="A"){setA({id:e.target.value,captureId:currentId});setAStream("");}else{setB(e.target.value);setBStream("");}}}>
          <option value="">{t("samples.choose")}</option>
          {sessions.map(s=><option key={s.session_id} value={s.session_id}>{formatTime(s.started_at,language)+" · "+s.session_id.slice(0,8)+" · "+s.state}</option>)}
        </Select>
      </Field>)}</div>
      {([[a.value,aStream,setAStream],[b.value,bStream,setBStream]] as const).map(([value,id,set],index)=>value&&value.streams.length>1&&<Field key={index} label={t("study.stream")+" "+(index?"B":"A")}><Select value={id||value.streams[0].id} onChange={e=>set(e.target.value)}>{value.streams.map(s=><option key={s.id} value={s.id}>{s.device_id+" · "+s.source_session.slice(0,8)}</option>)}</Select></Field>)}
      {([["A",sourceA,aTrial,setATrial],["B",sourceB,bTrial,setBTrial]] as const).map(([label,source,id,set])=>(trialRequests(source).length>0||!!source?.trials?.length)&&<Field key={label} label={t("study.trial")+" "+label}><Select data-testid={"study-trial-"+label} value={trialSelection(source,id)} onChange={e=>set(e.target.value)}><option value="first">{t("study.firstTrial")}</option>{trialRequests(source).map(e=><option key={e.operation_id} value={e.operation_id}>{formatNumber(e.t,language,3)+" s · "+scalarText(e.intent.direction??"")+" · "+scalarText(e.intent.mode??"")+" · "+scalarText(e.intent.request_id??e.operation_id)}</option>)}{!!source?.trials?.length&&<optgroup label={t("pulse.series")}>{source.trials.map(run=><option key={run.run_id} value={"series:"+run.run_id}>{run.profile_id+" · "+run.count+" × "+run.pulse_ms+" ms · "+run.phase}</option>)}</optgroup>}</Select></Field>)}
      {a.value&&<MotionTimelineView key={a.value.dataset_revision+":"+b.value?.dataset_revision+":"+streamA?.id+":"+streamB?.id+":"+aTrial+":"+bTrial} a={a.value} b={b.value} streamA={streamA} streamB={streamB} t={t} language={language}/>}
      {aId&&<a className={styles.button} href={getApiBaseUrl()+SAMPLES_BASE+"/sessions/"+aId+"/export"} download>{t("samples.export")} A</a>}
    </div>
  </Panel>;
}
/** Keep timeline playback, quality evidence and comparison traces synchronized. */
export function MotionTimelineView({a,b,streamA,streamB,t,language}:{a:MotionTimeline;b:MotionTimeline|null;streamA:MotionStream|null;streamB:MotionStream|null;t:Translator;language:string}){
  /** Render recording context, sample quality and pulse evidence before playback controls. */
  function renderTimelineSummary(){return <>    <PulseSeriesComparison a={seriesA} b={seriesB} t={t} language={language}/>
    <p className={styles.hint}>{t("study.reception")} · {t("study.revision")} A {a.dataset_revision.slice(0,8)}{b?" · B "+b.dataset_revision.slice(0,8):""}</p>
    <p>{t("study.context")} A: {a.context?.replace("motion-study-v1:","")||t("unknown")}{b?" · B: "+(b.context?.replace("motion-study-v1:","")||t("unknown")):""}</p>
    {([["A",a],["B",b]] as const).map(([name,data])=>data&&<p key={name} data-testid={"study-quality-"+name} className={styles.hint}>{name}: {t("study.quality",{samples:Number(data.counts.imu_records??0),drops:Number(data.collection_status.dropped??0),failed:Number(data.collection_status.failed??0),gap:formatNumber(data.source_gaps_s.p95,language,3)})}</p>)}
    {([["A",streamA],["B",streamB]] as const).map(([name,stream])=>stream&&<div key={name} data-testid={"sampling-"+name} className={styles.hint}>{t("motion.sampling",{name,hz:formatNumber(stream.sampling_diagnostics?.observed_hz,language,2),gap:formatNumber(stream.sampling_diagnostics?.source_interval_max_s,language,3)})}{Array.isArray(stream.sampling_diagnostics?.pulses)&&stream.sampling_diagnostics.pulses.some(p=>record(p).insufficient===true)&&<p>{t("motion.sparse")}</p>}</div>)}
    <p className={styles.hint}>{t("motion.semantics")}</p>
    {b&&(!a.context?.replace("motion-study-v1:","")||a.context!==b.context)&&<p role="status">{t("study.contextDifferent")}</p>}
    {b&&trialA.event?.intent.direction!==trialB.event?.intent.direction&&<p role="status">{t("study.directionDifferent")}</p>}
    {!seriesA&&!seriesB&&<p className={styles.hint}>{t(trialA.peaks&&(!b||trialB.peaks)?"study.observedRange":"study.insufficient")}</p>}
    <Details t={t} value={{A:condition(trialA),...(b?{B:condition(trialB)}:{})}}/>
</>;}
  /** Render the PlaybackButton section using this render's captured state. */
  function renderPlaybackButton(){
    return (<Button data-testid="study-play" disabled={cursor>=max} aria-pressed={playing&&step.next!==null&&!step.gap} onClick={()=>{if(step.gap){chooseTime(step.next!);return;}setPlaying(p=>!p);}}>{t(playing&&step.next!==null&&!step.gap?"attitude.pause":"attitude.play")}</Button>);
  }

  const times=useMemo(()=>streamTimes(streamA,streamB),[streamA,streamB]);
  const anglesA=useMemo(()=>angleStream(streamA),[streamA]),anglesB=useMemo(()=>angleStream(streamB),[streamB]);
  const ratesA=useMemo(()=>angleStream(streamA,true),[streamA]),ratesB=useMemo(()=>angleStream(streamB,true),[streamB]);
  const [cursor,setCursor]=useState(times[0]??0),[zoom,setZoom]=useState("all");
  const [playing,setPlaying]=useState(false);
  const min=times[0]??0,max=times.at(-1)??0;
  const [reducedMotion,setReducedMotion]=useState(false);
  const step=replayStep(times,cursor);
  useEffect(()=>{const query=window.matchMedia("(prefers-reduced-motion: reduce)");const update=()=>{setReducedMotion(query.matches);setPlaying(false);};update();query.addEventListener("change",update);return()=>query.removeEventListener("change",update);},[]);
  useEffect(()=>{const hidden=()=>{if(document.hidden)setPlaying(false);};document.addEventListener("visibilitychange",hidden);return()=>document.removeEventListener("visibilitychange",hidden);},[]);
  useEffect(()=>{
    if(!playing||step.next===null||step.gap)return;
    const timer=setTimeout(()=>setCursor(step.next!),step.delay);
    return()=>clearTimeout(timer);
  },[playing,step.next,step.delay,step.gap]);
  const chooseTime=(value:number)=>{setPlaying(false);setCursor(value);};
  const radius=zoom==="all"?null:Number(zoom)/2;
  const range:[number,number]=radius===null?[min,max]:[Math.max(min,cursor-radius),Math.min(max,cursor+radius)];
  const pA=pointAt(streamA,cursor),pB=pointAt(streamB,cursor);
  const next=(direction:number)=>{const n=direction>0?times.find(v=>v>cursor+1e-9):times.findLast(v=>v<cursor-1e-9);if(n!==undefined)chooseTime(n);};
  const readout=(p:typeof pA)=>p?["roll","pitch","yaw"].map(key=>t("motion.axis",{axis:axisNumber(key)})+" "+formatNumber(p.values[key],language,2)+" "+(p.angle_semantics?.[key]?.unit??p.units[key])).join(" · "):t("study.noSample");
  const profileA=profileAt(streamA,cursor),profileB=profileAt(streamB,cursor);
  const trialA=pulseEvidence(streamA),trialB=pulseEvidence(streamB);
  const seriesA=streamA?.trials?.find(run=>Math.abs(run.t)<1e-8)??null,seriesB=streamB?.trials?.find(run=>Math.abs(run.t)<1e-8)??null;
  const condition=(trial:ReturnType<typeof pulseEvidence>)=>({intent:trial.event?.intent??null,outcome:trial.result?.outcome??"unknown",profile:trial.profile,observed_range:trial.peaks});
  return <div data-testid="motion-timeline">
    {renderTimelineSummary()}
    {!times.length?<Empty>{t("study.noData")}</Empty>:<>
      <div className={styles.flex}>
        {renderPlaybackButton()}
        <Button data-testid="study-prev" onClick={()=>next(-1)} disabled={cursor<=min}>{t("study.previous")}</Button>
        <Button data-testid="study-next" onClick={()=>next(1)} disabled={cursor>=max}>{t("study.next")}</Button>
        <span className={styles.small}>A ━ · B ┄</span>
      </div>
      <Field label={t("study.time")+" · "+formatNumber(cursor,language,3)+" s"}><Range data-testid="study-time" min={min} max={Math.max(min+.001,max)} step="any" value={cursor} onChange={e=>chooseTime(nearestTime(times,Number(e.target.value)))} onKeyDown={e=>{if(["ArrowLeft","ArrowDown","ArrowRight","ArrowUp","Home","End"].includes(e.key)){e.preventDefault();if(e.key==="Home")chooseTime(min);else if(e.key==="End")chooseTime(max);else next(e.key==="ArrowRight"||e.key==="ArrowUp"?1:-1);}}}/></Field>
      <Field label={t("study.window")}><Select value={zoom} onChange={e=>setZoom(e.target.value)}><option value="all">{t("study.all")}</option>{[1,5,10].map(v=><option key={v} value={v}>{v} s</option>)}</Select></Field>
      <p className={styles.small} aria-live="polite">A: {readout(pA)}{b?<><br/>B: {readout(pB)}</>:null}</p>
      {(reducedMotion||step.gap)&&<p role="status" className={styles.hint}>{t(step.gap?"attitude.gap":"attitude.reducedMotion")}</p>}
      <AttitudeComparison a={streamA} b={streamB} time={cursor} t={t} language={language}/>
      <CommandTrace stream={streamA} range={range} cursor={cursor} t={t} language={language}/>
      {streamB&&<CommandTrace series="B" stream={streamB} range={range} cursor={cursor} t={t} language={language}/>}
      {["roll","pitch","yaw"].map(channel=><MotionTrace key={channel} a={anglesA} b={anglesB} channel={channel} range={range} cursor={cursor} onTime={chooseTime} t={t} language={language}/>)}
      <details data-testid="angle-rates" className={styles.details}><summary>{t("motion.rates")}</summary><p className={styles.hint}>{t("motion.rateHint")}</p>{["roll","pitch","yaw"].map(channel=><MotionTrace key={channel} a={ratesA} b={ratesB} channel={channel} range={range} cursor={cursor} onTime={chooseTime} t={t} language={language}/>)}</details>
      <details className={styles.details}><summary>{t("study.acceleration")}</summary>{["accel_x","accel_y","accel_z"].map(channel=><MotionTrace key={channel} a={streamA} b={streamB} channel={channel} range={range} cursor={cursor} onTime={chooseTime} t={t} language={language}/>)}</details>
      <details className={styles.details}><summary>{t("study.outputs")}</summary>{["lx","az"].map(channel=><MotionTrace key={channel} a={outputStream(streamA)} b={outputStream(streamB)} channel={channel} range={range} cursor={cursor} onTime={chooseTime} t={t} language={language}/>)}</details>
      <Details t={t} value={{profiles:{A:profileA,B:profileB},events:{A:streamA?.events.filter(e=>Math.abs(e.t-cursor)<1e-9),B:streamB?.events.filter(e=>Math.abs(e.t-cursor)<1e-9)},origin_a:streamA?.origin}}/>
    </>}
    <p className={styles.hint}>{t("study.limits")}</p>
    {(!profileA||b&&!profileB)&&<p className={styles.hint}>{t("study.noProfile")}</p>}
  </div>;
}
