import {useEffect,useState} from "react";
import {apiFetch} from "@/lib/api-client";
import {getApiBaseUrl} from "@/lib/env";
import {errorCode,formatNumber,formatTime,record} from "../lib/model";
import {readTimeline,pointAt,profileAt,streamTimes,trialRequests,alignTrial,pulseEvidence,type MotionStream,type MotionTimeline} from "../lib/motion-timeline";
import {SAMPLES_BASE,type Collection} from "../lib/samples";
import {useSamples} from "../lib/use-samples";
import type {RiderClient} from "../lib/use-rider";
import type {Translator} from "../lib/i18n";
import {Button,Details,Empty,Field,Input,Panel,Range,Select} from "./primitives";
import {CommandTrace,MotionTrace,outputStream} from "./motion-trace";
import styles from "../rider.module.css";

function useTimeline(id:string,refresh:number){
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

export function MotionStudy({client,t,language}:{client:RiderClient;t:Translator;language:string}){
  const [surface,setSurface]=useState("");
  const capture=useSamples(client.config?.device_id??null,true,"motion-study-v1:"+surface.trim());
  const [sessions,setSessions]=useState<Collection[]>([]);
  const [selectedA,setA]=useState<string|null>(null),[bId,setB]=useState("");
  const [aStream,setAStream]=useState(""),[bStream,setBStream]=useState("");
  const [aTrial,setATrial]=useState(""),[bTrial,setBTrial]=useState("");
  const [revision,refresh]=useState(0);
  const [error,setError]=useState("");
  const active=capture.session?.state==="collecting";
  const currentId=capture.session?.session_id;
  const aId=selectedA??currentId??"";
  useEffect(()=>{
    const controller=new AbortController();
    void apiFetch<{sessions:Collection[]}>(SAMPLES_BASE+"/sessions",{signal:controller.signal}).then(r=>{if(!controller.signal.aborted){setSessions(r.sessions);setError("");}}).catch(err=>{if(!controller.signal.aborted)setError(errorCode(err));});
    return()=>controller.abort();
  },[revision,currentId,active]);
  const a=useTimeline(aId,revision),b=useTimeline(bId,revision);
  const sourceA=a.value?.streams.find(s=>s.id===aStream)??a.value?.streams[0]??null;
  const sourceB=b.value?.streams.find(s=>s.id===bStream)??b.value?.streams[0]??null;
  const streamA=alignTrial(sourceA,aTrial),streamB=alignTrial(sourceB,bTrial);
  return <Panel title={t("study.title")} hint={t("study.hint")}>
    <div data-testid="motion-study">
      <Field label={t("study.surface")}><Input value={surface} maxLength={80} disabled={active||capture.busy} onChange={e=>setSurface(e.target.value)}/></Field>
      <div className={styles.flex} style={{marginTop:12}}>
        <Button data-testid="study-record" disabled={!client.canMutate||capture.busy||active} onClick={()=>{setA(null);void capture.start("manual");}}>{t("study.record")}</Button>
        <Button data-testid="study-end" disabled={!active||capture.busy} onClick={()=>void capture.end().then(()=>refresh(r=>r+1))}>{t("study.end")}</Button>
        <Button data-testid="study-refresh" onClick={()=>refresh(r=>r+1)}>{t("refresh")}</Button>
      </div>
      <p role="status" className={styles.hint}>{t(active?"study.recording":"study.passive")}</p>
      {record(capture.session?.evidence).retired===true&&<p role="alert">{t("study.retired")}</p>}
      {(error||capture.error||a.error||b.error)&&<p role="alert">{t("requestFailed")} · {error||capture.error||a.error||b.error}</p>}
      <div className={styles.grid}>{(["A","B"] as const).map(name=><Field key={name} label={t("study.session")+" "+name}>
        <Select data-testid={"study-session-"+name} value={name==="A"?aId:bId} onChange={e=>{if(name==="A"){setA(e.target.value);setAStream("");}else{setB(e.target.value);setBStream("");}}}>
          <option value="">{t("samples.choose")}</option>
          {sessions.map(s=><option key={s.session_id} value={s.session_id}>{formatTime(s.started_at,language)+" · "+s.session_id.slice(0,8)+" · "+s.state}</option>)}
        </Select>
      </Field>)}</div>
      {([[a.value,aStream,setAStream],[b.value,bStream,setBStream]] as const).map(([value,id,set],index)=>value&&value.streams.length>1&&<Field key={index} label={t("study.stream")+" "+(index?"B":"A")}><Select value={id||value.streams[0].id} onChange={e=>set(e.target.value)}>{value.streams.map(s=><option key={s.id} value={s.id}>{s.device_id+" · "+s.source_session.slice(0,8)}</option>)}</Select></Field>)}
      {([["A",sourceA,aTrial,setATrial],["B",sourceB,bTrial,setBTrial]] as const).map(([label,source,id,set])=>trialRequests(source).length>0&&<Field key={label} label={t("study.trial")+" "+label}><Select data-testid={"study-trial-"+label} value={trialRequests(source).some(e=>e.operation_id===id)?id:""} onChange={e=>set(e.target.value)}><option value="">{t("study.firstTrial")}</option>{trialRequests(source).map(e=><option key={e.operation_id} value={e.operation_id}>{formatNumber(e.t,language,3)+" s · "+String(e.intent.direction??"")+" · "+String(e.intent.mode??"")+" · "+String(e.intent.request_id??e.operation_id)}</option>)}</Select></Field>)}
      {a.value&&<MotionTimelineView key={a.value.dataset_revision+":"+b.value?.dataset_revision+":"+streamA?.id+":"+streamB?.id+":"+aTrial+":"+bTrial} a={a.value} b={b.value} streamA={streamA} streamB={streamB} t={t} language={language}/>}
      {aId&&<a className={styles.button} href={getApiBaseUrl()+SAMPLES_BASE+"/sessions/"+aId+"/export"} download>{t("samples.export")} A</a>}
    </div>
  </Panel>;
}
export function MotionTimelineView({a,b,streamA,streamB,t,language,orientation}:{a:MotionTimeline;b:MotionTimeline|null;streamA:MotionStream|null;streamB:MotionStream|null;t:Translator;language:string;orientation?:(a:MotionStream|null,b:MotionStream|null,time:number)=>React.ReactNode}){
  const times=streamTimes(streamA,streamB);
  const [cursor,setCursor]=useState(times[0]??0),[zoom,setZoom]=useState("all");
  const min=times[0]??0,max=times.at(-1)??0;
  const radius=zoom==="all"?null:Number(zoom)/2;
  const range:[number,number]=radius===null?[min,max]:[Math.max(min,cursor-radius),Math.min(max,cursor+radius)];
  const pA=pointAt(streamA,cursor),pB=pointAt(streamB,cursor);
  const next=(direction:number)=>{const n=direction>0?times.find(v=>v>cursor+.0006):times.findLast(v=>v<cursor-.0006);if(n!==undefined)setCursor(n);};
  const readout=(p:typeof pA)=>p?["roll","pitch","yaw"].map(key=>key+" "+formatNumber(p.values[key],language,2)+" "+p.units[key]).join(" · "):t("study.noSample");
  const profileA=profileAt(streamA,cursor),profileB=profileAt(streamB,cursor);
  const trialA=pulseEvidence(streamA),trialB=pulseEvidence(streamB);
  const condition=(trial:ReturnType<typeof pulseEvidence>)=>({intent:trial.event?.intent??null,outcome:trial.result?.outcome??"unknown",profile:trial.profile,observed_range:trial.peaks});
  return <div data-testid="motion-timeline">
    <p className={styles.hint}>{t("study.reception")} · {t("study.revision")} A {a.dataset_revision.slice(0,8)}{b?" · B "+b.dataset_revision.slice(0,8):""}</p>
    <p>{t("study.context")} A: {a.context?.replace("motion-study-v1:","")||t("unknown")}{b?" · B: "+(b.context?.replace("motion-study-v1:","")||t("unknown")):""}</p>
    <p className={styles.hint}>{t("study.quality",{samples:Number(a.counts.imu_records??0),drops:Number(a.collection_status.dropped??0),failed:Number(a.collection_status.failed??0),gap:formatNumber(a.source_gaps_s.p95,language,3)})}</p>
    {b&&(!a.context?.replace("motion-study-v1:","")||a.context!==b.context)&&<p role="status">{t("study.contextDifferent")}</p>}
    {b&&trialA.event?.intent.direction!==trialB.event?.intent.direction&&<p role="status">{t("study.directionDifferent")}</p>}
    <p className={styles.hint}>{t(trialA.peaks&&(!b||trialB.peaks)?"study.observedRange":"study.insufficient")}</p>
    <Details t={t} value={{A:condition(trialA),...(b?{B:condition(trialB)}:{})}}/>
    {!times.length?<Empty>{t("study.noData")}</Empty>:<>
      <div className={styles.flex}>
        <Button data-testid="study-prev" onClick={()=>next(-1)} disabled={cursor<=min}>{t("study.previous")}</Button>
        <Button data-testid="study-next" onClick={()=>next(1)} disabled={cursor>=max}>{t("study.next")}</Button>
        <span className={styles.small}>A ━ · B ┄</span>
      </div>
      <Field label={t("study.time")+" · "+formatNumber(cursor,language,3)+" s"}><Range data-testid="study-time" min={min} max={Math.max(min+.001,max)} step={.001} value={cursor} onChange={e=>setCursor(Number(e.target.value))}/></Field>
      <Field label={t("study.window")}><Select value={zoom} onChange={e=>setZoom(e.target.value)}><option value="all">{t("study.all")}</option>{[1,5,10].map(v=><option key={v} value={v}>{v} s</option>)}</Select></Field>
      <p className={styles.small} aria-live="polite">A: {readout(pA)}{b?<><br/>B: {readout(pB)}</>:null}</p>
      {orientation?.(streamA,streamB,cursor)}
      <CommandTrace stream={streamA} range={range} cursor={cursor} t={t} language={language}/>
      {streamB&&<CommandTrace stream={streamB} range={range} cursor={cursor} t={t} language={language}/>}
      {["roll","pitch","yaw"].map(channel=><MotionTrace key={channel} a={streamA} b={streamB} channel={channel} range={range} cursor={cursor} onTime={setCursor} t={t} language={language}/>)}
      <details className={styles.details}><summary>{t("study.acceleration")}</summary>{["accel_x","accel_y","accel_z"].map(channel=><MotionTrace key={channel} a={streamA} b={streamB} channel={channel} range={range} cursor={cursor} onTime={setCursor} t={t} language={language}/>)}</details>
      <details className={styles.details}><summary>{t("study.outputs")}</summary>{["lx","az"].map(channel=><MotionTrace key={channel} a={outputStream(streamA)} b={outputStream(streamB)} channel={channel} range={range} cursor={cursor} onTime={setCursor} t={t} language={language}/>)}</details>
      <Details t={t} value={{profiles:{A:profileA,B:profileB},events:{A:streamA?.events.filter(e=>Math.abs(e.t-cursor)<.0006),B:streamB?.events.filter(e=>Math.abs(e.t-cursor)<.0006)},origin_a:streamA?.origin}}/>
    </>}
    <p className={styles.hint}>{t("study.limits")}</p>
    {(!profileA||b&&!profileB)&&<p className={styles.hint}>{t("study.noProfile")}</p>}
  </div>;
}
