import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { data, errorCode, record, safetyOf, type JsonRecord } from "../lib/model";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Field, Panel } from "./primitives";
import styles from "../rider.module.css";

export function NavigationLoop({client,t}:{client:RiderClient;t:Translator}) {
  const [policy,setPolicy]=useState<JsonRecord|null>(null);
  const [detector,setDetector]=useState<JsonRecord>({});
  const [loopState,setLoopState]=useState<JsonRecord>({});
  const [runtime,setRuntime]=useState<JsonRecord>({});
  const [pending,setPending]=useState(false);
  const [error,setError]=useState("");
  const [plan,setPlan]=useState("");
  const policyLoaded=policy!==null;
  useEffect(()=>{
    if(!policyLoaded)return;
    const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const refresh=async()=>{
      if(!document.hidden) {
        try {
          const row=await apiFetch<JsonRecord>("/api/v1/rider/navigation/state",{signal:abort.signal});
          if(!abort.signal.aborted){setLoopState(record(row.status));setDetector(record(row.detector));}
        } catch {if(!abort.signal.aborted)setLoopState({stale:true});}
      }
      if(!abort.signal.aborted)timer=setTimeout(()=>void refresh(),2000);
    };
    void refresh();
    return ()=>{abort.abort();clearTimeout(timer);};
  },[policyLoaded]);
  const load=async()=>{
    setPending(true);setError("");
    try {
      const result=await apiFetch<JsonRecord>("/api/v1/rider/navigation/config");
      setPolicy(record(result.config));setDetector(record(result.detector));setLoopState(record(result.status));setRuntime(record(result.runtime));
    } catch (err) {setPolicy(null);setError(t(errorCode(err)==="navigation_api_unavailable"?"loopApiMissing":"loopLoadFailed"));} finally {setPending(false);}
  };
  const save=async()=>{
    if(!policy)return;
    setPending(true);setError("");
    try {
      const {revision,protocol_version:ignored,...values}=policy;
      void ignored;
      const result=await client.mutate("/navigation/config",{revision,values});
      setPolicy(record(record(data(result)).config));
    } catch {setError(t("loopSaveFailed"));} finally {setPending(false);}
  };
  return <Panel title={t("loopTitle")} hint={t("loopHint")}>
    <Button disabled={!client.canMutate||pending} onClick={()=>void load()}>{t("loopLoad")}</Button>
    {policy&&<div className={styles.stack}>
      <Field label={t("loopMode")}><select value={String(policy.mode)} onChange={e=>setPolicy({...policy,mode:e.target.value})}>
        <option value="observe">{t("loopObserve")}</option><option value="drive">{t("loopDrive")}</option>
      </select></Field>
      <Field label={t("loopSpeed")}><input type="number" min="0.01" max="0.15" step="0.01" value={Number(policy.speed)} onChange={e=>setPolicy({...policy,speed:Number(e.target.value)})}/></Field>
      <Field label={t("loopBudget")}><input type="number" min="100" max="500" step="10" value={Number(policy.decision_budget_ms)} onChange={e=>setPolicy({...policy,decision_budget_ms:Number(e.target.value)})}/></Field>
      <Field label={t("loopPulse")}><input type="number" min="50" max="200" step="10" value={Number(policy.max_motion_ms)} onChange={e=>setPolicy({...policy,max_motion_ms:Number(e.target.value)})}/></Field>
      <Field label={t("loopCorridor")}><input value={Array.isArray(policy.corridor)?policy.corridor.join(","):""} onChange={e=>setPolicy({...policy,corridor:e.target.value.split(",").map(Number)})}/></Field>
      <Field label={t("loopCalibration")}><input maxLength={64} value={String(policy.calibration_id??"")} onChange={e=>setPolicy({...policy,calibration_id:e.target.value})}/></Field>
      <Field label={t("loopHash")}><input maxLength={64} value={String(policy.model_sha256??"")} onChange={e=>setPolicy({...policy,model_sha256:e.target.value})}/></Field>
      <label><input type="checkbox" checked={policy.verified===true} onChange={e=>setPolicy({...policy,verified:e.target.checked})}/>{t("loopVerified")}</label>
      <Button disabled={!client.canMutate||pending} onClick={()=>void save()}>{t("loopSave")}</Button>
      <p>{t("state")}: {loopState.stale===true?t("unknown"):t(loopState.running===true?"active":"inactive")} · <code>{String(loopState.stop_reason??"")}</code></p>
      {!!loopState.decision&&<p>{t("state")}: {t("vision."+String(record(loopState.decision).state??"unknown"))} · {String(record(loopState.decision).inference_ms??"—")} ms · <code>{String(record(loopState.decision).reason??"")}</code></p>}
      <code>{String(detector.sha256??"")}</code>
      <p>{t("loopModel")}: {detector.configured===true?t("yes"):t("no")} · {t("loopLoaded")}: {detector.loaded===true?t("yes"):t("no")}</p>
      {(runtime.observe!==true||runtime.drive!==true)&&<p role="status" className={styles.hint}>{t(runtime.drive_reason==="navigation_executor_update_required"?"loopExecutorUpdate":runtime.known===true?"loopRuntimeMissing":"loopRuntimeUnknown")}</p>}
      <p className={styles.hint}>{t("loopRunHint")}</p>
      <Field label={t("loopPlan")}><input maxLength={512} value={plan} onChange={e=>setPlan(e.target.value)}/></Field>
      <div className={styles.flex}>
        <Button disabled={runtime.drive!==true||!client.canArm||pending||!plan.trim()||policy.mode!=="drive"||policy.verified!==true} onClick={()=>void client.command({action:"arm",owner:"navigation",arming_mode:"timed",lease_s:10,operator_consent:true,operator_plan:plan})}>{t("loopArm")}</Button>
        <Button disabled={runtime[String(policy.mode)]!==true||detector.configured!==true||!client.canMutate||pending||!!client.busy||(policy.mode==="drive"&&safetyOf(client.status).owner!=="navigation")} onClick={()=>void client.mutate(policy.mode==="drive"?"/goal/s14_venom_navigation":"/goal/s15_venom_perception",{enabled:true}).catch(()=>setError(t("requestFailed")))}>{t("loopStart")}</Button>
        <Button disabled={pending} onClick={()=>void Promise.all([client.mutate("/goal/s14_venom_navigation",{enabled:false}),client.mutate("/goal/s15_venom_perception",{enabled:false})]).catch(()=>setError(t("requestFailed")))}>{t("loopStop")}</Button>
      </div>
    </div>}
    {error&&<p role="alert">{error}</p>}
  </Panel>;
}
