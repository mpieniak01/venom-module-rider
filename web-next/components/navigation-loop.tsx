import { useEffect, useRef, useState, type ReactNode } from "react";
import { Play, Square, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { data, errorCode, formatTime, record, type JsonRecord } from "../lib/model";
import { calibrationAcceptance, calibrationMatches, calibrationRecorded, calibrationScope, decisionView, freshAnalysisFrame, loopBlock, loopFailure, loopPhase, loopSettingsValues, trialBlock } from "../lib/loop-controls";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { LoopDecision } from "./loop-decision";
import { CameraPanel } from "./control";
import { LoopCalibration } from "./loop-calibration";
import { Button, Field, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";

export function NavigationLoop({client,t,language,manual,onManualSettings}:{client:RiderClient;t:Translator;language:string;manual:ReactNode;onManualSettings:()=>void}) {
  const [policy,setPolicy]=useState<JsonRecord|null>(null);
  const [draft,setDraft]=useState<JsonRecord|null>(null);
  const [detector,setDetector]=useState<JsonRecord>({});
  const [loopState,setLoopState]=useState<JsonRecord>({});
  const [goals,setGoals]=useState<JsonRecord>({});
  const [runtime,setRuntime]=useState<JsonRecord>({});
  const [loading,setLoading]=useState(false);
  const [operation,setOperation]=useState<"check"|"start"|"stop"|"save"|null>(null);
  const [error,setError]=useState("");
  const [readError,setReadError]=useState("");
  const [trialResult,setTrialResult]=useState("");
  const [refresh,setRefresh]=useState(0);
  const startTask=useRef<Promise<unknown>|null>(null);
  const stopping=useRef(false);
  const settingsRef=useRef<HTMLDetailsElement>(null);
  const workspaceRef=useRef<HTMLDivElement>(null);
  const displayedRun=useRef("");
  const mounted=useRef(true);
  const trialArmed=useRef(false);
  const operationEpoch=useRef(0);
  const operationGeneration=useRef("");
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
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
  const {cameraStream,setCameraStream}=client;
  useEffect(()=>{
    if(!active||loopState.running!==true)return;
    const run=String(loopState.run_id??(goals.drive===true?"drive":"observe"));
    if(displayedRun.current===run)return;
    displayedRun.current=run;
    if(!cameraStream)setCameraStream("venom");
  },[active,loopState.running,loopState.run_id,goals.drive,cameraStream,setCameraStream]);

  const readState=async()=>{
    const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");
    setLoopState(record(row.status));setGoals(record(row.goals));setDetector(record(row.detector));
  };
  const failure=(err:unknown)=>setError(t(loopFailure(err))+" · "+errorCode(err));
  const stop=async()=>{
    if(stopping.current)return;
    stopping.current=true;setOperation("stop");setError("");
    try {
      const stoppedMotion=goals.drive===true||trialArmed.current;
      if(stoppedMotion)await client.command({action:"stop"},true);
      await startTask.current?.catch(()=>undefined);
      const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");
      const actualGoals=record(row.goals);
      if(actualGoals.drive===true&&!stoppedMotion)await client.command({action:"stop"},true);
      // Stopping analysis alone preserves manual ARM.
      for(const [key,path] of [["drive","s14_venom_navigation"],["observe","s15_venom_perception"]] as const){
        if(actualGoals[key]===true)await client.mutate("/goal/"+path,{enabled:false},"POST",true);
      }
      await readState();
      setTrialResult("trialStopped");
    } catch(err){failure(err);}
    finally{stopping.current=false;trialArmed.current=false;setOperation(null);}
  };
  const persist=async(values:JsonRecord)=>{
    if(!policy)throw new Error("loop_configuration_unavailable");
    const result=await client.mutate("/navigation/config",{revision:policy.revision,values},"POST",true);
    const saved=record(data(result).config);setPolicy(saved);setDraft(saved);return saved;
  };
  const save=async()=>{
    if(!draft||!policy||active)return;
    setOperation("save");setError("");
    try{await persist(loopSettingsValues(policy,draft));}
    catch(err){failure(err);}finally{setOperation(null);}
  };
  const applyState=(row:JsonRecord)=>{setLoopState(record(row.status));setGoals(record(row.goals));setDetector(record(row.detector));};
  const ensureOperation=(origin:string)=>{
    if(!mounted.current||stopping.current||client.currentSession()!==origin||client.currentMotionEpoch()!==operationEpoch.current||client.currentGeneration()!==operationGeneration.current||document.hidden)throw Object.assign(new Error("navigation_trial_cancelled"),{data:{error:"navigation_trial_cancelled"}});
  };
  const stopGoals=async(origin:string)=>{
    const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");
    ensureOperation(origin);
    for(const [key,path] of [["drive","s14_venom_navigation"],["observe","s15_venom_perception"]] as const){
      if(record(row.goals)[key]===true){ensureOperation(origin);await client.mutate("/goal/"+path,{enabled:false},"POST",true);}
    }
    await readState();
  };
  const ensureAnalysis=async(origin:string)=>{
    const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");ensureOperation(origin);
    if(record(row.goals).observe===true&&record(row.status).running===true&&record(row.status).stale!==true){
      client.setCameraStream("venom");applyState(row);return;
    }
    await stopGoals(origin);ensureOperation(origin);
    ensureOperation(origin);client.setCameraStream("venom");
    await client.mutate("/goal/s15_venom_perception",{enabled:true},"POST",true);await readState();
  };
  const observePreview=async()=>{
    if(!policy||dirty||operation||loading||observeBlock)return;
    workspaceRef.current?.scrollIntoView({behavior:"smooth",block:"start"});
    setOperation("start");setError("");setTrialResult("");
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    const task=ensureAnalysis(origin);startTask.current=task;
    try{await task;}
    catch(err){if(!stopping.current)failure(err);}
    finally{startTask.current=null;if(!stopping.current)setOperation(null);}
  };
  const runTrial=async()=>{
    if(!policy||trialUnavailable||operation||loading||phase==="running"&&goals.drive===true)return;
    workspaceRef.current?.scrollIntoView({behavior:"smooth",block:"start"});
    setOperation("check");setError("");setTrialResult("trialChecking");trialArmed.current=false;
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    const task=(async()=>{
      await ensureAnalysis(origin);ensureOperation(origin);
      const deadline=Date.now()+5000;
      let row:JsonRecord={}, ready=false;
      while(Date.now()<deadline){
        ensureOperation(origin);row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");ensureOperation(origin);applyState(row);
        const state=record(row.status),view=decisionView(state,record(row.detector)),camera=client.currentCamera();
        if(view.current&&record(state.decision).state!=="clear"){setTrialResult("trialImageStop");return;}
        if(view.vision==="go"&&freshAnalysisFrame(camera.frame,camera.stream)){ready=true;break;}
        await new Promise(resolve=>setTimeout(resolve,150));
      }
      if(!ready){setTrialResult("trialNoFreshDecision");return;}
      // Refetch the Pi-owned policy; concurrent edits must not inherit this trial.
      const loaded=await apiFetch<JsonRecord>("/api/v1/rider/navigation/config");ensureOperation(origin);
      const selected=record(loaded.config);
      if(!calibrationMatches(selected,record(loaded.detector))||selected.revision!==record(record(row.status).decision).config_revision||calibrationScope(selected,record(loaded.detector))!==calibrationScope(policy,record(loaded.detector)))throw Object.assign(new Error("navigation_configuration_changed"),{data:{error:"navigation_configuration_changed"}});
      setOperation("start");setTrialResult("trialStarting");
      await stopGoals(origin);ensureOperation(origin);
      const values=loopSettingsValues(selected,{...selected,mode:"drive"});
      const result=selected.mode==="drive"?{config:selected}:await client.mutate("/navigation/config",{revision:selected.revision,values},"POST",true);ensureOperation(origin);
      const saved=record(data(result).config);setPolicy(saved);setDraft(saved);
      const plan=t("trialPlan")+" · "+String(saved.speed)+" SDK · ≤ "+String(saved.max_motion_ms)+" ms";
      trialArmed.current=true;const generation=await client.armNavigation(plan,origin,operationGeneration.current,operationEpoch.current);operationGeneration.current=String(generation);ensureOperation(origin);
      await client.mutate("/goal/s14_venom_navigation",{enabled:true},"POST",true);ensureOperation(origin);await readState();
      setTrialResult("trialStarted");
    })();
    startTask.current=task;
    try{await task;}
    catch(err){if(!stopping.current){failure(err);setTrialResult("trialFailed");}}
    finally{startTask.current=null;if(!stopping.current)setOperation(null);}
  };
  const acceptCalibration=async()=>{
    if(!policy||dirty||operation||goals.drive===true)return;
    const values=calibrationAcceptance(policy,detector,"operator-"+crypto.randomUUID());
    setOperation("save");setError("");
    try{
      // Explicit acceptance ends observation before saving; it never arms or drives.
      if(goals.observe===true)await client.mutate("/goal/s15_venom_perception",{enabled:false},"POST",true);
      await persist(values);await readState();
    }catch(err){failure(err);}finally{setOperation(null);}
  };
  const revokeCalibration=async()=>{
    if(!policy||active||operation||dirty)return;
    setOperation("save");setError("");
    try{await persist({...loopSettingsValues(policy,policy),verified:false,calibration_id:"",model_sha256:""});}
    catch(err){failure(err);}finally{setOperation(null);}
  };
  const openSettings=()=>{if(settingsRef.current){settingsRef.current.open=true;settingsRef.current.scrollIntoView({behavior:"smooth",block:"center"});}};
  const observeBlock=policy?loopBlock({...policy,mode:"observe"},runtime,detector,connected,dirty,client.status):"loopLoadFailed";
  const calibrated=calibrationRecorded(policy);
  const trialUnavailable=trialBlock(policy,runtime,detector,connected,dirty,client.status);
  const decision=record(loopState.decision);
  return <Panel title={t("loopTitle")} extra={<Button aria-label={t("loopLoad")} disabled={loading||!!operation||!connected} onClick={()=>setRefresh(v=>v+1)}><RefreshCw size={14}/>{t("refresh")}</Button>}>
    <div className={styles.loopStatus} role="status" data-testid="loop-state">
      <Pill tone={phase==="running"?"good":phase==="offline"||phase==="unknown"||phase==="fault"?"warning":undefined}>{t(operation==="stop"?"loopStopping":operation==="check"?"trialChecking":operation==="start"?"loopStarting":"loopPhase."+phase)}</Pill>
      <strong>{t(goals.drive===true?"trialActiveMode":"trialAnalysisMode")}</strong>
      <Pill tone={observeBlock?"warning":"good"}>{t(observeBlock?"loopObservationBlocked":"loopObservationAvailable")}</Pill>
      <Pill tone={trialUnavailable?"warning":"good"}>{t(trialUnavailable?"trialPreparationBlocked":"trialPreparationReady")}</Pill>
    </div>
    <div className={styles.loopToolbar}>
    <div className={styles.loopActions}>
      <Button data-testid="loop-preview" disabled={!connected||!!operation||loading||!!observeBlock} onClick={()=>void observePreview()}>{t("trialAnalyse")}</Button>
      <Button tone="primary" data-testid="loop-start" disabled={!!trialUnavailable||!!operation||!!client.busy||loading||phase==="running"&&goals.drive===true} onClick={()=>void runTrial()}><Play size={18}/>{t("trialRun")}</Button>
      <Button tone="danger" data-testid="loop-stop" disabled={!connected||operation==="stop"||operation==="save"} onClick={()=>void stop()}><Square size={18}/>{t(operation==="stop"?"loopStopping":"loopStop")}</Button>
    </div>
    </div>
    {!!trialUnavailable&&<p className={styles.hint} data-testid="loop-block">{t(trialUnavailable)}</p>}
    {!!trialResult&&<p className={styles.hint} role="status" data-testid="trial-result">{t(phase==="fault"?"trialEnded":trialResult==="trialStarted"&&phase==="stopped"?"trialStopped":trialResult)}</p>}
    <p className={styles.hint}>{t("trialPlan")} · {String(policy?.speed??"—")} SDK · ≤ {String(policy?.max_motion_ms??"—")} ms</p>
    {phase==="fault"&&<p role="alert" className={styles.loopError}>{t("loopFaultHint")} · {String(loopState.stop_reason??"")}</p>}

    {(error||readError)&&<p className={styles.loopError} role="alert">{error||readError}</p>}
    <div ref={workspaceRef} className={styles.driveWorkspace}>
      <div className={styles.loopCamera}><CameraPanel client={client} t={t} language={language} compact onObservation={()=>void observePreview()} observationDisabled={!connected||!!operation||loading||(!active&&!!observeBlock)}/></div>
      <div className={styles.loopSidebar}>
        {manual}
        <LoopDecision state={loopState} detector={detector} policy={policy} status={client.status} t={t} language={language}/>
        <details className={styles.details} open={!calibrated&&!!policy}><summary>{t(calibrated?"trialCalibrationSaved":"trialCalibrationNeeded")}</summary>
          <LoopCalibration client={client} t={t} policy={policy} detector={detector} dirty={dirty} busy={!!operation||!!client.busy} driving={goals.drive===true} active={active} onAccept={acceptCalibration} onRevoke={revokeCalibration} onSettings={openSettings} onManualSettings={onManualSettings}/>
        </details>
      </div>
    </div>
    <details ref={settingsRef} className={styles.details}><summary>{t("loopSettings")}{dirty?" · "+t("unsaved"):""}</summary>
      <div className={styles.metrics}>
      <div className={styles.metric}><div className={styles.metricLabel}>{t("loopDecision")}</div><strong>{decision.state?t("vision."+String(decision.state)):"—"}</strong><p className={styles.small}>{String(decision.reason??loopState.reason??loopState.stop_reason??"")}</p></div>
      <div className={styles.metric}><div className={styles.metricLabel}>{t("loopLastFrame")}</div><strong>{formatTime(loopState.observed_at,language)}</strong><p className={styles.small}>{String(decision.inference_ms??"—")} ms · {t("loopLoaded")}: {t(detector.loaded===true?"yes":"no")}</p></div>
    </div>
      <p className={styles.hint}>{t("loopHint")}</p>
      {draft?<div className={styles.stack}>

        <div className={styles.grid}>
          <Field label={t("loopSpeed")}><input className={styles.input} disabled={active||!!operation} type="number" min="0.01" max="0.15" step="0.01" value={Number(draft.speed)} onChange={e=>setDraft({...draft,speed:Number(e.target.value)})}/></Field>
          <Field label={t("loopBudget")}><input className={styles.input} disabled={active||!!operation} type="number" min="100" max="500" step="10" value={Number(draft.decision_budget_ms)} onChange={e=>setDraft({...draft,decision_budget_ms:Number(e.target.value)})}/></Field>
          <Field label={t("loopPulse")}><input className={styles.input} disabled={active||!!operation} type="number" min="50" max="200" step="10" value={Number(draft.max_motion_ms)} onChange={e=>setDraft({...draft,max_motion_ms:Number(e.target.value)})}/></Field>
          <Field label={t("loopCorridor")}><input className={styles.input} disabled={active||!!operation} value={Array.isArray(draft.corridor)?draft.corridor.join(","):""} onChange={e=>setDraft({...draft,corridor:e.target.value.split(",").map(Number)})}/></Field>
        </div>
        <p className={styles.hint}>{t("loopCalibrationSettingsHelp")}</p>
        <Field label={t("loopCalibration")}><input className={styles.input} readOnly value={String(draft.calibration_id??"")}/></Field>
        <Field label={t("loopHash")}><input className={styles.input} readOnly value={String(draft.model_sha256??"")}/></Field>
        <div className={styles.loopModeRow}><Button disabled={!connected||!!operation||active||!dirty} onClick={()=>void save()}>{t("loopSave")}</Button>{dirty&&<Button disabled={!!operation} onClick={()=>setDraft(policy)}>{t("loopDiscard")}</Button>}</div>

        <p className={styles.hint}>{t("loopModel")}: {t(detector.configured===true?"yes":"no")} · {t("computeRevision")}: {String(policy?.revision??"—")}</p><code className={styles.small}>{String(detector.sha256??"")}</code>
      </div>:<p>{t(loading?"loading":"loopLoadFailed")}</p>}
    </details>
  </Panel>;
}
