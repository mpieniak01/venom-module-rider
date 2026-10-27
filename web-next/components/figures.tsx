import {useEffect,useRef,useState} from "react";
import {apiFetch} from "@/lib/api-client";
import type {RiderClient} from "../lib/use-rider";
import type {Translator} from "../lib/i18n";
import {data,errorCode,record,safetyOf,str,type JsonRecord} from "../lib/model";
import {figureActive,figureCatalog,type FigureRecipe} from "../lib/figures";
import {Button,Field,Select} from "./primitives";
import styles from "../rider.module.css";

/** A selection is inert; only explicit Start sends the bounded own-recipe intent. */
export function FiguresPanel({client,t}:{client:RiderClient;t:Translator}){
 const supported=record(safetyOf(client.status).figures).version===1;
 const [recipes,setRecipes]=useState<FigureRecipe[]>([]),[selected,setSelected]=useState("sway");
 const [run,setRun]=useState<JsonRecord|null>(null),[runId,setRunId]=useState<string|null>(null),[failure,setFailure]=useState("");
 const [busy,setBusy]=useState(false);
 const owner=useRef<{session:string;plan:string;id:string}|null>(null);
 const claimed=useRef(false);
 const cancelledRun=useRef<string|null>(null);
 const starting=useRef<AbortController|null>(null);
 const mounted=useRef(false);
 const unobserved=useRef<{id:string;session:string}|null>(null);
 const currentSession=client.currentSession,keepFigure=client.keepFigure;
 const session=currentSession();
 const chosen=recipes.find(row=>row.id===selected);
 const active=figureActive(run);
 useEffect(()=>{
  mounted.current=true;
  const retire=()=>{
   starting.current?.abort();
   const pending=unobserved.current;unobserved.current=null;
   if(pending){
    cancelledRun.current=pending.id;
    void apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(pending.id)+"/cancel",{method:"POST",headers:{"X-Rider-Session":pending.session},body:"{}"}).catch(()=>undefined);
   }
  };
  const visibility=()=>{if(document.hidden)retire();};
  document.addEventListener("visibilitychange",visibility);
  return()=>{mounted.current=false;document.removeEventListener("visibilitychange",visibility);retire();};
 },[]);
 useEffect(()=>{
  let cancelled=false;setRecipes([]);
  if(supported&&client.canMutate)void apiFetch("/api/v1/rider/motion/figures").then(row=>{if(!cancelled)setRecipes(figureCatalog(data(row)));}).catch(error=>{if(!cancelled)setFailure(errorCode(error));});
  return()=>{cancelled=true;};
 },[supported,session,client.canMutate]);
 useEffect(()=>{
  if(!runId||!owner.current)return;
  if(unobserved.current?.id===runId)unobserved.current=null;
  const own=owner.current;let alive=true,running=true,timer:ReturnType<typeof setTimeout>|undefined;
  const cancel=()=>{running=false;cancelledRun.current=runId;clearTimeout(timer);return apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId)+"/cancel",{method:"POST",headers:{"X-Rider-Session":own.session},body:"{}"}).catch(()=>undefined);};
  const tick=async()=>{
   if(!running||cancelledRun.current===runId)return;
   if(!alive||document.hidden||currentSession()!==own.session){if(running)void cancel();return;}
   try{
    await keepFigure(runId,own.id,own.plan);
    if(!alive||!running||cancelledRun.current===runId)return;
    const response=data(await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId))),next=record(response.run);
    if(!alive||cancelledRun.current===runId)return;
    setRun(next);
    running=figureActive(next);
    if(!running)return;
   }catch(error){
    if(!alive||cancelledRun.current===runId)return;
    try{
     const next=record(data(await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId))).run);
     if(!alive||cancelledRun.current===runId)return;
     if(!figureActive(next)){setRun(next);running=false;return;}
    }catch{}
    if(!alive||cancelledRun.current===runId)return;
    setFailure(errorCode(error));running=false;void cancel();return;
   }
   if(alive&&running&&cancelledRun.current!==runId)timer=setTimeout(()=>void tick(),200);
  };
  const visibility=()=>{if(document.hidden){running=false;void cancel();clearTimeout(timer);}else{void apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId)).then(response=>{if(alive)setRun(record(data(response).run));}).catch(()=>undefined);}};
  document.addEventListener("visibilitychange",visibility);void tick();
  return()=>{alive=false;clearTimeout(timer);document.removeEventListener("visibilitychange",visibility);if(running)void cancel();};
 },[runId,keepFigure,currentSession]);
 const start=async()=>{
  if(!chosen||!chosen.available||claimed.current||active)return;
  claimed.current=true;setBusy(true);setFailure("");
  const controller=new AbortController(),origin=client.currentSession();starting.current=controller;
  const plan=t("figures.plan",{name:t("figures.name."+chosen.id),seconds:chosen.duration_ms/1000,height:chosen.initial_height??"—",roll:chosen.initial_roll??"—"});
  try{
   const id=await client.startFigure(chosen.id,plan,controller.signal);
   if(controller.signal.aborted||client.currentSession()!==origin){
    void apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(id)+"/cancel",{method:"POST",headers:{"X-Rider-Session":origin},body:"{}"}).catch(()=>undefined);
    return;
   }
   owner.current={session:origin,plan,id:chosen.id};unobserved.current={id,session:origin};
   setRun({phase:"preparing",step:0,step_count:0});setRunId(id);
  }catch(error){if(!controller.signal.aborted)setFailure(errorCode(error));}
  finally{if(starting.current===controller)starting.current=null;claimed.current=false;if(mounted.current)setBusy(false);}
 };
 const cancel=async()=>{
  if(!runId)return;
  cancelledRun.current=runId;
  setBusy(true);
  try{await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId)+"/cancel",{method:"POST",headers:{"X-Rider-Session":owner.current?.session??session},body:"{}"});const next=record(data(await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId))).run);setRun(next);}
  catch(error){setFailure(errorCode(error));}finally{setBusy(false);}
 };
 const exportRun=async()=>{
  if(!runId)return;
  try{
   const result=await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId)+"/export");
   const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));
   const link=document.createElement("a");link.href=url;link.download="figure-"+runId+".json";
   document.body.append(link);
   try{link.click();}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),0);}
  }catch(error){setFailure(errorCode(error));}
 };
 if(!supported)return null;
 return <section className={styles.figurePanel} data-testid="figures-panel">
  <h3>{t("figures.title")}</h3><p className={styles.small}>{t("figures.trial")}</p>
  <Field label={t("figures.select")}><Select data-testid="figure-select" value={selected} disabled={busy||active} onChange={event=>{setSelected(event.target.value);setFailure("");}}>{recipes.map(row=><option key={row.id} value={row.id}>{t("figures.name."+row.id)}</option>)}</Select></Field>
  {chosen&&<div className={styles.figureDescription} data-testid="figure-plan"><p>{t("figures.description."+chosen.id)}</p><span>{t("figures.duration",{seconds:chosen.duration_ms/1000})}</span>{chosen.initial_height!==null&&<span>{t("figures.height",{value:chosen.initial_height})}</span>}{chosen.initial_roll!==null&&<span>{t("figures.roll",{value:chosen.initial_roll})}</span>}<span>{t("figures.strength",{factor:chosen.intensity_factor*100,limit:chosen.maximum_speed})}</span></div>}
  <div className={styles.flex}><Button tone="primary" size="large" data-testid="figure-start" disabled={!chosen?.available||!client.canMutate||!client.controlFresh||busy||active||!!client.busy} onClick={()=>void start()}>{t("figures.start")}</Button><Button tone="danger" size="large" data-testid="figure-cancel" disabled={!active||busy} onClick={()=>void cancel()}>{t("figures.cancel")}</Button>{runId&&<Button data-testid="figure-export" onClick={()=>void exportRun()}>{t("figures.export")}</Button>}</div>
  <div className={styles.figureStatus} role="status" data-testid="figure-status">{failure?t("requestFailed")+" · "+failure:run?t("figures.phase."+str(run.phase))+" · "+String(run.step??0)+"/"+String(run.step_count??0):chosen&&!chosen.available?t("figures.unsupported"):t("figures.idle")}</div>
 </section>;
}
