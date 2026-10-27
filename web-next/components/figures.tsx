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
 const currentSession=client.currentSession,keepFigure=client.keepFigure;
 const session=currentSession();
 const chosen=recipes.find(row=>row.id===selected);
 const active=figureActive(run);
 useEffect(()=>{
  let cancelled=false;setRecipes([]);
  if(supported&&client.canMutate)void apiFetch("/api/v1/rider/motion/figures").then(row=>{if(!cancelled)setRecipes(figureCatalog(data(row)));}).catch(error=>{if(!cancelled)setFailure(errorCode(error));});
  return()=>{cancelled=true;};
 },[supported,session,client.canMutate]);
 useEffect(()=>{
  if(!runId||!owner.current)return;
  const own=owner.current;let alive=true,running=true,timer:ReturnType<typeof setTimeout>|undefined;
  const cancel=()=>apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId)+"/cancel",{method:"POST",headers:{"X-Rider-Session":own.session},body:"{}"}).catch(()=>undefined);
  const tick=async()=>{
   if(!alive||document.hidden||currentSession()!==own.session){if(running)void cancel();return;}
   try{
    await keepFigure(runId,own.id,own.plan);
    const response=data(await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId))),next=record(response.run);
    if(!alive)return;
    setRun(next);
    running=figureActive(next);
    if(!running)return;
   }catch(error){
    if(!alive)return;
    try{const next=record(data(await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId))).run);if(!figureActive(next)){setRun(next);running=false;return;}}catch{}
    setFailure(errorCode(error));running=false;void cancel();return;
   }
   if(alive)timer=setTimeout(()=>void tick(),200);
  };
  const visibility=()=>{if(document.hidden){running=false;void cancel();clearTimeout(timer);}else{void apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId)).then(response=>{if(alive)setRun(record(data(response).run));}).catch(()=>undefined);}};
  document.addEventListener("visibilitychange",visibility);void tick();
  return()=>{alive=false;clearTimeout(timer);document.removeEventListener("visibilitychange",visibility);if(running)void cancel();};
 },[runId,keepFigure,currentSession]);
 const start=async()=>{
  if(!chosen||!chosen.available||claimed.current||active)return;
  claimed.current=true;setBusy(true);setFailure("");
  const plan=t("figures.plan",{name:t("figures.name."+chosen.id),seconds:chosen.duration_ms/1000,height:chosen.initial_height??"—",roll:chosen.initial_roll??"—"});
  try{
   const id=await client.startFigure(chosen.id,plan);
   owner.current={session:client.currentSession(),plan,id:chosen.id};
   setRun({phase:"preparing",step:0,step_count:0});setRunId(id);
  }catch(error){setFailure(errorCode(error));}
  finally{claimed.current=false;setBusy(false);}
 };
 const cancel=async()=>{
  if(!runId)return;
  setBusy(true);
  try{await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId)+"/cancel",{method:"POST",headers:{"X-Rider-Session":owner.current?.session??session},body:"{}"});const next=record(data(await apiFetch("/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId))).run);setRun(next);}
  catch(error){setFailure(errorCode(error));}finally{setBusy(false);}
 };
 if(!supported)return null;
 return <section className={styles.figurePanel} data-testid="figures-panel">
  <h3>{t("figures.title")}</h3><p className={styles.small}>{t("figures.trial")}</p>
  <Field label={t("figures.select")}><Select data-testid="figure-select" value={selected} disabled={busy||active} onChange={event=>{setSelected(event.target.value);setFailure("");}}>{recipes.map(row=><option key={row.id} value={row.id}>{t("figures.name."+row.id)}</option>)}</Select></Field>
  {chosen&&<div className={styles.figureDescription} data-testid="figure-plan"><p>{t("figures.description."+chosen.id)}</p><span>{t("figures.duration",{seconds:chosen.duration_ms/1000})}</span>{chosen.initial_height!==null&&<span>{t("figures.height",{value:chosen.initial_height})}</span>}{chosen.initial_roll!==null&&<span>{t("figures.roll",{value:chosen.initial_roll})}</span>}<span>{t("figures.strength",{factor:chosen.intensity_factor*100,limit:chosen.maximum_speed})}</span></div>}
  <div className={styles.flex}><Button tone="primary" size="large" data-testid="figure-start" disabled={!chosen?.available||!client.canMutate||!client.controlFresh||busy||active||!!client.busy} onClick={()=>void start()}>{t("figures.start")}</Button><Button tone="danger" size="large" data-testid="figure-cancel" disabled={!active||busy} onClick={()=>void cancel()}>{t("figures.cancel")}</Button>{runId&&<a href={"/api/v1/rider/motion/figures/runs/"+encodeURIComponent(runId)+"/export"} download data-testid="figure-export">{t("figures.export")}</a>}</div>
  <div className={styles.figureStatus} role="status" data-testid="figure-status">{failure?t("requestFailed")+" · "+failure:run?t("figures.phase."+str(run.phase))+" · "+String(run.step??0)+"/"+String(run.step_count??0):chosen&&!chosen.available?t("figures.unsupported"):t("figures.idle")}</div>
 </section>;
}
