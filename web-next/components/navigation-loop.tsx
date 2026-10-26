import { useOperatorTuning } from "../lib/operator-tuning";
import { useOperatorApply } from "../lib/use-operator-apply";
import { createBrowserUuid } from "@/lib/optional-modules/transport";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Play, Square, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { data, errorCode, formatNumber, formatTime, record, safetyOf, type JsonRecord } from "../lib/model";
import { calibrationAcceptance, calibrationFrameBlock, calibrationMatches, calibrationScope, decisionView, executionHeadline, freshPolicyFrame, loopBlock, loopFailure, loopPhase, navigationPulseLimit, navigationSpeedLimit, navigationTurnPulseLimit, navigationReadCurrent, refreshStartedNavigation, motionPermission, stoppingZone, stoppingZoneValues, trialBlock, trialPreparation, trialPreparationTitle, trialPreparationErrorKey, trialDiagnostics, trialStopReasonKey, trialOutcomeTone, freshObservation } from "../lib/loop-controls";
import { journeyBlocks, journeyView } from "../lib/journey";
import { operatorPrioritySupported } from "../lib/navigation";
import { stopLoopResources } from "../lib/stop-loop";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { OperatorStatus } from "./operator-status";
import { OperatorMessages } from "./operator-console";
import { PulseTrialsPanel } from "./pulse-trials";
import type { StudyRecording } from "../lib/use-study-recording";
import { OperatorMotion } from "./operator-motion";
import { LoopDecision } from "./loop-decision";
import { CameraPanel, CameraDetails, ManualPad } from "./control";
import { PerceptionModelsPanel } from "./perception-models";
import { NavigationProfiles } from "./navigation-profiles";
import { ModelTuning } from "./model-tuning";
import { Button, Checkbox, Field, Input, Panel, Pill, Select } from "./primitives";
import styles from "../rider.module.css";

const geometryRanges = {fx:[.1,5],fy:[.1,5],cx:[0,1],cy:[0,1],radius_ratio:[.05,5],lookahead_ratio:[.25,8],max_heading_deg:[5,70]} as const;

