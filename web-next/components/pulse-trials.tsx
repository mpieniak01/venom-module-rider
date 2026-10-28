import {useEffect,useRef,useState} from "react";
import {apiFetch} from "@/lib/api-client";
import {createBrowserUuid} from "@/lib/optional-modules/transport";
import {calibrationDraft,calibrationValues} from "../lib/calibration";
import { str, scalarText,data,errorCode,record,safetyOf,settingsOf,type JsonRecord} from "../lib/model";
import {useOperatorTuning} from "../lib/operator-tuning";
import {useOperatorApply} from "../lib/use-operator-apply";
import {matchingPulse,pulseActive,pulseProfiles,pulseRun,type PulseProfile} from "../lib/pulse-trials";
import type {StudyRecording} from "../lib/use-study-recording";
import type {RiderClient} from "../lib/use-rider";
import type {Translator} from "../lib/i18n";
import {Button,Field,Input,Select} from "./primitives";
import styles from "../rider.module.css";

/** Coordinate one explicit pulse run and its owned evidence recording. */
export function PulseTrialsPanel({client,t,recording}:{client:RiderClient;t:Translator;recording:StudyRecording}){
 const workspace=useOperatorTuning(),{state,setState}=workspace,apply=useOperatorApply(workspace,client);
 const [profiles,setProfiles]=useState<PulseProfile[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const [run,setRun]=useState<JsonRecord|null>(null),[revision,setRevision]=useState(0);
 const mounted=useRef(true),claimed=useRef(false),recordingOwned=useRef<string|null>(null);
 const recordingSession=useRef(recording.capture.session);recordingSession.current=recording.capture.session;
 const current=client.currentSession(),endRecording=recording.capture.end;
 const settings=settingsOf(client.status),effective=state.manual??calibrationDraft(settings),chosen=profiles.find(p=>p.id===state.trialProfile);
 const matches=matchingPulse(chosen,effective.directions);
 const live=pulseRun(record(safetyOf(client.status).pulse_trials).current);
 const trialVersion=record(safetyOf(client.status).pulse_trials).version;
 const active=pulseActive(run)||safetyOf(client.status).stale===false&&pulseActive(live);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 useEffect(()=>{
  let cancelled=false;setProfiles([]);
  if(client.canMutate)void apiFetch("/api/v1/rider/motion/trials/profiles").then(row=>{if(!cancelled)setProfiles(pulseProfiles(row));}).catch(()=>undefined);
  return()=>{cancelled=true;};
 },[current,client.canMutate,trialVersion,client.refreshing]);
 useEffect(()=>{
  if(!state.trialRunId||!client.canMutate)return;
  let cancelled=false,timer:ReturnType<typeof setTimeout>|undefined;const deadline=Date.now()+5000;
  /** Finish only the recording still owned by this pulse run. */
  const finishRecording=async(aborted=false)=>{
    const owned=recordingOwned.current;recordingOwned.current=null;
    if(recordingSession.current?.session_id===owned)await endRecording(aborted);
  };
  /** Observe one pulse result until completion or the existing deadline. */
  const tick=async()=>{
   try{
    const value=data(await apiFetch("/api/v1/rider/motion/trials/"+encodeURIComponent(state.trialRunId))),next=pulseRun(value.trial);
    if(cancelled)return;
    if(next){setRun(next);setError("");if(!pulseActive(next)){if(recordingOwned.current){await finishRecording();}return;}}
   }catch(err){if(!cancelled)setError(previous=>previous||errorCode(err));}
   if(cancelled)return;
   if(Date.now()>=deadline){setRun(null);if(recordingOwned.current){await finishRecording(true);}return;}
   if(!cancelled)timer=setTimeout(()=>void tick(),200);
  };
  void tick();return()=>{cancelled=true;clearTimeout(timer);};
 },[state.trialRunId,current,client.canMutate,endRecording,revision]);
 const choose=(id:string)=>{
  const profile=profiles.find(row=>row.id===id);setError("");
  setState(previous=>{
   const saved=calibrationDraft(settings),old=previous.manual;
   const alreadySaved=old&&JSON.stringify(old.directions)===JSON.stringify(saved.directions)&&JSON.stringify(calibrationValues(old,settings))===JSON.stringify(calibrationValues(saved,settings));
   const base=alreadySaved?saved:old??saved;
   return {...previous,trialProfile:profile?.id??null,direction:"forward",...(profile?{manual:{...base,directions:{...base.directions,forward:{...base.directions.forward,duration:profile.pulse_ms/1000}}}}:{})};
  });
 };
 const start=async()=>{
  if(!chosen||!matches||claimed.current||busy||active)return;
  claimed.current=true;setBusy(true);setError("");
  const origin=client.currentSession();let token:{epoch:number;generation:string}|null=null;let submitted=false;
  const check=(receipt=false)=>{if(!mounted.current||document.hidden||client.currentSession()!==origin||token&&(client.currentMotionEpoch()!==token.epoch||!receipt&&client.currentGeneration()!==token.generation))throw new Error("navigation_trial_cancelled");};
  try{
   const prepared=await apply.apply(false,true,"manual");token=prepared.token;check();
   const revision=Number(client.currentManualSettings().revision);
   const collection=await recording.capture.start("manual",{recordVideo:false});if(collection)recordingOwned.current=collection.session_id;check();
   if(!collection)throw new Error("pulse_recording_unavailable");
   const id=createBrowserUuid();
   const plan=t("pulse.plan",{profile:chosen.id,count:chosen.count,pulse:chosen.pulse_ms,gap:chosen.gap_ms});
   try{await client.startPulseTrial(chosen.id,plan,recording.surface.trim(),id,revision,token.generation,()=>{submitted=true;});check(true);}
   finally{if(submitted&&mounted.current)setState(previous=>({...previous,trialRunId:id}));}
   setRevision(value=>value+1);
  }catch(err){if(mounted.current)setError(errorCode(err));if(!submitted&&recordingOwned.current){recordingOwned.current=null;await endRecording(true);}}
  finally{claimed.current=false;if(mounted.current)setBusy(false);}
 };
 if(!profiles.length)return null;
 return <section className={styles.pulseTrials} data-testid="pulse-trials">
  <h3>{t("pulse.title")}</h3>
  <Field label={t("pulse.profile")}><Select data-testid="pulse-profile" value={matches?chosen?.id??"":""} disabled={busy||state.busy} onChange={event=>choose(event.target.value)}><option value="">{t("custom")}</option>{profiles.map(p=><option key={p.id} value={p.id}>{p.id} · {p.count} × {p.pulse_ms} ms{p.gap_ms?` · ${p.gap_ms} ms`:""}</option>)}</Select></Field>
  <Field label={t("study.surface")}><Input data-testid="pulse-context" value={recording.surface} maxLength={80} disabled={busy||recording.capture.session?.state==="collecting"} onChange={event=>recording.setSurface(event.target.value)}/></Field>
  {matches&&chosen&&<p className={styles.small} data-testid="pulse-plan">{t("pulse.summary",{count:chosen.count,pulse:chosen.pulse_ms,gap:chosen.gap_ms,speed:scalarText(record(effective.directions.forward).speed)})}</p>}
  <div className={styles.flex}><Button tone="primary" data-testid="pulse-start" disabled={!matches||!client.canMutate||busy||state.busy||active||!!client.busy} onClick={()=>void start()}>{t(busy?"loading":"pulse.start")}</Button></div>
  {run&&<p className={styles.small} data-testid="pulse-result">{t("pulse.phase."+str(run.phase))} · {scalarText(run.completed_pulses)}/{scalarText(run.count)} · {scalarText(run.elapsed_ms)} ms</p>}
  {error&&<p className={styles.loopError} role="alert" data-testid="pulse-error">{t("requestFailed")} · {error}</p>}
 </section>;
}
