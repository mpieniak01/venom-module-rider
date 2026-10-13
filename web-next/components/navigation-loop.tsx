import { useEffect, useRef, useState, type ReactNode } from "react";
import { Play, Square, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { data, errorCode, formatTime, record, type JsonRecord } from "../lib/model";
import { loopBlock, loopFailure, loopPhase } from "../lib/loop-controls";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Field, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";

export function NavigationLoop({client,t,language,preview}:{client:RiderClient;t:Translator;language:string;preview:ReactNode}) {
  const [policy,setPolicy]=useState<JsonRecord|null>(null);
  const [draft,setDraft]=useState<JsonRecord|null>(null);
  const [detector,setDetector]=useState<JsonRecord>({});
  const [loopState,setLoopState]=useState<JsonRecord>({});
  const [goals,setGoals]=useState<JsonRecord>({});
  const [runtime,setRuntime]=useState<JsonRecord>({});
  const [loading,setLoading]=useState(false);
  const [operation,setOperation]=useState<"start"|"stop"|"save"|null>(null);
  const [error,setError]=useState("");
  const [readError,setReadError]=useState("");
  const [plan,setPlan]=useState("");
  const [refresh,setRefresh]=useState(0);
  const startTask=useRef<Promise<unknown>|null>(null);
  const stopping=useRef(false);
  const connected=client.canMutate;
  const session=client.status?.connection.session;
  useEffect(()=>{
    const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;let configured=false;let reading=false;
    setLoopState({stale:true});setGoals({});setRuntime({});
    if(!connected)return ()=>abort.abort();
    setLoading(true);setReadError("");
    const apply=(row:JsonRecord)=>{setLoopState(record(row.status));setDetector(record(row.detector));setGoals(record(row.goals));};
    const tick=async(initial=false)=>{
      if(reading||document.hidden||abort.signal.aborted)return;
      reading=true;
      const first=initial||!configured;
      try {
        const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/"+(first?"config":"state"),{signal:abort.signal});
        if(abort.signal.aborted)return;
        apply(row);
        if(first){setPolicy(record(row.config));setDraft(record(row.config));setRuntime(record(row.runtime));configured=true;}
        setReadError("");
      } catch(err) {
        if(abort.signal.aborted)return;
        setLoopState({stale:true});setGoals({});
        if(first){setPolicy(null);setDraft(null);}
        setReadError(t(loopFailure(err)));
      } finally {
        reading=false;
        if(!abort.signal.aborted){setLoading(false);if(!document.hidden)timer=setTimeout(()=>void tick(),1000);}
      }
    };
    const visibility=()=>{clearTimeout(timer);if(!document.hidden)void tick();};
    document.addEventListener("visibilitychange",visibility);
    void tick(true);
    return ()=>{abort.abort();clearTimeout(timer);document.removeEventListener("visibilitychange",visibility);};
  },[connected,session,refresh,t]);
  const dirty=!!policy&&JSON.stringify(policy)!==JSON.stringify(draft);
  const phase=loopPhase(connected,loopState,goals);
  const active=goals.observe===true||goals.drive===true;
  const block=loading?"loading":loopBlock(policy,runtime,detector,connected,dirty,client.status);
  const mode=policy?.mode==="drive"?"drive":"observe";
  const goalPath=mode==="drive"?"/goal/s14_venom_navigation":"/goal/s15_venom_perception";
  const readState=async()=>{
    const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");
    setLoopState(record(row.status));setGoals(record(row.goals));setDetector(record(row.detector));
  };
  const failure=(err:unknown)=>setError(t(loopFailure(err))+" · "+errorCode(err));
  const start=async()=>{
    if(block||operation||loading||active)return;
    setOperation("start");setError("");
    client.setCameraStream("venom");
    const task=client.mutate(goalPath,{enabled:true},"POST",true);
    startTask.current=task;
    try {await task;if(!stopping.current)await readState();}
    catch(err){if(!stopping.current)failure(err);}
    finally {startTask.current=null;if(!stopping.current)setOperation(null);}
  };
  const stop=async()=>{
    if(stopping.current)return;
    stopping.current=true;setOperation("stop");setError("");
    try {
      await startTask.current?.catch(()=>undefined);
      // Stop only loop goals; observing never changes manual ARM.
      const paths=goals.drive===true&&goals.observe===true?["/goal/s14_venom_navigation","/goal/s15_venom_perception"]:[goals.drive===true?"/goal/s14_venom_navigation":goals.observe===true?"/goal/s15_venom_perception":goalPath];
      for(const path of paths)await client.mutate(path,{enabled:false},"POST",true);
      await readState();
      client.setCameraStream(null);
    } catch(err){failure(err);}
    finally{stopping.current=false;setOperation(null);}
  };
  const save=async()=>{
    if(!draft)return;
    setOperation("save");setError("");
    try{
      const {revision,protocol_version:ignored,...values}=draft;void ignored;
      const result=await client.mutate("/navigation/config",{revision,values},"POST",true);
      const saved=record(data(result).config);setPolicy(saved);setDraft(saved);
    }catch(err){failure(err);}finally{setOperation(null);}
  };
  const decision=record(loopState.decision);
  const reasonKey="vision.reason."+String(decision.reason);
  const reasonLabel=t(reasonKey)===reasonKey?t("vision.reason.default"):t(reasonKey);
  return <Panel title={t("loopTitle")} extra={<Button aria-label={t("loopLoad")} disabled={loading||!!operation||!connected} onClick={()=>setRefresh(v=>v+1)}><RefreshCw size={14}/>{t("refresh")}</Button>}>
    <div className={styles.loopWorkspace}><div>
    <div className={styles.loopStatus} role="status" data-testid="loop-state">
      <Pill tone={phase==="running"?"good":phase==="offline"||phase==="unknown"?"warning":undefined}>{t(operation==="stop"?"loopStopping":operation==="start"?"loopStarting":"loopPhase."+phase)}</Pill>
      <strong>{t(mode==="drive"?"loopDrive":"loopObserve")}</strong>
    </div>
    <div className={styles.loopActions}>
      <Button tone="primary" data-testid="loop-start" disabled={!!block||!!operation||!!client.busy||loading||active||phase==="unknown"} onClick={()=>void start()}><Play size={18}/>{t(operation==="start"?"loopStarting":mode==="drive"?"loopStartDrive":"loopStartObserve")}</Button>
      <Button tone="danger" data-testid="loop-stop" disabled={!connected||operation==="stop"||operation==="save"} onClick={()=>void stop()}><Square size={18}/>{t(operation==="stop"?"loopStopping":"loopStop")}</Button>
    </div>
    {(block||phase==="unknown")&&<p className={styles.hint} data-testid="loop-block">{t(block||"loopStateUnknown")}</p>}
    <p className={styles.hint}>{t(mode==="observe"?"loopObservationHint":"loopRunHint")}</p>
    {!!decision.state&&<p className={styles.hint}>{t("loopDecision")}: <strong>{t("vision."+String(decision.state))}</strong> · {formatTime(loopState.observed_at,language)}<br/>{reasonLabel}</p>}
    {mode==="drive"&&<div className={styles.stack}><Field label={t("loopPlan")}><input className={styles.input} maxLength={512} value={plan} onChange={e=>setPlan(e.target.value)}/></Field><Button disabled={runtime.drive!==true||!client.canArm||!!operation||dirty||!plan.trim()||policy?.verified!==true||policy?.model_sha256!==detector.sha256} onClick={()=>void client.command({action:"arm",owner:"navigation",arming_mode:"timed",lease_s:10,operator_consent:true,operator_plan:plan})}>{t("loopArm")}</Button></div>}
    {(error||readError)&&<p className={styles.loopError} role="alert">{error||readError}</p>}
    </div><div>{preview}</div></div>
    <details className={styles.details}><summary>{t("loopSettings")}{dirty?" · "+t("unsaved"):""}</summary>
      <div className={styles.metrics}>
      <div className={styles.metric}><div className={styles.metricLabel}>{t("loopDecision")}</div><strong>{decision.state?t("vision."+String(decision.state)):"—"}</strong><p className={styles.small}>{String(decision.reason??loopState.reason??loopState.stop_reason??"")}</p></div>
      <div className={styles.metric}><div className={styles.metricLabel}>{t("loopLastFrame")}</div><strong>{formatTime(loopState.observed_at,language)}</strong><p className={styles.small}>{String(decision.inference_ms??"—")} ms · {t("loopLoaded")}: {t(detector.loaded===true?"yes":"no")}</p></div>
    </div>
      <p className={styles.hint}>{t("loopHint")}</p>
      {draft?<div className={styles.stack}>
        <Field label={t("loopMode")}><select className={styles.select} disabled={active||!!operation} value={String(draft.mode)} onChange={e=>setDraft({...draft,mode:e.target.value})}><option value="observe">{t("loopObserve")}</option><option value="drive">{t("loopDrive")}</option></select></Field>
        <div className={styles.grid}>
          <Field label={t("loopSpeed")}><input className={styles.input} disabled={active||!!operation} type="number" min="0.01" max="0.15" step="0.01" value={Number(draft.speed)} onChange={e=>setDraft({...draft,speed:Number(e.target.value)})}/></Field>
          <Field label={t("loopBudget")}><input className={styles.input} disabled={active||!!operation} type="number" min="100" max="500" step="10" value={Number(draft.decision_budget_ms)} onChange={e=>setDraft({...draft,decision_budget_ms:Number(e.target.value)})}/></Field>
          <Field label={t("loopPulse")}><input className={styles.input} disabled={active||!!operation} type="number" min="50" max="200" step="10" value={Number(draft.max_motion_ms)} onChange={e=>setDraft({...draft,max_motion_ms:Number(e.target.value)})}/></Field>
          <Field label={t("loopCorridor")}><input className={styles.input} disabled={active||!!operation} value={Array.isArray(draft.corridor)?draft.corridor.join(","):""} onChange={e=>setDraft({...draft,corridor:e.target.value.split(",").map(Number)})}/></Field>
        </div>
        <Field label={t("loopCalibration")}><input className={styles.input} disabled={active||!!operation} maxLength={64} value={String(draft.calibration_id??"")} onChange={e=>setDraft({...draft,calibration_id:e.target.value})}/></Field>
        <Field label={t("loopHash")}><input className={styles.input} disabled={active||!!operation} maxLength={64} value={String(draft.model_sha256??"")} onChange={e=>setDraft({...draft,model_sha256:e.target.value})}/></Field>
        <label className={styles.check}><input disabled={active||!!operation} type="checkbox" checked={draft.verified===true} onChange={e=>setDraft({...draft,verified:e.target.checked})}/>{t("loopVerified")}</label>
        <div className={styles.flex}><Button disabled={!connected||!!operation||active||!dirty} onClick={()=>void save()}>{t("loopSave")}</Button><Button disabled={!dirty||!!operation} onClick={()=>setDraft(policy)}>{t("loopDiscard")}</Button></div>
        <p className={styles.hint}>{t("loopModel")}: {t(detector.configured===true?"yes":"no")} · {t("computeRevision")}: {String(policy?.revision??"—")}</p><code className={styles.small}>{String(detector.sha256??"")}</code>
      </div>:<p>{t(loading?"loading":"loopLoadFailed")}</p>}
    </details>
  </Panel>;
}
