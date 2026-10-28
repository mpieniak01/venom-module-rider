import {GeometryShapes,GEOMETRY_STROKE} from "./geometry-overlay";
import {readGeometry} from "../lib/overlay-geometry";
import {useEffect,useRef,useState,type PointerEvent} from "react";
import {apiFetch} from "@/lib/api-client";
import {Ruler} from "lucide-react";
import {imagePoint} from "../lib/corridor-editor";
import {bodyEdges,initialReference,referenceFitsFrame,moveReference,readReference,validReference,type ImageReference,type ReferenceState} from "../lib/image-reference";
import {data,errorCode} from "../lib/model";
import type {RiderClient} from "../lib/use-rider";
import type {Translator} from "../lib/i18n";
import {Button,Field,Input} from "./primitives";
import styles from "../rider.module.css";

export function useImageReference(session:string){
  const [state,setState]=useState<ReferenceState|null>(null),[draft,setDraft]=useState<ImageReference|null>(null),[editing,setEditing]=useState(false),[error,setError]=useState(""),[saving,setSaving]=useState(false);
  const [attempt,setAttempt]=useState(0);
  const owner=useRef(session);owner.current=session;
  useEffect(()=>{
    setState(null);setDraft(null);setEditing(false);setSaving(false);setError("");if(!session)return;
    const abort=new AbortController();
    void apiFetch("/api/v1/rider/navigation/image-reference",{signal:abort.signal}).then(result=>{if(abort.signal.aborted)return;const value=readReference(data(result));if(!value)throw new Error("invalid_reference");setState(value);}).catch(()=>{if(!abort.signal.aborted)setError("reference.unavailable");});
    return ()=>abort.abort();
  },[session,attempt]);
  const save=async(client:RiderClient)=>{
    if(!state||!draft||!validReference(draft)||saving)return false;
    const origin=client.currentSession();setSaving(true);setError("");
    try{const result=await client.mutate("/navigation/image-reference",{revision:state.revision,values:draft},"POST",true);if(owner.current!==origin||client.currentSession()!==origin)return false;const saved=readReference(data(result));if(!saved)throw new Error("invalid_reference");setState(saved);setDraft(null);setEditing(false);return true;}
    catch(error){if(owner.current!==origin)return false;const conflict=errorCode(error).includes("conflict");if(conflict){const latest=await apiFetch("/api/v1/rider/navigation/image-reference").then(result=>readReference(data(result))).catch(()=>null);if(latest&&owner.current===origin){setState(latest);setDraft(null);setEditing(false);}}if(owner.current===origin)setError(conflict?"conflict":"reference.saveFailed");return false;}
    finally{if(owner.current===origin)setSaving(false);}
  };
  const baseline=state?.values??initialReference(),values=draft??baseline;
  const changed=!!draft&&JSON.stringify(draft)!==JSON.stringify(baseline);
  return {state,draft,setDraft,editing,setEditing,error,saving,save,reload:()=>setAttempt(value=>value+1),changed,defaulted:!state?.values&&!changed,values};
}
export type ReferenceController=ReturnType<typeof useImageReference>;

export function ImageReferenceControls({reference,client,size,t}:{reference:ReferenceController;client:RiderClient;size:[number,number];t:Translator}){
  const [open,setOpen]=useState(false),v=reference.values;
  const unavailable=!reference.state||reference.saving;
  let sourceLabel="reference.defaults";
  if(reference.state?.values)sourceLabel="reference.saved";
  if(reference.changed)sourceLabel="reference.pending";
  const {state:loadedState,draft:loadedDraft,setDraft}=reference;
  useEffect(()=>{
    if(open&&loadedState&&!loadedDraft)setDraft(structuredClone(loadedState.values??initialReference()));
  },[open,loadedState,loadedDraft,setDraft]);
  const begin=()=>{if(!reference.state){reference.reload();setOpen(true);return;}reference.setDraft(structuredClone(v??initialReference()));setOpen(true);};
  const change=(next:ImageReference)=>reference.setDraft(next);
  return <span className={styles.referenceControl}>
    <Button data-testid="image-reference-toggle" aria-label={t("reference.title")} title={t("reference.title")} aria-pressed={!!v?.visible} onClick={()=>open?setOpen(false):begin()}><Ruler size={15}/></Button>
    {open&&<div className={styles.referenceSettings} role="dialog" aria-label={t("reference.title")}>
      <strong>{t("reference.title")}</strong><p className={styles.hint}>{t("reference.hint")}</p>
      <p className={styles.hint} data-testid="reference-source">{t(sourceLabel)}</p>
      {reference.error&&<p role="alert">{t(reference.error)}</p>}
      {!reference.state&&(reference.error?<Button data-testid="reference-retry" onClick={()=>reference.reload()}>{t("reference.retry")}</Button>:<p>{t("loading")}</p>)}
      {v&&<>
        <label className={styles.flex}><input data-testid="reference-visible" disabled={unavailable} type="checkbox" checked={v.visible} onChange={e=>change({...v,visible:e.target.checked})}/>{t("reference.visible")}</label>
        <div className={styles.grid3}>{(["track_width_cm","robot_width_cm","camera_height_cm"] as const).map(key=><Field key={key} label={t("reference."+key)+" · cm"}><Input data-testid={"reference-"+key} disabled={unavailable} type="number" min=".1" step=".1" value={v[key]>0?v[key]:""} onChange={e=>change({...v,[key]:e.target.value===""?0:Number(e.target.value)})}/></Field>)}</div>
        <div className={styles.grid3}>{v.markers.map(([cm,y],i)=><Field key={i} label={t("reference.marker")+" "+(i+1)+" · cm"}><Input data-testid={"reference-marker-"+i} disabled={unavailable} type="number" min=".1" step=".1" value={cm>0?cm:""} onChange={e=>change({...v,markers:v.markers.map((p,j)=>j===i?[Number(e.target.value),y]:p)})}/></Field>)}</div>
        <p className={styles.hint}>{t("reference.dragHint")}</p>{!referenceFitsFrame(v,size)&&<p className={styles.hint}>{t("reference.frameMismatch")}</p>}{!validReference(v)&&<p role="status">{t("reference.invalid")}</p>}
        <div className={styles.flex}>
          <Button data-testid="reference-edit" disabled={unavailable} onClick={()=>{reference.setEditing(true);setOpen(false);}}>{t("reference.edit")}</Button>
          <Button data-testid="reference-save" tone="primary" disabled={!reference.state||!client.canMutate||reference.saving||!validReference(v)} onClick={()=>void reference.save(client).then(saved=>{if(saved)setOpen(false);})}>{t("reference.save")}</Button>
          <Button onClick={()=>{reference.setDraft(null);reference.setEditing(false);setOpen(false);}}>{t("cancel")}</Button>
        </div>
      </>}
    </div>}
  </span>;
}

