import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Play, Square, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { data, errorCode, formatNumber, formatTime, record, type JsonRecord } from "../lib/model";
import { calibrationAcceptance, calibrationMatches, completedTrial, calibrationScope, decisionView, executionHeadline, freshPolicyFrame, loopBlock, loopFailure, loopPhase, loopSettingsValues, motionPermission, stoppingZone, stoppingZoneValues, trialBlock, trialPreparation, trialPreparationTitle, trialPreparationErrorKey } from "../lib/loop-controls";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { LoopDecision } from "./loop-decision";
import { CameraPanel } from "./control";
import { Button, Field, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";

export function NavigationLoop({client,t,language,manual}:{client:RiderClient;t:Translator;language:string;manual:ReactNode}) {
  const [policy,setPolicy]=useState<JsonRecord|null>(null);
  const [draft,setDraft]=useState<JsonRecord|null>(null);
  const dirty=!!policy&&JSON.stringify(policy)!==JSON.stringify(draft);
  const policyRef=useRef(policy),dirtyRef=useRef(dirty);
  policyRef.current=policy;dirtyRef.current=dirty;
  const applyPolicy=useCallback((value:JsonRecord|null)=>{
    policyRef.current=value;setPolicy(value);setDraft(value);
  },[]);
  const [detector,setDetector]=useState<JsonRecord>({});
  const [loopState,setLoopState]=useState<JsonRecord>({});
  const [goals,setGoals]=useState<JsonRecord>({});
  const [runtime,setRuntime]=useState<JsonRecord>({});
  const [loading,setLoading]=useState(false);
  const [operation,setOperation]=useState<"check"|"start"|"stop"|"save"|"zone"|null>(null);
  const [error,setError]=useState("");
  const [errorRun,setErrorRun]=useState("");
  const [trialFailure,setTrialFailure]=useState<unknown>(null);
  const [readError,setReadError]=useState("");
  const [trialResult,setTrialResult]=useState("");
  const [trialStopReason,setTrialStopReason]=useState("");
  const trialSession=useRef("");
  const [refresh,setRefresh]=useState(0);
  const startTask=useRef<Promise<unknown>|null>(null);
  const stopping=useRef(false);
  const settingsRef=useRef<HTMLDetailsElement>(null);
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
    setLoopState({stale:true});setGoals({});setRuntime({});setTrialFailure(null);
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
        const incoming=record(row.config);
        if(first||!dirtyRef.current&&typeof incoming.revision==="number"&&incoming.revision>Number(policyRef.current?.revision??-1))applyPolicy(incoming);
        if(first){setRuntime(record(row.runtime));configured=true;}
        setReadError("");
      } catch(err) {
        if(abort.signal.aborted)return;
        setLoopState({stale:true});setGoals({});
        if(first)applyPolicy(null);
        setReadError(t(loopFailure(err)));
      } finally {
        reading=false;
        if(!abort.signal.aborted){setLoading(false);if(!document.hidden)timer=setTimeout(()=>void tick(),500);}
      }
    };
    const visibility=()=>{clearTimeout(timer);if(!document.hidden)void tick();};
    document.addEventListener("visibilitychange",visibility);
    void tick(true);
    return ()=>{abort.abort();clearTimeout(timer);document.removeEventListener("visibilitychange",visibility);};
  },[connected,session,refresh,t,applyPolicy]);
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
  const failure=useCallback((err:unknown)=>{
    setTrialFailure(err);
    const diagnostic=record(record(record(record(err).data).detail).details).navigation;
    const row=record(diagnostic);setErrorRun(String(row.run_id??""));const cause=String(record(row.motion_failure).reason??row.stop_reason??"");
    const key="vision.reason."+cause, translated=t(key);
    setError(t(loopFailure(err))+" · "+errorCode(err)+(cause?" · "+(translated===key?cause:translated):""));
  },[t]);
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
    const saved=record(data(result).config);applyPolicy(saved);return saved;
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
    setOperation("start");setError("");setTrialResult("");
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    const task=ensureAnalysis(origin);startTask.current=task;
    try{await task;}
    catch(err){if(!stopping.current)failure(err);}
    finally{startTask.current=null;if(!stopping.current)setOperation(null);}
  };
  const changeStoppingZone=async(zone:"near"|"far")=>{
    if(!policy||dirty||operation||loading||!connected||client.busy||goals.drive===true)return;
    const values=stoppingZoneValues(policy,zone);if(!values)return;
    setOperation("zone");setError("");setTrialResult("");setTrialStopReason("");
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    const task=(async()=>{
      const loaded=await apiFetch<JsonRecord>("/api/v1/rider/navigation/config");ensureOperation(origin);
      if(record(loaded.config).revision!==policy.revision||record(loaded.goals).drive===true)throw Object.assign(new Error("navigation_configuration_changed"),{data:{error:"navigation_configuration_changed"}});
      const resume=record(loaded.goals).observe===true;
      await stopGoals(origin);ensureOperation(origin);
      await persist(values);ensureOperation(origin);
      if(resume)await ensureAnalysis(origin);
    })();startTask.current=task;
    try{await task;}catch(err){if(!stopping.current)failure(err);}
    finally{startTask.current=null;if(!stopping.current)setOperation(null);}
  };
  const acceptTrial=async()=>{
    const camera=client.currentCamera();
    if(!policy||dirty||operation||loading||goals.drive===true||imageDecision.vision!=="go"||!freshPolicyFrame(camera.frame,camera.stream,loopState,policy))return;
    setOperation("save");setError("");setTrialResult("");
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    const task=(async()=>{
      const loaded=await apiFetch<JsonRecord>("/api/v1/rider/navigation/config");ensureOperation(origin);
      const current=record(loaded.config),model=record(loaded.detector);
      const visible=client.currentCamera();
      if(current.revision!==policy.revision||calibrationScope(current,model)!==calibrationScope(policy,detector)||decisionView(record(loaded.status),model).vision!=="go"||!freshPolicyFrame(visible.frame,visible.stream,record(loaded.status),current))throw Object.assign(new Error("navigation_configuration_changed"),{data:{error:"navigation_configuration_changed"}});
      await stopGoals(origin);ensureOperation(origin);
      await persist(calibrationAcceptance(current,model,"operator-"+crypto.randomUUID()));ensureOperation(origin);
      setTrialResult("trialAcceptanceSaved");
      await ensureAnalysis(origin);
    })();startTask.current=task;
    try{await task;}catch(err){if(!stopping.current)failure(err);}
    finally{startTask.current=null;if(!stopping.current)setOperation(null);}
  };
  const runTrial=async()=>{
    if(!policy||trialUnavailable||operation||loading||phase==="running"&&goals.drive===true)return;
    setOperation("check");setError("");setTrialFailure(null);setTrialResult("trialChecking");setTrialStopReason("");trialArmed.current=false;
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    trialSession.current=origin;
    const task=(async()=>{
      setOperation("start");setTrialResult("trialStarting");client.setCameraStream("venom");
      const plan=t("trialPlan")+" · "+String(policy.speed)+" SDK · ≤ "+String(policy.max_motion_ms)+" ms";
      trialArmed.current=true;
      const result=await client.startNavigationTrial(plan,Number(policy.revision));
      if(stopping.current||client.currentSession()!==origin)throw new Error("navigation_trial_cancelled");
      const saved=record(result.config);if(Object.keys(saved).length)applyPolicy(saved);
      operationGeneration.current=client.currentGeneration();operationEpoch.current=client.currentMotionEpoch();
      await readState();
      setTrialResult("trialStarted");
    })();
    startTask.current=task;
    try{await task;}
    catch(err){if(!stopping.current){failure(err);setTrialResult("trialFailed");}}
    finally{startTask.current=null;if(!stopping.current)setOperation(null);}
  };
  useEffect(()=>{
    // Only recover a trial initiated in this panel; never replay an old ARM.
    if(!trialArmed.current||operation||client.busy||!connected||stopping.current||trialSession.current!==client.currentSession()
      ||loopState.mode!=="drive"||loopState.running!==false||loopState.latched!==true)return;
    const reason=String(loopState.stop_reason??"");
    if(["navigation_stopped","operator_stop","process_signal"].includes(reason))return;
    trialArmed.current=false;setError("");setTrialStopReason(reason);setTrialResult("trialEnded");setOperation("start");
    const origin=client.currentSession();
    const task=(async()=>{
      if(stopping.current||client.currentSession()!==origin)return;
      // Retire the stopped drive intent before observation; a requested S14
      // otherwise survives the producer STOP and disables the next operator test.
      await client.mutate("/goal/s14_venom_navigation",{enabled:false},"POST",true);
      if(stopping.current||client.currentSession()!==origin)return;
      await client.mutate("/goal/s15_venom_perception",{enabled:true},"POST",true);
      if(mounted.current&&client.currentSession()===origin)await readState();
    })();startTask.current=task;
    void task.catch(err=>{if(!stopping.current)failure(err);}).finally(()=>{
      if(startTask.current===task)startTask.current=null;
      if(mounted.current&&!stopping.current)setOperation(null);
    });
  },[client,connected,failure,loopState,operation]);
  const observeBlock=policy?loopBlock({...policy,mode:"observe"},runtime,detector,connected,dirty,client.status):"loopLoadFailed";
  const trialUnavailable=trialBlock(policy,runtime,detector,connected,dirty,client.status);
  const decision=record(loopState.decision);
  const perception=record(draft?.perception);
  const permission=motionPermission(loopState,goals,policy,detector,client.status);
  const imageDecision=decisionView(loopState,detector);
  const sharedTrial=loopState.running===true&&loopState.mode==="drive"?null:completedTrial(loopState);
  const displayedStopReason=sharedTrial?String(sharedTrial.stop_reason):trialStopReason;
  const preparation=trialPreparation(loopState,trialFailure);
  const hasPreparationError=trialPreparation({},trialFailure)!==null;
  const visibleError=preparation?.source==="pi"&&hasPreparationError||errorRun&&sharedTrial?.run_id===errorRun?"":error;

  return <Panel title={t("loopTitle")} extra={<Button aria-label={t("loopLoad")} disabled={loading||!!operation||!connected} onClick={()=>setRefresh(v=>v+1)}><RefreshCw size={14}/>{t("refresh")}</Button>}>
    <div className={styles.loopStatus} role="status" data-testid="loop-state">
      <Pill tone={phase==="running"?"good":phase==="offline"||phase==="unknown"||phase==="fault"?"warning":undefined}>{t(operation==="stop"?"loopStopping":phase==="fault"?"loopPhase.fault":operation==="check"?"trialChecking":operation==="start"?"loopStarting":"loopPhase."+phase)}</Pill>
      <strong>{t(goals.drive===true?"trialActiveMode":"trialAnalysisMode")}</strong>
      <span className={styles.small} data-testid="active-perception-method">{t("perceptionMethod")}: {t(detector.purpose==="relative_ground_geometry"?"perceptionGeometry":"perceptionSemantic")}</span>
      <strong data-testid="venom-permission" className={permission.venom===true?styles.verdictGo:undefined}>{t(executionHeadline(loopState,goals,client.status,permission))}</strong>
    </div>
    <div className={styles.loopToolbar}>
    <div className={styles.loopActions}>
      <Button data-testid="loop-preview" disabled={!connected||!!operation||loading||!!observeBlock||active&&loopState.running===true} onClick={()=>void observePreview()}><Play size={18}/>{t(active&&loopState.running===true?"analysisRunning":"startObservation")}</Button>
      {<Button tone="primary" data-testid="loop-start" title={t("trialSimpleHelp")} aria-describedby={trialUnavailable?"rider-trial-block":undefined} disabled={!!trialUnavailable||!!operation||!!client.busy||loading||phase==="running"&&goals.drive===true} onClick={()=>void runTrial()}><Play size={18}/>{t("trialRun")}</Button>}
      <Button tone="danger" data-testid="loop-stop" disabled={!connected||operation==="stop"||operation==="save"} onClick={()=>void stop()}><Square size={18}/>{t(operation==="stop"?"loopStopping":"loopStop")}</Button>
      {detector.drive_supported!==false&&<span className={styles.small} data-testid="trial-plan">{String(policy?.speed??"—")} SDK · ≤ {String(policy?.max_motion_ms??"—")} ms</span>}
    </div>
    </div>
    {policy&&<div className={styles.loopModeRow} data-testid="stopping-zone">
      <Field label={t("stopZone")}><select aria-label={t("stopZone")} data-testid="loop-zone" className={styles.select} value={stoppingZone(policy)} disabled={!connected||loading||!!operation||!!client.busy||dirty||goals.drive===true} onChange={e=>{if(e.target.value==="near"||e.target.value==="far")void changeStoppingZone(e.target.value);}}>
        <option value="near" disabled={!stoppingZoneValues(policy,"near")}>{t("stopZoneNear")}</option>
        <option value="far" disabled={!stoppingZoneValues(policy,"far")}>{t("stopZoneFar")}</option>
        {stoppingZone(policy)==="custom"&&<option value="custom" disabled>{t("stopZoneCustom")}</option>}
      </select></Field>
      <p className={styles.hint}>{t(operation==="zone"?"stopZoneUpdating":"stopZoneHelp")}</p>
    </div>}
    {policy&&!calibrationMatches(policy,detector)&&detector.drive_supported!==false&&<div className={styles.stack} data-testid="trial-acceptance">
      <p className={styles.hint}>{t("trialAcceptanceHelp")}</p>
      <Button data-testid="trial-accept" disabled={!connected||!!operation||loading||dirty||goals.drive===true||imageDecision.vision!=="go"||!freshPolicyFrame(client.camera,client.cameraStream,loopState,policy)} onClick={()=>void acceptTrial()}>{t("trialAccept")}</Button>
    </div>}
    {preparation&&<div className={preparation.state==="failed"?styles.loopError:styles.metric} role={preparation.state==="failed"?"alert":"status"} data-testid="trial-preparation" data-request-id={preparation.requestId}>
      <strong>{t(trialPreparationTitle(preparation))} · {t("trialPreparation.phase."+preparation.phase)}</strong>
      <p className={styles.small}>{t("trialPreparationElapsed",{seconds:formatNumber(preparation.elapsedMs===null?null:preparation.elapsedMs/1000,language,1)})}</p>
      <p className={styles.small}>{t(preparation.source==="pi"?"trialPreparationSource":"trialPreparationResponse")} · {formatTime(preparation.observedAt,language)}{!preparation.fresh?" · "+t("trialPreparationStale"):""}</p>
      {preparation.error&&<p>{t(trialPreparationErrorKey(preparation))}</p>}
    </div>}
    {!!trialUnavailable&&<p id="rider-trial-block" className={styles.loopError} role="status" data-testid="loop-block">{t(trialUnavailable)}</p>}
    {!!trialResult&&!displayedStopReason&&!(trialResult==="trialStarted"&&phase==="running")&&<p className={styles.hint} role="status" data-testid="trial-result">{t(phase==="fault"?"trialEnded":trialResult==="trialStarted"&&phase==="stopped"?"trialStopped":trialResult)}</p>}
    {loopState.mode==="drive"&&loopState.paused===true&&loopState.running===true&&<p className={styles.hint} data-testid="trial-visual-pause">{t("trialVisualPause")}</p>}
    {!!displayedStopReason&&<p role="status" className={styles.loopError} data-testid="trial-stop-reason">{sharedTrial?t("lastTrial")+" · "+formatTime(sharedTrial.observed_at,language):t("trialEnded")} · {t("vision.reason."+displayedStopReason)}</p>}
    {phase==="fault"&&!displayedStopReason&&<p role="alert" className={styles.loopError}>{t("loopFaultHint")} · {String(loopState.stop_reason??"")}</p>}

    {(visibleError||readError)&&<p className={styles.loopError} role="alert">{t("trialRequestError")} · {visibleError||readError}</p>}
    <div className={styles.driveWorkspace}>
      <div className={styles.loopCamera}><CameraPanel client={client} t={t} language={language} compact analysisRunning={active&&loopState.running===true} analysisCurrent={imageDecision.current&&decision.reason!=="vision_observation_transport_retry"}/></div>
      <div className={styles.loopSidebar}>
        {manual}
        <LoopDecision frame={client.cameraStream==="venom"?client.camera:null} state={loopState} detector={detector} policy={policy} status={client.status} t={t} language={language}/>
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
        {"perception" in draft?<div className={styles.grid} data-testid="perception-settings">
          <Field label={t("perceptionMethod")}><select aria-label={t("perceptionMethod")} className={styles.select} disabled={active||!!operation} value={String(perception.engine??"segformer")} onChange={e=>setDraft({...draft,mode:"observe",perception:{...perception,engine:e.target.value}})}>
            <option value="segformer">{t("perceptionSemantic")}</option><option value="depth_plane">{t("perceptionGeometry")}</option>
          </select></Field>
          {perception.engine==="depth_plane"&&<>
            <Field label={t("perceptionResidual")}><input className={styles.input} disabled={active||!!operation} type="number" min="0.01" max="0.15" step="0.01" value={Number(perception.residual_limit)} onChange={e=>setDraft({...draft,perception:{...perception,residual_limit:Number(e.target.value)}})}/></Field>
            <Field label={t("perceptionOutliers")}><input className={styles.input} disabled={active||!!operation} type="number" min="0" max="2" step="0.1" value={Number(perception.outlier_limit)*100} onChange={e=>setDraft({...draft,perception:{...perception,outlier_limit:Number(e.target.value)/100}})}/></Field>
            <Field label={t("perceptionSlope")}><input className={styles.input} disabled={active||!!operation} type="number" min="1" max="4" step="0.1" value={Number(perception.slope_min)} onChange={e=>setDraft({...draft,perception:{...perception,slope_min:Number(e.target.value)}})}/></Field>
          </>}
        </div>:<p className={styles.hint}>{t("perceptionPiUpdate")}</p>}
        {perception.engine==="depth_plane"&&<p className={styles.hint}>{t("perceptionGeometryHint")}</p>}
        <p className={styles.hint}>{t("loopCalibrationSettingsHelp")}</p>
        <div className={styles.loopModeRow}><Button disabled={!connected||!!operation||active||!dirty} onClick={()=>void save()}>{t("loopSave")}</Button>{dirty&&<Button disabled={!!operation} onClick={()=>setDraft(policy)}>{t("loopDiscard")}</Button>}</div>

        <p className={styles.hint}>{t("loopModel")}: {t(detector.configured===true?"yes":"no")} · {t("computeRevision")}: {String(policy?.revision??"—")}</p>
      </div>:<p>{t(loading?"loading":"loopLoadFailed")}</p>}
    </details>
  </Panel>;
}