export function NavigationLoop({client,t,language,recording,operatorDetails,studyRecording}:{client:RiderClient;t:Translator;language:string;recording?:ReactNode;operatorDetails?:ReactNode;studyRecording?:StudyRecording}) {
  const workspace=useOperatorTuning();
  const {state:operatorDraft,setState:setOperatorDraft}=workspace;
  const view=operatorDraft.view;
  const setView=(view:typeof operatorDraft.view)=>setOperatorDraft(previous=>({...previous,view}));
  const policy=operatorDraft.base;
  const setPolicy=useCallback((base:JsonRecord|null)=>setOperatorDraft(previous=>({...previous,base})),[setOperatorDraft]);
  const [profileSnapshot,setProfileSnapshot]=useState<{policy:JsonRecord|null;session:string}>({policy:null,session:""});
  const [profileBusy,setProfileBusy]=useState(false);
  const draft=operatorDraft.draft;
  const setDraft=useCallback((value:JsonRecord|null)=>setOperatorDraft(previous=>({...previous,draft:value})),[setOperatorDraft]);
  const operatorApply=useOperatorApply(workspace,client);
  const [editingZone,setEditingZone]=useState(false);
  const dirty=operatorApply.dirty||operatorDraft.modelPending;
  const policyRef=useRef(policy),dirtyRef=useRef(dirty),policySession=useRef(client.currentSession());
  const stateReadSequence=useRef(0),stateAppliedSequence=useRef(0);
  policyRef.current=policy;dirtyRef.current=operatorApply.navigationDirty;
  const {setPerceptionPolicy,currentSession}=client;
  const applyPolicy=useCallback((value:JsonRecord|null)=>{
    policyRef.current=value;policySession.current=currentSession();setPolicy(value);setProfileSnapshot({policy:value,session:currentSession()});setDraft(value);setPerceptionPolicy?.(value);
  },[setPerceptionPolicy,currentSession,setPolicy,setDraft]);
  const [detector,setDetector]=useState<JsonRecord>({});
  const [loopState,setLoopState]=useState<JsonRecord>({});
  const [goals,setGoals]=useState<JsonRecord>({});
  const [runtime,setRuntime]=useState<JsonRecord>({});
  const [loading,setLoading]=useState(false);
  const [operationState,setOperation]=useState<"check"|"start"|"stop"|"save"|"zone"|null>(null);
  const operation=operationState??(profileBusy?"behavior":null);
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
  const refreshPerception=useCallback(()=>setRefresh(value=>value+1),[]);
  const startTask=useRef<Promise<unknown>|null>(null);
  const stopping=useRef(false);
  const settingsRef=useRef<HTMLElement>(null);
  const railRef=useRef<HTMLDivElement>(null);
  const workspaceDetailsRef=useRef<HTMLDetailsElement>(null);
  const preparationRef=useRef<HTMLDetailsElement>(null);
  const [detailsOpen,setDetailsOpen]=useState(false);
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
    setProfileSnapshot({policy:null,session:session??""});setLoopState({stale:true});setGoals({});setRuntime({});setTrialFailure(null);setSettingsError("");setSavedRevision(null);
    if(!connected)return ()=>abort.abort();
    setLoading(true);setReadError("");
    const apply=(row:JsonRecord)=>{setLoopState(record(row.status));setDetector(record(row.detector));setGoals(record(row.goals));};
    const tick=async(initial=false)=>{
      if(reading||document.hidden||abort.signal.aborted)return;
      reading=true;
      const first=initial||!configured;
      try {
        const request=++stateReadSequence.current;
        const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/"+(first?"config":"state"),{signal:abort.signal});
        if(abort.signal.aborted)return;
        const incoming=record(row.config);
        if(!navigationReadCurrent(request,stateAppliedSequence.current,incoming.revision,policySession.current===session?policyRef.current?.revision:undefined))return;
        stateAppliedSequence.current=request;
        apply(row);
        if(typeof incoming.revision==="number")setProfileSnapshot(previous=>previous.session===session&&Number(previous.policy?.revision??-1)>Number(incoming.revision)?previous:{policy:incoming,session:session??""});
        if((policySession.current!==session||Number(incoming.revision)>=Number(policyRef.current?.revision??-1))&&((!policyRef.current||!dirtyRef.current)&&typeof incoming.revision==="number"&&(incoming.revision>Number(policyRef.current?.revision??-1)||incoming.revision===policyRef.current?.revision&&JSON.stringify(incoming)!==JSON.stringify(policyRef.current))))applyPolicy(incoming);
        if(first){setRuntime(record(row.runtime));configured=true;}
        setReadError("");
      } catch(err) {
        if(abort.signal.aborted)return;
        setLoopState({stale:true});setGoals({});
        if(first&&!dirtyRef.current)applyPolicy(null);
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
  const settingsLocked=!!operation||loading;
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

  const readState=useCallback(async(refreshPolicy=false)=>{
    const origin=currentSession(),request=++stateReadSequence.current;
    const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");
    if(currentSession()!==origin||!navigationReadCurrent(request,stateAppliedSequence.current,record(row.config).revision,policyRef.current?.revision))return;
    stateAppliedSequence.current=request;
    setLoopState(record(row.status));setGoals(record(row.goals));setDetector(record(row.detector));
    if(refreshPolicy&&!dirtyRef.current&&typeof record(row.config).revision==="number")applyPolicy(record(row.config));
  },[currentSession,applyPolicy]);
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
  const persist=async(values:JsonRecord,base=policy)=>{
    if(!base)throw new Error("loop_configuration_unavailable");
    const result=await client.mutate("/navigation/config",{revision:base.revision,values},"POST",true);
    const saved={...base,...record(data(result).config)};applyPolicy(saved);return saved;
  };
  const save=async(resume=true)=>{
    if(!dirty||!operatorApply.valid||operatorDraft.modelPending&&!operatorDraft.modelReady||operation)return;
    setOperation("save");setSettingsError("");setSavedRevision(null);
    const task=operatorDraft.modelPending?(async()=>{const selected=await workspace.modelApply.current?.(resume);if(!selected)throw new Error("perception_selection_unconfirmed");return {policy:selected.policy};})():operatorApply.apply(resume);startTask.current=task;
    try{
      const result=await task;const saved=result.policy;setSavedRevision(Number(saved.revision));
      await readState(true);return saved;
    }catch(err){setSettingsError(t(loopFailure(err))+" · "+errorCode(err));throw err;}
    finally{startTask.current=null;setOperatorDraft(previous=>({...previous,busy:false}));if(!stopping.current)setOperation(null);}
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
    if(!policy||dirty||operation||loading||observeBlock||goals.drive===true)return;
    client.setNotice(null);setTrialFailure(null);setOperation("start");setError("");setTrialResult("");
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    const task=ensureAnalysis(origin);startTask.current=task;
    try{await task;}
    catch(err){if(!stopping.current)failure(err);}
    finally{startTask.current=null;setOperatorDraft(previous=>({...previous,busy:false}));if(!stopping.current)setOperation(null);}
  };
  const changeStoppingZone=(zone:"near"|"far")=>{
    if(!draft||operation||loading)return;
    const values=stoppingZoneValues(draft,zone);if(values)setDraft({...draft,...values});
  };
  const saveImageAcceptance=async(origin:string,acceptSweep:boolean,resumeAfterSave=true,base=policy,baseDetector=detector)=>{
    if(!base)throw new Error("loop_configuration_unavailable");
    const loaded=await apiFetch<JsonRecord>("/api/v1/rider/navigation/config");ensureOperation(origin);
    const current=record(loaded.config),model=record(loaded.detector);
    const visible=client.currentCamera();
    if(current.revision!==base.revision||calibrationScope(current,model)!==calibrationScope(base,baseDetector)||decisionView(record(loaded.status),model).vision!=="go"||!freshPolicyFrame(visible.frame,visible.stream,record(loaded.status),current))throw Object.assign(new Error("navigation_configuration_changed"),{data:{error:"navigation_configuration_changed"}});
    const resumeObservation=record(loaded.goals).observe===true;
    const continuous=current.approval_without_restart_version===1;
    // Updated Pi accepts only approval metadata while observing. Parameter/model
    // edits still use stopGoals; older peers keep their stopped-write contract.
    if(!continuous){await stopGoals(origin);ensureOperation(origin);}
    let saved:JsonRecord;
    try {
      saved=await persist(calibrationAcceptance(current,model,"operator-"+createBrowserUuid(),acceptSweep),current);ensureOperation(origin);
    } catch (saveError) {
      // Restore only observation explicitly requested before this transaction.
      // STOP/cancellation prevents both recovery and a following journey request.
      if(resumeObservation&&!continuous){
        ensureOperation(origin);
        try{await ensureAnalysis(origin);}
        catch(restoreError){
          if(errorCode(restoreError)==="navigation_trial_cancelled")throw restoreError;
          ensureOperation(origin);
          client.setNotice({key:"requestFailed",code:errorCode(restoreError),tone:"error"});
        }
      }
      throw saveError;
    }
    setTrialResult("trialAcceptanceSaved");
    if(resumeAfterSave&&!continuous)await ensureAnalysis(origin);
    return saved;
  };
  const acceptTrial=async()=>{
    const camera=client.currentCamera();
    if(!policy||dirty||operation||loading||goals.drive===true||view==="journey"&&(record(policy.avoidance).enabled!==true||record(policy.avoidance).protocol_version!==3)||imageDecision.vision!=="go"||!freshPolicyFrame(camera.frame,camera.stream,loopState,policy))return;
    client.setNotice(null);setTrialFailure(null);setOperation("save");setError("");setTrialResult("");
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    const task=saveImageAcceptance(origin,view==="journey");startTask.current=task;
    try{await task;}catch(err){if(!stopping.current)failure(err);}
    finally{startTask.current=null;setOperatorDraft(previous=>({...previous,busy:false}));if(!stopping.current)setOperation(null);}
  };
  const prepareStart=async(origin:string):Promise<JsonRecord>=>{
    let target=policy!;
    if(operatorDraft.modelPending){
      const selected=await workspace.modelApply.current?.(false);if(!selected)throw new Error("perception_selection_unconfirmed");
      target=selected.policy;operationEpoch.current=selected.token.epoch;operationGeneration.current=selected.token.generation;
      ensureOperation(origin);setOperatorDraft(previous=>({...previous,busy:true}));
    }else if(operatorApply.dirty){
      const applied=await operatorApply.apply(false);
      if(!mounted.current||stopping.current||client.currentSession()!==origin||document.hidden)throw new Error("navigation_trial_cancelled");
      target={...policy,...applied.policy};
      operationEpoch.current=applied.token.epoch;operationGeneration.current=applied.token.generation;
      setOperatorDraft(previous=>({...previous,busy:true}));
    }
    ensureOperation(origin);
    const acceptSweep=record(target.avoidance).enabled===true;
    if(calibrationMatches(target,detector)&&(!acceptSweep||record(target.avoidance).sweep_verified===true))return target;
    await ensureAnalysis(origin);ensureOperation(origin);
    const deadline=Date.now()+12000;
    while(Date.now()<deadline){
      ensureOperation(origin);
      const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state");ensureOperation(origin);
      const current=record(row.config),model=record(row.detector),state=record(row.status),camera=client.currentCamera();
      if(current.revision!==target.revision)throw new Error("navigation_configuration_changed");
      if(decisionView(state,model).vision==="go"&&freshPolicyFrame(camera.frame,camera.stream,state,current)){
        return saveImageAcceptance(origin,acceptSweep,false,current,model);
      }
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw new Error("navigation_fresh_frame_timeout");
  };
  const runTrial=async()=>{
    if(!policy||trialUnavailable||operatorDraft.modelPending&&!operatorDraft.modelReady||!operatorApply.valid||journey.active||operation||startTask.current||loading||phase==="running"&&goals.drive===true&&record(safetyOf(client.status).operator_control).state==="active")return;
    client.setNotice(null);setOperation("check");setError("");setTrialFailure(null);setTrialResult("trialChecking");setTrialStopReason("");trialArmed.current=false;
    const origin=client.currentSession();operationEpoch.current=client.currentMotionEpoch();operationGeneration.current=client.currentGeneration();
    trialSession.current=origin;
    const task=(async()=>{
      setOperation("start");setOperatorDraft(previous=>({...previous,busy:true}));setTrialResult("trialStarting");client.setCameraStream("venom");
      const target=await prepareStart(origin);ensureOperation(origin);
      const plan=t(record(target.avoidance).enabled===true?"avoidanceTrialPlan":"trialPlan")+" · "+String(target.speed)+" · ≤ "+String(target.max_motion_ms)+" ms";
      trialArmed.current=true;
      const warm = operatorPrioritySupported(client.status) && loopState.operator_control_version === 1 && loopState.running === true && loopState.stale !== true && safetyOf(client.status).state === "ARMED";
      const epoch = client.currentMotionEpoch();
      if(warm)await client.command({action:"navigation_resume"},true,true);
      else await client.startNavigationTrial(plan,Number(target.revision));
      if(stopping.current||client.currentSession()!==origin||client.currentMotionEpoch()!==epoch)throw new Error("navigation_trial_cancelled");
      operationGeneration.current=client.currentGeneration();operationEpoch.current=client.currentMotionEpoch();
      // Start returns persisted policy only; the state endpoint adds current Pi capabilities.
      const refreshError=await refreshStartedNavigation(()=>readState(true),()=>!stopping.current&&client.currentSession()===origin&&client.currentMotionEpoch()===epoch);
      if(refreshError){setReadError(t("loopStartedReadFailed")+" · "+errorCode(refreshError));setLoopState(previous=>({...previous,stale:true}));}
      setTrialResult("trialStarted");
    })();
    startTask.current=task;
    try{await task;}
    catch(err){if(!stopping.current){failure(err);setTrialResult("trialFailed");}}
    finally{startTask.current=null;setOperatorDraft(previous=>({...previous,busy:false}));if(!stopping.current)setOperation(null);}
  };
  const runJourney=async()=>{
    if(!policy||journeyUnavailable.length>0||operatorDraft.modelPending&&!operatorDraft.modelReady||!operatorApply.valid||operation||loading||startTask.current||journey.active)return;
    const origin=client.currentSession(),epoch=client.currentMotionEpoch();
    operationEpoch.current=epoch;operationGeneration.current=client.currentGeneration();
    client.setNotice(null);setTrialFailure(null);setError("");setOperation("start");setJourneyError("");setJourneyRequested(false);trialArmed.current=false;
    const task=(async()=>{
      client.setCameraStream("venom");
      setOperatorDraft(previous=>({...previous,busy:true}));
      const target=await prepareStart(origin);ensureOperation(origin);
      const plan=t("journeyPlan")+" · "+String(target.speed)+" · ≤ "+String(target.max_motion_ms)+" ms";
      const admissionEpoch=client.currentMotionEpoch();
      await client.startNavigationJourney(plan,Number(target.revision));
      if(stopping.current||client.currentSession()!==origin||client.currentMotionEpoch()!==admissionEpoch)throw new Error("navigation_trial_cancelled");
      // Never replace presented policy with the raw Start reply: it omits motion limits.
      const refreshError=await refreshStartedNavigation(()=>readState(true),()=>!stopping.current&&client.currentSession()===origin&&client.currentMotionEpoch()===admissionEpoch);
      if(refreshError){setReadError(t("loopStartedReadFailed")+" · "+errorCode(refreshError));setLoopState(previous=>({...previous,stale:true}));}
      setJourneyRequested(true);
    })();startTask.current=task;
    try{await task;}catch(err){if(!stopping.current&&client.currentMotionEpoch()===operationEpoch.current&&client.currentSession()===origin)setJourneyError(errorCode(err));}
    finally{startTask.current=null;setOperatorDraft(previous=>({...previous,busy:false}));if(!stopping.current)setOperation(null);}
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
    if(workspaceDetailsRef.current)workspaceDetailsRef.current.open=true;
    if(settingsRef.current)settingsRef.current.scrollIntoView({block:"nearest",behavior:"smooth"});
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
  },[client,connected,failure,loopState,operation,setCameraTransition,readState]);
  useEffect(()=>{
    if(freshObservation(loopState,goals))setRecoveryFailure(null);
  },[loopState,goals]);
  const showModelTuning = () => { setOperatorDraft(previous=>({...previous,pane:"model"})); if(railRef.current)railRef.current.scrollTop=0; };
  const showPreparation = () => {
    if (preparationRef.current) { preparationRef.current.open=true; preparationRef.current.scrollIntoView({block:"nearest",behavior:"smooth"}); }
    // This explicit operator action prepares observation only, never motion.
    if(goals.drive!==true&&!freshObservation(loopState,goals))void observePreview();
  };
  const observeBlock=policy?loopBlock({...policy,mode:"observe"},runtime,detector,connected,dirty,client.status):"loopLoadFailed";
  const avoidanceSupported=Array.isArray(detector.supported_protocol_versions)&&detector.supported_protocol_versions.includes(Number(record(draft?.avoidance??policy?.avoidance).protocol_version??3));
  const trialUnavailable=record(policy?.avoidance).enabled===true&&!avoidanceSupported?"avoidanceUpdateRequired":trialBlock(draft??policy,runtime,detector,connected,false,client.status,false);
  const journeyUnavailable=journeyBlocks(draft??policy,runtime,detector,connected,false,client.status,false);
  const nonAcceptanceJourneyBlock=journeyBlocks(draft??policy,runtime,detector,connected,false,client.status,false)[0]||"";
  const journey=journeyView(loopState);
  const decision=record(loopState.decision);
  const perception=record(draft?.perception);
  const permission=motionPermission(loopState,goals,policy,detector,client.status);
  const imageDecision=decisionView(loopState,detector);
  const acceptanceBlock=dirty?"loopUnsaved":view==="journey"&&(record(policy?.avoidance).enabled!==true||record(policy?.avoidance).protocol_version!==3)?"journeyAvoidanceRequired":calibrationFrameBlock(client.camera,client.cameraStream,loopState,policy)|| (imageDecision.vision!=="go"?"calibrationClearFrame":"");
  const confirmJourney=!!policy&&(!calibrationMatches(policy,detector)||record(policy.avoidance).sweep_verified!==true)
    &&!acceptanceBlock&&detector.loaded===true&&/^[a-f0-9]{64}$/.test(String(detector.sha256))
    &&!nonAcceptanceJourneyBlock;
  const diagnostic=trialDiagnostics(loopState,trialFailure);
  const sharedTrial=diagnostic.outcome;
  const displayedStopReason=sharedTrial?String(sharedTrial.stop_reason):trialStopReason;
  const preparation=diagnostic.preparation;
  const hasPreparationError=trialPreparation({},trialFailure)!==null;
  const visibleError=diagnostic.requestErrorCovered||preparation?.source==="pi"&&hasPreparationError||errorRun&&sharedTrial?.run_id===errorRun?"":error;
  const sharedRecovery=sharedTrial&&recoveryFailure?.runId===sharedTrial.run_id?recoveryFailure:null;

  const safety = safetyOf(client.status);
  const operator = record(safety.operator_control);
  const priority = operatorPrioritySupported(client.status);
  const automaticActive = priority ? operator.state === "active" || operator.state === "qualifying" : phase === "running" && goals.drive === true;
  const journeyBlockLabel=(block:string)=>block==="journeyMotionLimits"
    ?t(block,{speed:String(policy?.speed??"—"),pulse:String(policy?.max_motion_ms??"—"),speedLimit:String(navigationSpeedLimit(policy,true)),limit:String(navigationPulseLimit(policy,true))}):t(block);
  let commandReason = "";
  if (view === "journey") {
    if (journeyError) commandReason = t("journeyRequestError") + " · " + journeyError;
    else if (readError) commandReason=readError;
    else if (nonAcceptanceJourneyBlock||journeyUnavailable[0]) commandReason = journeyBlockLabel(nonAcceptanceJourneyBlock||journeyUnavailable[0]);
    else if (journey.reason&&["failed","blocked"].includes(journey.phase)) commandReason=t(["decision_timeout","decision_invalid_or_expired"].includes(journey.reason)?"workspace.journeyAnalysisExpired":trialStopReasonKey(journey.reason));
    else if (journey.active&&journey.reason) commandReason = t("state") + ": " + journey.reason;
  } else if (view === "automatic") {
    commandReason = visibleError || readError;
    if (!commandReason && preparation?.state === "failed") commandReason = t(trialPreparationErrorKey(preparation));
    if (!commandReason && trialUnavailable) commandReason = t(trialUnavailable);
    if (!commandReason && displayedStopReason) commandReason = t(trialStopReasonKey(displayedStopReason));
  }
  if(loading&&!policy&&view!=="manual")commandReason=t("loading");
  const needsAcceptance=!!policy&&(!calibrationMatches(policy,detector)||view==="journey"&&record(policy.avoidance).sweep_verified!==true);
  const prepareAction=needsAcceptance&&detector.drive_supported!==false&&(view==="automatic"&&!trialUnavailable||view==="journey"&&journeyUnavailable.length===0);
  if(prepareAction&&!dirty&&(view!=="journey"||!nonAcceptanceJourneyBlock)&&!journeyError&&!visibleError&&!readError){
    commandReason=t(!active||loopState.running!==true?"workspace.prepareImage":acceptanceBlock||"workspace.confirmReady");
  }
  return <section className={styles.controlWorkspace} data-testid="control-workspace">
    <div className={styles.drivingSurface} data-testid="driving-surface" data-view={view}>
    <div className={styles.controlModeRow}>
      <div className={styles.controlModes} role="group" aria-label={t("workspaceView")}>
        {(["manual", "automatic", "journey"] as const).map(mode => <Button key={mode} data-testid={"workspace-" + mode} aria-pressed={view === mode} tone={view === mode ? "primary" : undefined} onClick={() => setView(mode)}>{t(mode === "manual" ? "workspaceManual" : mode === "journey" ? "workspaceJourney" : "workspaceAutomatic")}</Button>)}
      </div>
      <div className={styles.commandAccess}>
        <Button data-testid="workspace-settings-toggle" aria-expanded={detailsOpen} aria-controls="rider-workspace-details" onClick={()=>{if(workspaceDetailsRef.current){workspaceDetailsRef.current.open=!workspaceDetailsRef.current.open;if(workspaceDetailsRef.current.open)workspaceDetailsRef.current.scrollIntoView({block:"nearest",behavior:"smooth"});}}}>{t("cockpitDetails")}</Button>
      </div>
    </div>
    <div className={styles.commandDeck} data-testid="command-deck" hidden={view === "manual"}>
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
      <Button tone="primary" size="large" data-testid="loop-start" title={t("trialSimpleHelp")} aria-describedby="rider-trial-help" disabled={!!trialUnavailable||!operatorApply.valid||operatorDraft.modelPending&&!operatorDraft.modelReady||!!operation||!!client.busy||loading||automaticActive||journey.active} onClick={()=>void runTrial()}><Play size={18}/>{t(dirty?"operator.applyStart":priority&&loopState.operator_control_version===1&&loopState.running===true?"cockpitResume":"cockpitStartTrial")}</Button>
      <Button tone="caution" size="large" data-testid="loop-stop" title={t(priority ? "operatorPauseHint" : "loopStop")} disabled={!connected||operation==="stop"||!priority&&operation==="save"} onClick={()=>void stop()}><Square size={18}/>{t(operation==="stop"?"loopStopping":priority?"cockpitPause":"cockpitStop")}</Button>
      {detector.drive_supported!==false&&<span className={styles.small} data-testid="trial-plan">{String(policy?.speed??"—")} SDK · ≤ {String(policy?.max_motion_ms??"—")} ms</span>}
    </div>
    </div>
          </Panel>
        </div>
        <div hidden={view !== "journey"} data-testid="journey-commands">
          <Panel title={t("journeyTitle")}>
            <div className={styles.loopStatus} role="status" data-testid="journey-state">
              {journey.historical&&<span className={styles.small} data-testid="journey-last-outcome">{t("journeyLastOutcome")}</span>}
              <Pill tone={journey.active&&journey.phase!=="unknown"?"good":journey.phase==="blocked"||journey.phase==="failed"||journey.phase==="unknown"?"warning":undefined}>{t("journey.state."+journey.phase)}</Pill>
              <span className={styles.small}>{t("journeyElapsed")}: {formatNumber(journey.elapsed,language,1)} s · {t("journeyManeuver")}: {journey.maneuver??"—"}</span>
            </div>
            <div className={styles.loopActions}>
              <Button tone="primary" size="large" data-testid="journey-start" disabled={journeyUnavailable.length>0||!operatorApply.valid||operatorDraft.modelPending&&!operatorDraft.modelReady||!!operation||!!client.busy||loading||journey.active||automaticActive} onClick={()=>void runJourney()}><Play size={18}/>{t(dirty?"operator.applyStart":"cockpitStartJourney")}</Button>
              <Button tone="caution" size="large" data-testid="journey-stop" disabled={!connected||operation==="stop"} onClick={()=>void stopJourney()}><Square size={18}/>{t("cockpitStopJourney")}</Button>
              <span className={styles.small} data-testid="journey-motion-limits">{String(policy?.speed??"—")} SDK · ≤ {String(policy?.max_motion_ms??"—")} ms</span>
            </div>
          </Panel>
        </div>
      <div className={styles.commandReason} role="status" data-testid="command-reason">
        <span>{commandReason}</span>
        {prepareAction&&!dirty&&!(view==="journey"&&confirmJourney)&&<Button data-testid="prepare-navigation" disabled={!connected||!!operation||loading||!!observeBlock||goals.drive===true} onClick={()=>{if(!acceptanceBlock)void acceptTrial();else showPreparation();}}>{t(!acceptanceBlock?"workspace.confirm":active?"workspace.preparation":"workspace.prepare")}</Button>}
      </div>

    </div>
    {!settingsError&&!journeyError&&!visibleError&&<OperatorMessages client={client} t={t} authority={view==="manual"}/>}
    <div className={styles.driveWorkspace}>
      <div className={styles.workspaceContent} data-testid="workspace-content" role="region" aria-label={t("workspaceObservation")}>
        <div className={styles.loopCamera}><CameraPanel client={client} t={t} language={language} compact showTools={false} editor={editingZone && draft && Array.isArray(draft.corridor) ? {value:draft.corridor as number[],disabled:settingsLocked,onChange:value=>setDraft({...draft,corridor:value})}:undefined}/></div>
      </div>
      <aside className={styles.tuningRail} aria-label={t("operator.tuning")} data-testid="operator-tuning">
        <div className={styles.tuningTabs} role="tablist" aria-label={t("operator.tuning")}>{(["motion","model"] as const).map(pane=><Button key={pane} role="tab" aria-selected={operatorDraft.pane===pane} data-testid={"tuning-tab-"+pane} tone={operatorDraft.pane===pane?"primary":undefined} onClick={()=>setOperatorDraft(previous=>({...previous,pane}))}>{t("operator."+pane)}</Button>)}</div>
        <div ref={railRef} className={styles.tuningBody}>
          <div hidden={operatorDraft.pane!=="motion"} data-testid="operator-motion-pane">
            <div hidden={view!=="manual"}>{(safety.owner!=="manual"&&safety.state==="ARMED")&&<Button data-testid="takeover-manual" disabled={!connected||!!client.busy} onClick={()=>void client.takeManual(t("manual"))}>{t("takeManual")}</Button>}<ManualPad client={client} t={t} language={language} compact minimal/></div>
            {view==="manual"&&studyRecording&&<PulseTrialsPanel client={client} t={t} recording={studyRecording}/>}
            {view!=="manual"&&draft&&<div data-testid="automatic-motion-settings">        <div className={styles.grid}>
          <Field label={t(view==="journey"?"loopJourneySpeed":"loopSpeed",{limit:String(navigationSpeedLimit(policy,view==="journey"))})}><Input data-testid="automatic-speed" disabled={settingsLocked} type="number" min="0.01" max={navigationSpeedLimit(policy,view==="journey")} step="0.01" value={Number(draft.speed)} onChange={e=>setDraft({...draft,speed:Number(e.target.value)})}/></Field>
          <Field label={t("loopBudget")}><Input disabled={settingsLocked} type="number" min="100" max="500" step="10" value={Number(draft.decision_budget_ms)} onChange={e=>setDraft({...draft,decision_budget_ms:Number(e.target.value)})}/></Field>
          <Field label={t("loopPulseLimit",{limit:String(navigationPulseLimit(policy,view==="journey"))})}><Input data-testid="automatic-pulse" disabled={settingsLocked} type="number" min="50" max={navigationPulseLimit(policy,view==="journey")} step="10" value={Number(draft.max_motion_ms)} onChange={e=>setDraft({...draft,max_motion_ms:Number(e.target.value)})}/></Field>
        </div>
<div className={styles.grid}>          <Field label={t("avoidanceTurnSpeed")+` (0.01–${navigationSpeedLimit(policy,false,true)})`}><Input type="number" data-testid="automatic-turn-speed" disabled={settingsLocked} min=".01" max={navigationSpeedLimit(policy,false,true)} step=".01" value={Number(record(draft?.avoidance).turn_speed)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),turn_speed:Number(e.target.value),sweep_verified:false}})}/></Field>
          <Field label={t("avoidanceTurnMs")+` (50–${navigationTurnPulseLimit(policy)} ms)`}><Input type="number" data-testid="automatic-turn-pulse" disabled={settingsLocked} min="50" max={navigationTurnPulseLimit(policy)} step="10" value={Number(record(draft?.avoidance).turn_ms)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),turn_ms:Number(e.target.value),sweep_verified:false}})}/></Field></div></div>}
            <OperatorMotion client={client} t={t} manual={view==="manual"} locked={!!operation}/>{view==="manual"&&<OperatorStatus client={client} t={t} language={language}/>}
          </div>
          <div hidden={operatorDraft.pane!=="model"} data-testid="operator-model-pane"><PerceptionModelsPanel beforeApply={()=>operatorApply.apply(false,true)} embedded client={client} t={t} language={language} policy={policy} goals={goals} locked={!!operation||loading} onApplied={applyPolicy} onRefresh={refreshPerception}/><ModelTuning editor={{draft:draft??policy,onChange:setDraft}} key={"tuning-"+session} client={client} t={t} policy={policy} goals={goals} locked={!!operation||loading} onApplied={applyPolicy} onRefresh={refreshPerception} onPendingChange={setProfileBusy}/><NavigationProfiles editor={{draft:draft??policy,onChange:setDraft}} key={session} client={client} t={t} policy={policy} goals={goals} locked={!!operation||loading} onApplied={applyPolicy} onRefresh={refreshPerception} onPendingChange={setProfileBusy}/></div>
        </div>
        <div className={styles.tuningFooter}>
          <Button tone="primary" data-testid="settings-save" disabled={!connected||!!operation||operatorApply.busy||!operatorApply.valid||operatorDraft.modelPending&&!operatorDraft.modelReady||!dirty} onClick={()=>void save().catch(()=>undefined)}>{t(safety.state==="ARMED"||safety.moving===true||goals.drive===true?"operator.stopApply":"operator.apply")}</Button>
          <Button data-testid="operator-discard" disabled={!dirty||!!operation||operatorApply.busy} onClick={()=>{applyPolicy(profileSnapshot.policy??policy);setOperatorDraft(previous=>({...previous,manual:null,modelChoice:null,modelPending:false}));}}>{t("operator.discard")}</Button>
          {settingsError&&<p className={styles.loopError} role="alert" data-testid="settings-save-error">{settingsError}</p>}
          {savedRevision!==null&&!dirty&&<span className={styles.small} data-testid="settings-save-success">✓ {String(savedRevision)}</span>}
        </div>
      </aside>
    </div>
    </div>
    <div className={styles.workspaceSecondary} data-testid="workspace-secondary">
      <details ref={preparationRef} className={styles.workspaceDisclosure} data-testid="navigation-preparation">
        <summary><span>{t("workspace.preparation")}</span><span>{t(!policy?"loading":detector.drive_supported===false?"trialAnalysisMode":needsAcceptance?"workspace.acceptancePending":"workspace.acceptanceReady")}</span></summary>
        <div className={styles.disclosureBody}>
          <div className={styles.preparationActions} data-testid="loop-observation">
            <span>{t("workspace.observationStep")}</span>
            <Button data-testid="loop-preview" disabled={!connected||!!operation||loading||!!observeBlock||active&&loopState.running===true} onClick={()=>void observePreview()}><Play size={14}/>{t(active&&loopState.running===true?"analysisRunning":"startObservation")}</Button>
          </div>
          {!!observeBlock&&<p className={styles.hint}>{t(observeBlock)}</p>}
          <div id="rider-trial-help" data-testid="trial-start-help" className={styles.hint}>{t("workspace.acceptanceStep")}</div>
    {policy&&(!calibrationMatches(policy,detector)||view==="journey"&&record(policy.avoidance).sweep_verified!==true)&&detector.drive_supported!==false&&<div className={styles.stack} data-testid="trial-acceptance">
      <p className={styles.hint} data-testid="calibration-acceptance-help">{t(view==="journey"?"journeyAcceptanceHelp":"trialAcceptanceHelp")}</p>
      {!!acceptanceBlock&&<p className={styles.hint} role="status" data-testid="calibration-preview-block">{t(acceptanceBlock)}</p>}
      <Button data-testid="trial-accept" disabled={!connected||!!operation||loading||!!acceptanceBlock||goals.drive===true} onClick={()=>void acceptTrial()}>{t(view==="journey"?"journeyAccept":"trialAccept")}</Button>
    </div>}

          {trialUnavailable&&trialUnavailable!=="loopUnverified"&&<p id="rider-trial-block" className={styles.hint} data-testid="loop-block">{t(trialUnavailable)}</p>}
          {!needsAcceptance&&policy&&<p role="status" data-testid={view==="journey"?"journey-acceptance-saved":"trial-acceptance-saved"}><Pill tone="good">{t("workspace.acceptanceRevision",{revision:String(policy.revision)})}</Pill></p>}
        </div>
      </details>
      <CameraDetails client={client} t={t} language={language}/>
      {recording&&<details className={styles.workspaceDisclosure} data-testid="recording-tools"><summary>{t("workspace.recording")}</summary><div className={styles.disclosureBody}>{recording}</div></details>}
        <details id="rider-workspace-details" ref={workspaceDetailsRef} onToggle={event=>setDetailsOpen(event.currentTarget.open)} className={styles.workspaceDetails} data-testid="workspace-details">
          <summary>{t("cockpitDetails")}</summary>
          <div className={styles.workspaceDetailsBody}>
            <div hidden={view !== "journey"}>
            {journeyUnavailable.length>0&&<div data-testid="journey-blocks" className={styles.loopError} role="status"><ul>{journeyUnavailable.map(block=><li key={block}>{journeyBlockLabel(block)}</li>)}</ul><Button onClick={prepareAction?showPreparation:showJourneySettings}>{t(prepareAction?"workspace.prepare":"journeySettings")}</Button></div>}
            {journey.reason&&<p className={styles.hint} data-testid="journey-reason">{t("state")}: <code>{journey.reason}</code></p>}
            {journeyRequested&&journey.active&&<p className={styles.hint}>{t("journeyStarted")}</p>}
            {journeyError&&<p className={styles.loopError} role="alert" data-testid="journey-error">{t("journeyRequestError")} · {journeyError}</p>}
            </div>
        <div data-testid="automatic-panel">
          <Panel title={t("loopSettings")}>
    {policy&&!groundActive&&<div className={styles.loopModeRow} data-testid="stopping-zone">
      <Field label={t("stopZone")}><Select aria-label={t("stopZone")} data-testid="loop-zone" value={stoppingZone(draft??policy)} disabled={!connected||loading||!!operation} onChange={e=>{if(e.target.value==="near"||e.target.value==="far")void changeStoppingZone(e.target.value);}}>
        <option value="near" disabled={!stoppingZoneValues(draft??policy,"near")}>{t("stopZoneNear")}</option>
        <option value="far" disabled={!stoppingZoneValues(draft??policy,"far")}>{t("stopZoneFar")}</option>
        {stoppingZone(draft??policy)==="custom"&&<option value="custom" disabled>{t("stopZoneCustom")}</option>}
      </Select></Field>
      <p className={styles.hint}>{t(operation==="zone"?"stopZoneUpdating":"stopZoneHelp")}</p>
    </div>}
    <div hidden={view !== "automatic"}>
    {preparation&&!diagnostic.combined&&<div className={preparation.state==="failed"?styles.loopError:styles.metric} role={preparation.state==="failed"?"alert":"status"} data-testid="trial-preparation" data-request-id={preparation.requestId}>
      <strong>{t(trialPreparationTitle(preparation))} · {t("trialPreparation.phase."+preparation.phase)}</strong>
      <p className={styles.small}>{t("trialPreparationElapsed",{seconds:formatNumber(preparation.elapsedMs===null?null:preparation.elapsedMs/1000,language,1)})}</p>
      <p className={styles.small}>{t(preparation.source==="pi"?"trialPreparationSource":"trialPreparationResponse")} · {formatTime(preparation.observedAt,language)}{!preparation.fresh?" · "+t("trialPreparationStale"):""}</p>
      {preparation.error&&<p>{t(trialPreparationErrorKey(preparation))}</p>}
    </div>}
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
      {!groundActive&&<Button data-testid="zone-edit" disabled={!draft||!!operation} onClick={()=>setEditingZone(!editingZone)}>{t(editingZone?"zoneFinish":"zoneEdit")}</Button>}
      {editingZone&&!groundActive&&<><Button disabled={!!operation} onClick={()=>draft&&setDraft({...draft,corridor:[.35,.80,.65,.95]})}>{t("zoneReset")}</Button></>}
    </div>
    {editingZone&&!groundActive&&<div className={styles.flex}><p className={styles.hint}>{t("zoneDraftHelp")}</p><Button onClick={showModelTuning}>{t("tuning.title")}</Button></div>}
    {groundActive&&<p className={styles.hint} data-testid="ground-geometry-help">{t("geometryPreviewHelp")}</p>}
    <section ref={settingsRef} className={styles.details} data-testid="advanced-motion-settings"><h3>{t("loopSettings")}</h3>
      <div className={styles.metrics}>
      <div className={styles.metric}><div className={styles.metricLabel}>{t("loopDecision")}</div><strong>{decision.state?t("vision."+String(decision.state)):"—"}</strong><p className={styles.small}>{String(decision.reason??loopState.reason??loopState.stop_reason??"")}</p></div>
      <div className={styles.metric}><div className={styles.metricLabel}>{t("loopLastFrame")}</div><strong>{formatTime(loopState.observed_at,language)}</strong><p className={styles.small}>{String(decision.inference_ms??"—")} ms · {t("loopLoaded")}: {t(detector.loaded===true?"yes":"no")}</p></div>
    </div>
      {draft?<div className={styles.stack}>

          {!groundDraft&&<Field label={t("loopCorridor")}><Input disabled={settingsLocked} value={Array.isArray(draft.corridor)?draft.corridor.join(","):""} onChange={e=>setDraft({...draft,corridor:e.target.value.split(",").map(Number)})}/></Field>}
        {"perception" in draft?<div className={styles.grid} data-testid="perception-settings">
          <p className={styles.hint}>{t("perceptionMethod")}: {t(perception.engine==="depth_plane"?"perceptionGeometry":"perceptionSemantic")}</p>
          <Button data-testid="open-model-tuning" onClick={showModelTuning}>{t("tuning.title")}</Button>
        </div>:<p className={styles.hint}>{t("perceptionPiUpdate")}</p>}
        {record(draft?.avoidance).protocol_version===3&&<fieldset className={styles.stack} disabled={settingsLocked} data-testid="ground-geometry-settings"><legend>{t("geometrySettings")}</legend>
          <Field label={t("geometryMode")}><Select data-testid="geometry-mode" value={String(record(record(draft?.avoidance).geometry).mode??"image")} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),geometry:{...record(record(draft.avoidance).geometry),mode:e.target.value},sweep_verified:false}})}><option value="ground">{t("geometryGround")}</option><option value="image">{t("geometryImage")}</option></Select></Field>
          <p className={styles.hint} data-testid="geometry-mode-help">{t(record(record(draft?.avoidance).geometry).mode==="ground"?"geometryCalibrationHelp":"geometryImageHelp")}</p>
          {record(record(draft?.avoidance).geometry).mode==="ground"&&<><p className={styles.hint}>{t("geometryHelp")}</p>{(["fx","fy","cx","cy","radius_ratio","lookahead_ratio","max_heading_deg"] as const).map(key=><Field key={key} label={t("geometry."+key)+` (${geometryRanges[key][0]}–${geometryRanges[key][1]})`}><Input type="number" min={geometryRanges[key][0]} max={geometryRanges[key][1]} step=".01" value={record(record(draft?.avoidance).geometry)[key]===null?"":Number(record(record(draft?.avoidance).geometry)[key])} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),geometry:{...record(record(draft.avoidance).geometry),[key]:e.target.value===""?null:Number(e.target.value)},sweep_verified:false}})}/></Field>)}</>}
        </fieldset>}
        {"avoidance" in (draft??{})&&<fieldset disabled={settingsLocked} className={styles.stack} data-testid="avoidance-settings"><legend>{t("avoidanceSettings")}</legend>{!avoidanceSupported&&<p>{t("avoidanceUpdateRequired")}</p>}
          <label className={styles.check}><Checkbox type="checkbox" disabled={!avoidanceSupported} checked={record(draft?.avoidance).enabled===true} onChange={e=>draft&&setDraft({...draft,mode:"observe",avoidance:{...record(draft.avoidance),enabled:e.target.checked,protocol_version:3,geometry:record(draft.avoidance).geometry??{mode:"ground",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45},max_stalled_corrections:record(draft.avoidance).max_stalled_corrections??2,progress_min_delta:record(draft.avoidance).progress_min_delta??.03,sweep_verified:false}})}/>{t("avoidanceEnabled")}</label>
          <label className={styles.check}><Checkbox type="checkbox" checked={record(draft?.avoidance).mirrored===true} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),mirrored:e.target.checked,sweep_verified:false}})}/>{t("avoidanceMirrored")}</label>
          {view!=="journey"&&<label className={styles.check}><Checkbox type="checkbox" checked={record(draft?.avoidance).sweep_verified===true} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),sweep_verified:e.target.checked}})}/>{t("avoidanceSweepAcceptance")}</label>}
          {view==="journey"&&<p className={styles.hint}>{t(record(policy?.avoidance).sweep_verified===true?"journeySweepSaved":"journeySweepHelp")}</p>}
          <Field label={t("avoidanceStalled")+" (1–5)"}><Input type="number" min="1" max="5" step="1" value={Number(record(draft?.avoidance).max_stalled_corrections??2)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),max_stalled_corrections:Number(e.target.value),sweep_verified:false}})}/></Field>
          <Field label={t("avoidanceProgress")+" (0–0.3)"}><Input type="number" min="0" max=".3" step=".01" value={Number(record(draft?.avoidance).progress_min_delta??.03)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),progress_min_delta:Number(e.target.value),sweep_verified:false}})}/></Field>
          <Field label={t("avoidanceMaxTurns")+" (1–5)"}><Input type="number" min="1" max="5" step="1" value={Number(record(draft?.avoidance).max_turn_pulses)} onChange={e=>draft&&setDraft({...draft,avoidance:{...record(draft.avoidance),max_turn_pulses:Number(e.target.value),sweep_verified:false}})}/></Field><p className={styles.hint}>{t(view==="journey"?"journeyAvoidanceHelp":"avoidanceHelp")}</p>
        </fieldset>}
        <p className={styles.hint}>{t("loopModel")}: {t(detector.configured===true?"yes":"no")} · {t("computeRevision")}: {String(policy?.revision??"—")}</p>
      </div>:<p>{t(loading?"loading":"loopLoadFailed")}</p>}
    </section>
          <LoopDecision frame={client.cameraStream==="venom"?client.camera:null} state={loopState} detector={detector} policy={policy} status={client.status} t={t} language={language}/>
          </Panel>
        </div>
        <div data-testid="operator-console">{operatorDetails}</div>
          </div>
        </details>
    </div>
  </section>;
}
