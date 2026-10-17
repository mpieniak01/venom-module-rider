"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { AUTO_BASE, autoIntent, autoStatus, recoveredAutoEnable, defaultEventProfile, eventNames, type AutoStatus, type EventProfile } from "../lib/automatic";
import { errorCode, record, str } from "../lib/model";
import { SAMPLES_BASE, type Collection } from "../lib/samples";
import { getApiBaseUrl } from "@/lib/env";
import type { Translator } from "../lib/i18n";
import { Button, Checkbox, Input, Pill } from "./primitives";
import styles from "../rider.module.css";

const fields: [Exclude<keyof EventProfile,"events">,number,number,number][]=[
  ["stable_frames",1,20,1],["stable_ms",0,5000,50],["cooldown_ms",500,60000,100],
  ["scene_distance_bits",1,64,1],["burst_frames",1,5,1],["burst_interval_ms",150,2000,50],
  ["max_samples",1,150,1],["duration_s",1,1800,1],["max_gap_ms",500,5000,100],
];

export function AutomaticCapture({collection,sourceSession,t}:{collection:Collection|null;sourceSession:string|null;t:Translator}) {
  const [profile,setProfile]=useState<EventProfile>(()=>({...defaultEventProfile,events:[...defaultEventProfile.events]}));
  const [status,setStatus]=useState<AutoStatus>({state:"off"});
  const [events,setEvents]=useState<Record<string,unknown>[]>([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const epoch=useRef(0);
  const pendingEnable=useRef<string|null>(null);
  const invalidate=useCallback(()=>++epoch.current,[]);
  useEffect(()=>{
    invalidate();
    let stopped=false;let timer:ReturnType<typeof setTimeout>|undefined;
    const poll=async()=>{
      const version=epoch.current;
      try {
        const row=autoStatus(await apiFetch(AUTO_BASE));
        let items:Record<string,unknown>[]=[];
        if(row.id){const detail=await apiFetch<Record<string,unknown>>(AUTO_BASE+"/runs/"+row.id);items=Array.isArray(detail.events)?detail.events:[];}
        if(!stopped&&version===epoch.current){setStatus(row);setEvents(items);if(recoveredAutoEnable(row,pendingEnable.current)){pendingEnable.current=null;setError("");}}
      }catch(err){if(!stopped)setError(errorCode(err));}
      if(!stopped)timer=setTimeout(()=>void poll(),1000);
    };
    void poll();
    return()=>{stopped=true;invalidate();clearTimeout(timer);};
  },[collection?.session_id,sourceSession,invalidate]);
  const active=status.state==="on";
  const start=async()=>{
    const intent=autoIntent(collection?.session_id??null,sourceSession,profile,crypto.randomUUID());
    if(!intent)return;
    const version=++epoch.current;
    pendingEnable.current=intent.body.request_id;
    setBusy(true);setError("");
    try{const row=autoStatus(await apiFetch(AUTO_BASE,{method:"POST",headers:intent.headers,body:JSON.stringify(intent.body)}));if(version===epoch.current){setStatus(row);pendingEnable.current=null;}}
    catch(err){if(version===epoch.current&&pendingEnable.current===intent.body.request_id)setError(errorCode(err));}
    finally{if(version===epoch.current)setBusy(false);}
  };
  const stop=async()=>{
    const version=++epoch.current;
    setBusy(true);setError("");
    try{const row=autoStatus(await apiFetch(AUTO_BASE+"/stop",{method:"POST",body:"{}"}));if(version===epoch.current)setStatus(row);}
    catch(err){setError(errorCode(err));}
    finally{if(version===epoch.current)setBusy(false);}
  };
  return <section className={styles.sampleAutomation} data-testid="automatic-capture">
    <div className={styles.flex}><strong>{t("auto.title")}</strong><Pill tone={active?"good":undefined}>{t(active?"auto.on":"auto.off")}</Pill>
      {active?<Button data-testid="auto-stop" disabled={busy} onClick={()=>void stop()}>{t("auto.stop")}</Button>:
        <Button data-testid="auto-start" disabled={busy||!collection||!sourceSession||!profile.events.length} onClick={()=>void start()}>{t("auto.start")}</Button>}
    </div>
    <p className={styles.hint}>{t("auto.hint")}</p>
    {!collection&&<p className={styles.hint}>{t("auto.collectionRequired")}</p>}
    <details className={styles.details}><summary>{t("auto.profile")}</summary>
      <fieldset className={styles.autoSettings} disabled={active||busy}>
        <div className={styles.autoEvents}>{eventNames.map(name=><label className={styles.check} key={name}><Checkbox type="checkbox" checked={profile.events.includes(name)} onChange={e=>setProfile(p=>({...p,events:e.target.checked?[...p.events,name]:p.events.filter(n=>n!==name)}))}/>{t("auto.event."+name)}</label>)}</div>
        <div className={styles.grid}>{fields.map(([name,min,max,step])=><label className={styles.field} key={name}>{t("auto.field."+name)}<Input aria-label={t("auto.field."+name)} type="number" min={min} max={max} step={step} value={profile[name]} onChange={e=>setProfile(p=>({...p,[name]:Number(e.target.value)}))}/></label>)}</div>
      </fieldset>
      <p className={styles.hint}>{t("auto.noObjectDetector")}</p>
    </details>
    <p aria-live="polite" data-testid="auto-counts">{t("auto.counts",{saved:Number(status.saved??0),failed:Number(status.failed??0),skipped:Number(status.skipped??0),dropped:Number(status.dropped??0)})}</p>
    {status.reason&&<p className={styles.hint}>{t("auto.reason."+status.reason)}</p>}
    {error&&<p role="alert">{t("auto.failure")} · <code>{error}</code></p>}
    {events.length>0&&<details><summary>{t("auto.recent")}</summary><ul>{events.map(item=>{
      const body=record(item.body);const reasons=Array.isArray(body.reasons)?body.reasons.map(String):[];
      return <li key={str(item.id)}>{t("auto.outcome."+str(item.state))} · {reasons.map(r=>t("auto.event."+r)).join(", ")}
        {Boolean(item.sample_id)&&<a href={getApiBaseUrl()+SAMPLES_BASE+"/items/"+str(item.sample_id)+"/original"} target="_blank" rel="noreferrer">{t("auto.original")}</a>}
        {Boolean(item.error)&&<code> · {str(item.error)}</code>}
      </li>;
    })}</ul></details>}
  </section>;
}
