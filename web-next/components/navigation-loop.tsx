import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Play, Square, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { data, errorCode, formatNumber, formatTime, record, safetyOf, type JsonRecord } from "../lib/model";
import { calibrationAcceptance, calibrationMatches, calibrationScope, decisionView, executionHeadline, freshPolicyFrame, loopBlock, loopFailure, loopPhase, loopSettingsValues, motionPermission, stoppingZone, stoppingZoneValues, trialBlock, trialPreparation, trialPreparationTitle, trialPreparationErrorKey, trialDiagnostics, trialStopReasonKey, trialOutcomeTone, freshObservation } from "../lib/loop-controls";
import { journeyBlocks, journeyView } from "../lib/journey";
import { operatorPrioritySupported } from "../lib/navigation";
import { stopLoopResources } from "../lib/stop-loop";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { LoopDecision } from "./loop-decision";
import { CameraPanel } from "./control";
import { Button, Checkbox, Field, Input, Panel, Pill, Select } from "./primitives";
import styles from "../rider.module.css";

export function NavigationLoop({client,t,language,manual,auxiliary}:{client:RiderClient;t:Translator;language:string;manual:ReactNode;auxiliary?:ReactNode}) {
  const [view, setView] = useState<"manual" | "automatic" | "journey">("manual");
  const [policy,setPolicy]=useState<JsonRecord|null>(null);
  const [draft,setDraft]=useState<JsonRecord|null>(null);
  const [editingZone,setEditingZone]=useState(false);
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
  const [recoveryFailure,setRecoveryFailure]=useState<{runId:string;code:string}|null>(null);
  const [readError,setReadError]=useState("");
  const [trialResult,setTrialResult]=useState("");
  const [journeyError,setJourneyError]=useState("");
  const [settingsError,setSettingsError]=useState("");
  const [savedRevision,setSavedRevision]=useState<number|null>(null);
  const [journeyRequested,setJourneyRequested]=useState(false);
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
  const {setCameraTransition}=client;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;setCameraTransition(false);};},[setCameraTransition]);
  const connected=client.canMutate;
  const session=client.status?.connection.session;
  useEffect(()=>{
    const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;let configured=false;let reading=false;
    setLoopState({stale:true});setGoals({});setRuntime({});setTrialFailure(null);setSettingsError("");setSavedRevision(null);
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
  const settingsLocked=goals.drive===true||!!operation;
  const groundDraft=record(draft?.avoidance).enabled===true&&record(record(draft?.avoidance).geometry).mode==="ground";
  const groundActive=record(policy?.avoidance).enabled===true&&record(record(policy?.avoidance).geometry).mode==="ground";
  const {cameraStream,setCameraStream}=client;
  useEffect(()=>{
    if(stopping.current||!active||loopState.running!==true)return;
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
    const key=trialStopReasonKey(cause), translated=t(key);
    setError(t(loopFailure(err))+" · "+errorCode(err)+(cause?" · "+(key==="trialStopped"?cause:translated):""));
  },[t]);
  const stop=async()=>{
    if(stopping.current)return;
    const trialWasArmed=trialArmed.current;
    const manualWasActive=client.canNavigate||!!client.activeDirection;
    client.hold.cancel();
    stopping.current=true;trialArmed.current=false;setOperation("stop");setError("");
    const priority = operatorPrioritySupported(client.status);
    if (!priority) { client.setCameraTransition(true);client.setCameraStream(null); }
    try {
      if (priority) {
        await client.stopManual();
        await readState();
        setTrialResult("operatorPaused");
        return;
      }
      const stoppedMotion=goals.drive===true||trialWasArmed;
      if(stoppedMotion)await client.command({action:"stop"},true);
      else if(manualWasActive)await client.stopManual();
      await startTask.current?.catch(()=>undefined);
      const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");
      const actualGoals=record(row.goals);
      if(actualGoals.drive===true&&!stoppedMotion)await client.command({action:"stop"},true);
      // Stopping analysis alone preserves manual ARM; release the explicit S2 reservation too.
      await stopLoopResources(actualGoals,client.mutate);
      await readState();
      client.setCameraStream(null);
      setTrialResult("trialStopped");
    } catch(err){failure(err);}
    finally{client.setCameraTransition(false);stopping.current=false;trialArmed.current=false;setOperation(null);}
  };
  const persist=async(values:JsonRecord)=>{
    if(!policy)throw new Error("loop_configuration_unavailable");
    const result=await client.mutate("/navigation/config",{revision:policy.revision,values},"POST",true);
    const saved=record(data(result).config);applyPolicy(saved);return saved;
  };
  const save=async()=>{
    if(!draft||!policy||goals.drive===true||operation)return;
    setOperation("save");setError("");setSettingsError("");setSavedRevision(null);
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    const task=(async()=>{
      const loaded=await apiFetch<JsonRecord>("/api/v1/rider/navigation/config");ensureOperation(origin);
      if(record(loaded.config).revision!==policy.revision||record(loaded.goals).drive===true)throw new Error("navigation_configuration_changed");
      const resume=record(loaded.goals).observe===true;
      await stopGoals(origin);ensureOperation(origin);
      const saved=await persist(loopSettingsValues(policy,draft));
      setSavedRevision(Number(saved.revision));ensureOperation(origin);
      if(resume)await ensureAnalysis(origin);
      setEditingZone(false);
    })();startTask.current=task;
    try{await task;}catch(err){setSettingsError(t(loopFailure(err))+" · "+errorCode(err));}finally{startTask.current=null;if(!stopping.current)setOperation(null);}
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
    if((record(row.goals).observe===true || operatorPrioritySupported(client.status)&&record(row.status).operator_control_version===1)&&record(row.status).running===true&&record(row.status).stale!==true){
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
    if(!policy||trialUnavailable||journey.active||operation||loading||phase==="running"&&goals.drive===true&&record(safetyOf(client.status).operator_control).state==="active")return;
    client.setNotice(null);setOperation("check");setError("");setTrialFailure(null);setTrialResult("trialChecking");setTrialStopReason("");trialArmed.current=false;
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    trialSession.current=origin;
    const task=(async()=>{
      setOperation("start");setTrialResult("trialStarting");client.setCameraStream("venom");
      const plan=t(record(policy.avoidance).enabled===true?"avoidanceTrialPlan":"trialPlan")+" · "+String(policy.speed)+" SDK · ≤ "+String(policy.max_motion_ms)+" ms";
      trialArmed.current=true;
      const warm = operatorPrioritySupported(client.status) && loopState.operator_control_version === 1 && loopState.running === true && loopState.stale !== true && safetyOf(client.status).state === "ARMED";
      const epoch = client.currentMotionEpoch();
      const result = warm
        ? record(await client.command({action:"navigation_resume"},true,true))
        : await client.startNavigationTrial(plan,Number(policy.revision));
      if(stopping.current||client.currentSession()!==origin||client.currentMotionEpoch()!==epoch)throw new Error("navigation_trial_cancelled");
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
  const runJourney=async()=>{
    if(!policy||journeyUnavailable.length||operation||loading||startTask.current||journey.active)return;
    const origin=client.currentSession(),epoch=client.currentMotionEpoch();
    client.setNotice(null);setOperation("start");setJourneyError("");setJourneyRequested(false);trialArmed.current=false;
    const task=(async()=>{
      client.setCameraStream("venom");
      const plan=t("journeyPlan")+" · "+String(policy.speed)+" SDK · ≤ "+String(policy.max_motion_ms)+" ms";
      const result=await client.startNavigationJourney(plan,Number(policy.revision));
      if(stopping.current||client.currentSession()!==origin||client.currentMotionEpoch()!==epoch)throw new Error("navigation_trial_cancelled");
      const saved=record(result.config);if(Object.keys(saved).length)applyPolicy(saved);
      await readState();setJourneyRequested(true);
    })();startTask.current=task;
    try{await task;}catch(err){if(!stopping.current)setJourneyError(errorCode(err));}
    finally{startTask.current=null;if(!stopping.current)setOperation(null);}
  };
  const stopJourney=async()=>{
    if(stopping.current)return;
    stopping.current=true;setOperation("stop");setJourneyError("");client.hold.cancel();
    try{await client.command({action:"stop"},true);await readState();setJourneyRequested(false);}
    catch(err){setJourneyError(errorCode(err));}
    finally{stopping.current=false;setOperation(null);}
  };
  const showJourneySettings=()=>{
    setEditingZone(false);
    if(settingsRef.current){settingsRef.current.open=true;settingsRef.current.scrollIntoView({block:"start",behavior:"smooth"});}
  };
  useEffect(()=>{
    // The compatible producer keeps observing after a completed trial.
    if (operatorPrioritySupported(client.status)) return;
    // Only recover a trial initiated in this panel; never replay an old ARM.
    if(!trialArmed.current||operation||client.busy||!connected||stopping.current||trialSession.current!==client.currentSession()
      ||loopState.mode!=="drive"||loopState.running!==false||loopState.latched!==true)return;
    const reason=String(loopState.stop_reason??"");
    if(["navigation_stopped","operator_stop","process_signal"].includes(reason))return;
    trialArmed.current=false;setError("");setTrialStopReason(reason);setTrialResult("trialEnded");setOperation("start");setCameraTransition(true);
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
    void task.catch(err=>{if(!stopping.current)setRecoveryFailure({runId:String(loopState.run_id??""),code:errorCode(err)});}).finally(()=>{
      if(startTask.current===task)startTask.current=null;
      if(mounted.current){setCameraTransition(false);if(!stopping.current)setOperation(null);}
    });
  },[client,connected,failure,loopState,operation,setCameraTransition]);
  useEffect(()=>{
    if(freshObservation(loopState,goals))setRecoveryFailure(null);
  },[loopState,goals]);
  const observeBlock=policy?loopBlock({...policy,mode:"observe"},runtime,detector,connected,dirty,client.status):"loopLoadFailed";
  const avoidanceSupported=Array.isArray(detector.supported_protocol_versions)&&detector.supported_protocol_versions.includes(Number(record(draft?.avoidance??policy?.avoidance).protocol_version??3));
  const trialUnavailable=record(policy?.avoidance).enabled===true&&!avoidanceSupported?"avoidanceUpdateRequired":trialBlock(policy,runtime,detector,connected,dirty,client.status);
  const journeyUnavailable=journeyBlocks(policy,runtime,detector,connected,dirty,client.status);
  const journey=journeyView(loopState);
  const decision=record(loopState.decision);
  const perception=record(draft?.perception);
  const permission=motionPermission(loopState,goals,policy,detector,client.status);
  const imageDecision=decisionView(loopState,detector);
  const diagnostic=trialDiagnostics(loopState,trialFailure);
  const sharedTrial=diagnostic.outcome;
  const displayedStopReason=sharedTrial?String(sharedTrial.stop_reason):trialStopReason;
  const preparation=diagnostic.preparation;
  const hasPreparationError=trialPreparation({},trialFailure)!==null;
  const visibleError=diagnostic.requestErrorCovered||preparation?.source==="pi"&&hasPreparationError||errorRun&&sharedTrial?.run_id===errorRun?"":error;
  const sharedRecovery=sharedTrial&&recoveryFailure?.runId===sharedTrial.run_id?recoveryFailure:null;

  const safety = safetyOf(client.status);
  const owner = safety.owner;
  const operator = record(safety.operator_control);
  const priority = operatorPrioritySupported(client.status);
  const automaticActive = priority ? operator.state === "active" || operator.state === "qualifying" : phase === "running" && goals.drive === true;
  return <section className={styles.controlWorkspace} data-testid="control-workspace">
    <div className={styles.controlModeRow}>
      <div className={styles.controlModes} role="group" aria-label={t("workspaceView")}>
        {(["manual", "automatic", "journey"] as const).map(mode => <Button key={mode} data-testid={"workspace-" + mode} aria-pressed={view === mode} tone={view === mode ? "primary" : undefined} onClick={() => setView(mode)}>{t(mode === "manual" ? "workspaceManual" : mode === "journey" ? "workspaceJourney" : "workspaceAutomatic")}</Button>)}
      </div>
      <span className={styles.workspaceOwner} role="status">{t("workspaceOwner")}: <strong>{t(owner === "manual" ? "workspaceManual" : owner === "navigation" ? safety.arming_mode === "journey" ? "workspaceJourney" : "workspaceAutomatic" : safety.state === "DISARMED" ? "workspaceDisarmedOwner" : "workspaceNoOwner")}</strong></span>
    </div>
    <div className={styles.driveWorkspace}>
      <div className={styles.workspaceContent} data-testid="workspace-content" tabIndex={0} role="region" aria-label={t("workspaceObservation")}>
        <div hidden={view !== "automatic"} data-testid="automatic-commands">
          <Panel title={t("loopTitle")} extra={<Button aria-label={t("loopLoad")} disabled={loading||!!operation||!connected} onClick={()=>setRefresh(v=>v+1)}><RefreshCw size={14}/>{t("refresh")}</Button>}>
    <div className={styles.loopStatus} role="status" data-testid="loop-state">
      {priority && <Pill>{t("operatorState." + String(operator.state ?? "idle"))}</Pill>}
      <Pill tone={phase==="running"?"good":phase==="offline"||phase==="unknown"||phase==="fault"?"warning":undefined}>{t(operation==="stop"?"loopStopping":phase==="fault"?"loopPhase.fault":operation==="check"?"trialChecking":operation==="start"?"loopStarting":"loopPhase."+phase)}</Pill>
      <strong>{t(goals.drive===true?"trialActiveMode":"trialAnalysisMode")}</strong>
      <span className={styles.small} data-testid="active-perception-method">{t("perceptionMethod")}: {t(detector.purpose==="relative_ground_geometry"?"perceptionGeometry":"perceptionSemantic")}</span>
      <strong data-testid="venom-permission" className={permission.venom===true?styles.verdictGo:undefined}>{t(executionHeadline(loopState,goals,client.status,permission))}</strong>
    </div>
    <div className={styles.loopToolbar}>
    <div className={styles.loopActions}>
      <Button size="large" tone="primary" data-testid="loop-start" title={t("trialSimpleHelp")} aria-describedby={trialUnavailable?"rider-trial-help rider-trial-block":"rider-trial-help"} disabled={!!trialUnavailable||!!operation||!!client.busy||loading||automaticActive||journey.active} onClick={()=>void runTrial()}><Play size={18}/>{t(priority&&loopState.operator_control_version===1&&loopState.running===true?"operatorResume":record(policy?.avoidance).enabled===true?"avoidanceTrialRun":"trialRun")}</Button>
      <Button size="large" tone="caution" data-testid="loop-stop" title={t(priority ? "operatorPauseHint" : "loopStop")} disabled={!connected||operation==="stop"||!priority&&operation==="save"} onClick={()=>void stop()}><Square size={18}/>{t(operation==="stop"?"loopStopping":priority?"operatorPause":"loopStop")}</Button>
      {detector.drive_supported!==false&&<span className={styles.small} data-testid="trial-plan">{String(policy?.speed??"—")} SDK · ≤ {String(policy?.max_motion_ms??"—")} ms</span>}
    </div>
    </div>
          </Panel>
        </div>
        <div hidden={view !== "journey"} data-testid="journey-commands">
          <Panel title={t("journeyTitle")}>
            <div className={styles.loopStatus} role="status" data-testid="journey-state">
              <Pill tone={journey.active&&journey.phase!=="unknown"?"good":journey.phase==="blocked"||journey.phase==="failed"||journey.phase==="unknown"?"warning":undefined}>{t("journey.state."+journey.phase)}</Pill>
              <span className={styles.small}>{t("journeyElapsed")}: {formatNumber(journey.elapsed,language,1)} s · {t("journeyManeuver")}: {journey.maneuver??"—"}</span>
            </div>
            <div className={styles.loopActions}>
              <Button size="large" tone="primary" data-testid="journey-start" disabled={journeyUnavailable.length>0||!!operation||!!client.busy||loading||journey.active||automaticActive} onClick={()=>void runJourney()}><Play size={18}/>{t("journeyStart")}</Button>
              <Button size="large" tone="caution" data-testid="journey-stop" disabled={!connected||operation==="stop"} onClick={()=>void stopJourney()}><Square size={18}/>{t("journeyStop")}</Button>
              <span className={styles.small} data-testid="journey-motion-limits">{String(policy?.speed??"—")} SDK · ≤ {String(policy?.max_motion_ms??"—")} ms</span>
            </div>
            <p className={styles.hint}>{t("journeyHelp")}</p>
            <p className={styles.hint} data-testid="journey-setup-help">{t("journeySetupHelp")}</p>
            {journeyUnavailable.length>0&&<div data-testid="journey-blocks" className={styles.loopError} role="status"><ul>{journeyUnavailable.map(block=><li key={block}>{t(block)}</li>)}</ul><Button onClick={showJourneySettings}>{t("journeySettings")}</Button></div>}
            {journey.reason&&<p className={styles.hint} data-testid="journey-reason">{t("state")}: <code>{journey.reason}</code></p>}
            {journeyRequested&&<p className={styles.hint}>{t("journeyStarted")}</p>}
            {journeyError&&<p className={styles.loopError} role="alert" data-testid="journey-error">{t("journeyRequestError")} · {journeyError}</p>}
          </Panel>
        </div>
        <div className={styles.loopCamera}><CameraPanel client={client} t={t} language={language} compact editor={view !== "manual" && editingZone && draft && Array.isArray(draft.corridor) ? {value:draft.corridor as number[],disabled:goals.drive===true||!!operation,onChange:value=>setDraft({...draft,corridor:value})}:undefined}/></div>
        <div hidden={view === "manual"} data-testid="automatic-panel">
          <Panel title={t("loopSettings")}>
    <p hidden={view !== "automatic"} id="rider-trial-help" className={styles.hint} data-testid="trial-start-help">{t("trialStartHelp")}</p>
    <details className={styles.details} data-testid="loop-observation">
      <summary>{t("observationOptional")}</summary>
      <p className={styles.hint}>{t("observationOptionalHelp")}</p>
      <Button data-testid="loop-preview" disabled={!connected||!!operation||loading||!!observeBlock||active&&loopState.running===true} onClick={()=>void observePreview()}><Play size={14}/>{t(active&&loopState.running===true?"analysisRunning":"startObservation")}</Button>
      {!!observeBlock&&<p className={styles.hint}>{t(observeBlock)}</p>}
    </details>
    {policy&&!groundActive&&<div className={styles.loopModeRow} data-testid="stopping-zone">
      <Field label={t("stopZone")}><Select aria-label={t("stopZone")} data-testid="loop-zone" value={stoppingZone(policy)} disabled={!connected||loading||!!operation||!!client.busy||dirty||goals.drive===true} onChange={e=>{if(e.target.value==="near"||e.target.value==="far")void changeStoppingZone(e.target.value);}}>
        <option value="near" disabled={!stoppingZoneValues(policy,"near")}>{t("stopZoneNear")}</option>
        <option value="far" disabled={!stoppingZoneValues(policy,"far")}>{t("stopZoneFar")}</option>
        {stoppingZone(policy)==="custom"&&<option value="custom" disabled>{t("stopZoneCustom")}</option>}
      </Select></Field>
      <p className={styles.hint}>{t(operation==="zone"?"stopZoneUpdating":"stopZoneHelp")}</p>
    </div>}
    {policy&&!calibrationMatches(policy,detector)&&detector.drive_supported!==false&&<div className={styles.stack} data-testid="trial-acceptance">
      <p className={styles.hint} data-testid="calibration-acceptance-help">{t(view==="journey"?"journeyAcceptanceHelp":"trialAcceptanceHelp")}</p>
      {view==="journey"&&<Button data-testid="journey-preview" disabled={!connected||!!operation||loading||!!observeBlock||active&&loopState.running===true} onClick={()=>void observePreview()}>{t(active&&loopState.running===true?"analysisRunning":"startObservation")}</Button>}
      <Button data-testid="trial-accept" disabled={!connected||!!operation||loading||dirty||goals.drive===true||imageDecision.vision!=="go"||!freshPolicyFrame(client.camera,client.cameraStream,loopState,policy)} onClick={()=>void acceptTrial()}>{t("trialAccept")}</Button>
    </div>}
    <div hidden={view !== "automatic"}>
    {preparation&&!diagnostic.combined&&<div className={preparation.state==="failed"?styles.loopError:styles.metric} role={preparation.state==="failed"?"alert":"status"} data-testid="trial-preparation" data-request-id={preparation.requestId}>
      <strong>{t(trialPreparationTitle(preparation))} · {t("trialPreparation.phase."+preparation.phase)}</strong>
      <p className={styles.small}>{t("trialPreparationElapsed",{seconds:formatNumber(preparation.elapsedMs===null?null:preparation.elapsedMs/1000,language,1)})}</p>
      <p className={styles.small}>{t(preparation.source==="pi"?"trialPreparationSource":"trialPreparationResponse")} · {formatTime(preparation.observedAt,language)}{!preparation.fresh?" · "+t("trialPreparationStale"):""}</p>
      {preparation.error&&<p>{t(trialPreparationErrorKey(preparation))}</p>}
    </div>}
    {!!trialUnavailable&&<p id="rider-trial-block" className={styles.loopError} role="status" data-testid="loop-block">{t(trialUnavailable)}</p>}
    {!!trialResult&&!displayedStopReason&&!(trialResult==="trialStarted"&&phase==="running")&&<p className={styles.hint} role="status" data-testid="trial-result">{t(phase==="fault"?"trialEnded":trialResult==="trialStarted"&&phase==="stopped"?"trialStopped":trialResult)}</p>}
    {loopState.mode==="drive"&&loopState.paused===true&&loopState.running===true&&<p className={styles.hint} data-testid="trial-visual-pause">{t("trialVisualPause")}</p>}
    {!!displayedStopReason&&<p role="status" className={trialOutcomeTone(displayedStopReason)==="neutral"?styles.hint:styles.loopError} data-testid="trial-stop-reason">{sharedTrial?t("lastTrial")+" · "+formatTime(sharedTrial.observed_at,language):t("trialEnded")} · {t(trialStopReasonKey(displayedStopReason))}
      {diagnostic.combined&&preparation&&<><br/><span className={styles.small} data-testid="trial-preparation-report">{t("trialPreparationReport",{phase:t("trialPreparation.phase."+preparation.phase),seconds:formatNumber(preparation.elapsedMs===null?null:preparation.elapsedMs/1000,language,1)})}</span></>}
      {sharedRecovery&&<><br/><span className={styles.small}>{t("trialRecoveryFailed")} · {sharedRecovery.code}</span></>}
    </p>}
    {phase==="fault"&&!displayedStopReason&&<p role="alert" className={styles.loopError}>{t("loopFaultHint")} · {String(loopState.stop_reason??"")}</p>}

    </div>
    {(visibleError||readError)&&<p className={styles.loopError} role="alert" data-testid="navigation-request-error">{t("trialRequestError")} · {visibleError||readError}</p>}
    <div className={styles.loopModeRow}>
      {!groundActive&&<Button data-testid="zone-edit" disabled={!draft||goals.drive===true||!!operation} onClick={()=>setEditingZone(!editingZone)}>{t(editingZone?"zoneFinish":"zoneEdit")}</Button>}
      {editingZone&&!groundActive&&<><Button data-testid="zone-save" disabled={!dirty||!!operation||goals.drive===true} onClick={()=>void save()}>{t("loopSave")}</Button><Button onClick={()=>{setDraft(policy);setEditingZone(false);}}>{t("loopDiscard")}</Button><Button disabled={!!operation} onClick={()=>draft&&setDraft({...draft,corridor:[.35,.80,.65,.95]})}>{t("zoneReset")}</Button></>}
    </div>
    {editingZone&&!groundActive&&<><p className={styles.hint}>{t("zoneDraftHelp")}</p><div className={styles.grid}>
      <Field label={t("perceptionResidual")}><Input type="number" min=".01" max=".15" step=".005" value={Number(perception.residual_limit)} onChange={e=>draft&&setDraft({...draft,perception:{...perception,residual_limit:Number(e.target.value)}})}/></Field>
      <Field label={t("perceptionOutliers")}><Input type="number" min="0" max="2" step=".1" value={Number(perception.outlier_limit)*100} onChange={e=>draft&&setDraft({...draft,perception:{...perception,outlier_limit:Number(e.target.value)/100}})}/></Field>
    </div></>}
    {groundActive&&<p className={styles.hint} data-testid="ground-geometry-help">{t("geometryPreviewHelp")}</p>}
    <details ref={settingsRef} className={styles.details}><summary>{t("loopSettings")}{dirty?" · "+t("unsaved"):""}</summary>
      <div className={styles.metrics}>
      <div className={styles.metric}><div className={styles.metricLabel}>{t("loopDecision")}</div><strong>{decision.state?t("vision."+String(decision.state)):"—"}</strong><p className={styles.small}>{String(decision.reason??loopState.reason??loopState.stop_reason??"")}</p></div>
      <div className={styles.metric}><div className={styles.metricLabel}>{t("loopLastFrame")}</div><strong>{formatTime(loopState.observed_at,language)}</strong><p className={styles.small}>{String(decision.inference_ms??"—")} ms · {t("loopLoaded")}: {t(detector.loaded===true?"yes":"no")}</p></div>
    </div>
      <p className={styles.hint}>{t("loopHint")}</p>
      {draft?<div className={styles.stack}>

        <div className={styles.grid}>
          <Field label={t("loopSpeed")}><Input disabled={settingsLocked} type="number" min="0.01" max="0.15" step="0.01" value={Number(draft.speed)} onChange={e=>setDraft({...draft,speed:Number(e.target.value)})}/></Field>
          <Field label={t("loopBudget")}><Input disabled={settingsLocked} type="number" min="100" max="500" step="10" value={Number(draft.decision_budget_ms)} onChange={e=>setDraft({...draft,decision_budget_ms:Number(e.target.value)})}/></Field>
          <Field label={t("loopPulse")}><Input disabled={settingsLocked} type="number" min="50" max="200" step="10" value={Number(draft.max_motion_ms)} onChange={e=>setDraft({...draft,max_motion_ms:Number(e.target.value)})}/></Field>
          {!groundDraft&&<Field label={t("loopCorridor")}><Input disabled={settingsLocked} value={Array.isArray(draft.corridor)?draft.corridor.join(","):""} onChange={e=>setDraft({...draft,corridor:e.target.value.split(",").map(Number)})}/></Field>}
        </div>
        {"perception" in draft?<div className={styles.grid} data-testid="perception-settings">
          <Field label={t("perceptionMethod")}><Select aria-label={t("perceptionMethod")} disabled={settingsLocked} value={String(perception.engine??"segformer")} onChange={e=>setDraft({...draft,mode:"observe",perception:{...perception,engine:e.target.value}})}>
            <option value="segformer">{t("perceptionSemantic")}</option><option value="depth_plane">{t("perceptionGeometry")}</option>
          </Select></Field>
          {perception.engine==="depth_plane"&&<>
            <Field label={t("perceptionResidual")}><Input disabled={settingsLocked} type="number" min="0.01" max="0.15" step="0.01" value={Number(perception.residual_limit)} onChange={e=>setDraft({...draft,perception:{...perception,residual_limit:Number(e.target.value)}})}/></Field>
            <Field label={t("perceptionOutliers")}><Input disabled={settingsLocked} type="number" min="0" max="2" step="0.1" value={Number(perception.outlier_limit)*100} onChange={e=>setDraft({...draft,perception:{...perception,outlier_limit:Number(e.target.value)/100}})}/></Field>
            {!groundDraft&&<Field label={t("perceptionSlope")}><Input disabled={settingsLocked} type="number" min="1" max="4" step="0.1" value={Number(perception.slope_min)} onChange={e=>setDraft({...draft,perception:{...perception,slope_min:Number(e.target.value)}})}/></Field>}
          </>}
        </div>:<p className={styles.hint}>{t("perceptionPiUpdate")}</p>}
        {perception.engine==="depth_plane"&&<p className={styles.hint}>{t("perceptionGeometryHint")}</p>}
        {record(draft?.avoidance).protocol_version===3&&<fieldset className={styles.stack} disabled={settingsLocked} data-testid="ground-geometry-settings"><legend>{t("geometrySettings")}</legend>
          <Field label={t("geometryMode")}><Select data-testid="geometry-mode" value={String(record(record(draft?.avoidance).geometry).mode??"image")} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),geometry:{...record(record(draft.avoidance).geometry),mode:e.target.value},sweep_verified:false}})}><option value="ground">{t("geometryGround")}</option><option value="image">{t("geometryImage")}</option></Select></Field>
          <p className={styles.hint} data-testid="geometry-mode-help">{t(record(record(draft?.avoidance).geometry).mode==="ground"?"geometryCalibrationHelp":"geometryImageHelp")}</p>
          {record(record(draft?.avoidance).geometry).mode==="ground"&&<><p className={styles.hint}>{t("geometryHelp")}</p>{(["fx","fy","cx","cy","radius_ratio","lookahead_ratio","max_heading_deg"] as const).map(key=><Field key={key} label={t("geometry."+key)}><Input type="number" step=".01" value={record(record(draft?.avoidance).geometry)[key]===null?"":Number(record(record(draft?.avoidance).geometry)[key])} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),geometry:{...record(record(draft.avoidance).geometry),[key]:e.target.value===""?null:Number(e.target.value)},sweep_verified:false}})}/></Field>)}</>}
        </fieldset>}
        {"avoidance" in (draft??{})&&<fieldset disabled={settingsLocked} className={styles.stack} data-testid="avoidance-settings"><legend>{t("avoidanceSettings")}</legend>{!avoidanceSupported&&<p>{t("avoidanceUpdateRequired")}</p>}
          <label className={styles.check}><Checkbox type="checkbox" disabled={!avoidanceSupported} checked={record(draft?.avoidance).enabled===true} onChange={e=>draft&&setDraft({...draft,mode:"observe",avoidance:{...record(draft.avoidance),enabled:e.target.checked,protocol_version:3,geometry:record(draft.avoidance).geometry??{mode:"ground",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45},max_stalled_corrections:record(draft.avoidance).max_stalled_corrections??2,progress_min_delta:record(draft.avoidance).progress_min_delta??.03,sweep_verified:false}})}/>{t("avoidanceEnabled")}</label>
          <Field label={t("avoidanceTurnSpeed")}><Input type="number" min=".01" max=".1" step=".01" value={Number(record(draft?.avoidance).turn_speed)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),turn_speed:Number(e.target.value),sweep_verified:false}})}/></Field>
          <Field label={t("avoidanceTurnMs")}><Input type="number" min="50" max="150" step="10" value={Number(record(draft?.avoidance).turn_ms)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),turn_ms:Number(e.target.value),sweep_verified:false}})}/></Field>
          <label className={styles.check}><Checkbox type="checkbox" checked={record(draft?.avoidance).mirrored===true} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),mirrored:e.target.checked,sweep_verified:false}})}/>{t("avoidanceMirrored")}</label>
          <label className={styles.check}><Checkbox type="checkbox" checked={record(draft?.avoidance).sweep_verified===true} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),sweep_verified:e.target.checked}})}/>{t("avoidanceSweepAcceptance")}</label>
          <Field label={t("avoidanceStalled")}><Input type="number" min="1" max="5" step="1" value={Number(record(draft?.avoidance).max_stalled_corrections??2)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),max_stalled_corrections:Number(e.target.value),sweep_verified:false}})}/></Field>
          <Field label={t("avoidanceProgress")}><Input type="number" min="0" max=".3" step=".01" value={Number(record(draft?.avoidance).progress_min_delta??.03)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),progress_min_delta:Number(e.target.value),sweep_verified:false}})}/></Field>
          <Field label={t("avoidanceMaxTurns")}><Input type="number" min="1" max="5" step="1" value={Number(record(draft?.avoidance).max_turn_pulses)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),max_turn_pulses:Number(e.target.value),sweep_verified:false}})}/></Field><p className={styles.hint}>{t(view==="journey"?"journeyAvoidanceHelp":"avoidanceHelp")}</p>
        </fieldset>}
        <p className={styles.hint}>{t("loopCalibrationSettingsHelp")}</p>
        <p className={styles.hint} data-testid="settings-save-help">{t(goals.drive===true?"loopSettingsDriveActive":dirty?"loopSettingsSaveHelp":"loopSettingsNoChanges")}</p>
        {settingsError&&<p className={styles.loopError} role="alert" data-testid="settings-save-error">{t(savedRevision!==null?"loopSettingsResumeFailed":"loopSaveFailed")} · {settingsError}</p>}
        {savedRevision!==null&&policy?.revision===savedRevision&&!dirty&&<p className={styles.hint} role="status" data-testid="settings-save-success">{t("loopSettingsSaved",{revision:String(savedRevision)})}</p>}
        <div className={styles.loopModeRow}><Button data-testid="settings-save" disabled={!connected||!!operation||goals.drive===true||!dirty} onClick={()=>void save()}>{t("loopSave")}</Button>{dirty&&<Button disabled={!!operation} onClick={()=>setDraft(policy)}>{t("loopDiscard")}</Button>}</div>

        <p className={styles.hint}>{t("loopModel")}: {t(detector.configured===true?"yes":"no")} · {t("computeRevision")}: {String(policy?.revision??"—")}</p>
      </div>:<p>{t(loading?"loading":"loopLoadFailed")}</p>}
    </details>
          <LoopDecision frame={client.cameraStream==="venom"?client.camera:null} state={loopState} detector={detector} policy={policy} status={client.status} t={t} language={language}/>
          </Panel>
        </div>
        {auxiliary}
      </div>
      <aside className={styles.operatorConsole} aria-label={t("manual")} data-testid="operator-console">{manual}</aside>
    </div>
  </section>;
}