/** Combine model geometry with compatible visual calibration in one source space. */
export function ImageReferenceOverlay({reference,size,t,geometry}:{reference:ReferenceController;size:[number,number];t:Translator;geometry?:unknown}){
  const drag=useRef<string|null>(null),v=reference.values;
  const model=readGeometry(geometry,size);
  const showReference=!!v&&(v.visible||reference.editing)&&referenceFitsFrame(v,size);
  if(!showReference&&!model)return null;
  const [w,h]=model?[model.width,model.height]:v.frame_size;
  const body=v.track_width_cm>0?bodyEdges(v):[[],[]],poly=(points:number[][])=>points.map(([x,y])=>(x*w)+","+Math.min(h-1,y*h)).join(" ");
  const point=(e:PointerEvent<SVGSVGElement>)=>{const b=e.currentTarget.getBoundingClientRect();return imagePoint(e.clientX-b.left,e.clientY-b.top,b.width,b.height,w,h);};
  return <svg data-testid="image-reference-overlay" data-purpose="visual_reference_only" aria-label={t("reference.title")} viewBox={"0 0 "+w+" "+h} preserveAspectRatio="xMidYMid meet" style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:reference.editing?"auto":"none",touchAction:"none"}}
    onPointerDown={e=>{if(!reference.editing)return;drag.current=(e.target as Element).closest<HTMLElement | SVGElement>("[data-reference-handle]")?.dataset.referenceHandle??null;if(drag.current)e.currentTarget.setPointerCapture(e.pointerId);}}
    onPointerMove={e=>{const p=point(e);if(p&&drag.current&&reference.editing)reference.setDraft(moveReference(v,drag.current,p[0],p[1]));}}
    onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
    {model&&<GeometryShapes geometry={model}/>}
    {showReference&&<>
    <g fill="none" stroke="#7edcff" {...GEOMETRY_STROKE} strokeDasharray="5 4">{[v.left_edge,v.right_edge].map((edge,i)=><polyline key={i} points={poly(edge)} {...GEOMETRY_STROKE}/>)}</g>
    {v.track_width_cm>0&&<g fill="none" stroke="white" {...GEOMETRY_STROKE}>{body.map((edge,i)=><polyline key={i} points={poly(edge)} {...GEOMETRY_STROKE}/>)}</g>}
    <g fontSize="7" fontFamily="sans-serif" fill="white" stroke="#061522" strokeWidth="2" paintOrder="stroke">
      <text x="5" y="11">{t(reference.defaulted?"reference.defaultOverlay":"reference.approximate")} · {t("reference.lowest")}</text>
      <text x="5" y="21">{t("reference.track")} {v.track_width_cm} cm · {t("reference.robot")} {v.robot_width_cm} cm</text>
      <text x="5" y="31">{t("reference.camera_height_cm")}: {v.camera_height_cm} cm</text>
      {v.markers.map(([cm,y],i)=><g key={i} data-reference-handle={"marker:"+i} style={{cursor:reference.editing?"ns-resize":undefined}}>
        <line x1="3" y1={Math.min(h-1,y*h)} x2={w-3} y2={Math.min(h-1,y*h)} stroke="#7edcff" {...GEOMETRY_STROKE} strokeDasharray="5 4"/>
        <text x={i%2?w-5:5} y={Math.min(h-5,y*h-3)} textAnchor={i%2?"end":"start"}>{cm} cm</text>
        {reference.editing&&<circle cx={w/2} cy={Math.min(h-3,y*h)} r="3" fill="#7edcff" stroke="white" strokeWidth="1"/>}
      </g>)}
    </g>
    {reference.editing&&(["left","right"] as const).flatMap(side=>(side==="left"?v.left_edge:v.right_edge).map(([x,y],i)=><circle key={side+i} data-reference-handle={side+":"+i} cx={x*w} cy={Math.min(h-3,y*h)} r="3" fill="#7edcff" stroke="white" strokeWidth="1" style={{cursor:"move"}}/>))}
    </>}
  </svg>;
}