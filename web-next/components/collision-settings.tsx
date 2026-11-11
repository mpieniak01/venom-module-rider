import {collisionDraft,guidanceDraft,validGuidance} from "../lib/collision-geometry";
import {record,type JsonRecord} from "../lib/model";
import type {Translator} from "../lib/i18n";
import {Checkbox,Field,Input,Select,Button} from "./primitives";
import styles from "../rider.module.css";

/** Explicit draft settings; saving uses the existing stopped, revisioned Pi transaction. */
export function CollisionSettings({geometry,corridor,supported,guidanceSupported=false,disabled,onChange,t}:{geometry:JsonRecord;corridor:number[];supported:boolean;guidanceSupported?:boolean;disabled:boolean;onChange:(value:JsonRecord)=>void;t:Translator}){
 if(geometry.mode!=="image")return null;
 const collision=record(geometry.collision),enabled="collision" in geometry;
 const set=(key:string,value:unknown)=>onChange({...geometry,collision:{...collision,[key]:value}});
 const guidance=record(collision.guidance),bounds=guidance.rotation_bounds;
 const setGuidance=(patch:JsonRecord)=>set("guidance",{...guidanceDraft(),...guidance,...patch});
 const fields=[["far_width_ratio",1,100],["left_shift",0,100],["right_shift",0,100],["end_fraction",5,50]] as const;
 return <fieldset disabled={disabled} className={styles.stack} data-testid="collision-settings"><legend>{t("collision.title")}</legend>
  <label className={styles.check}><Checkbox data-testid="collision-enabled" type="checkbox" disabled={!supported&&!enabled} checked={enabled} onChange={event=>{
   const next={...geometry};if(event.target.checked)next.collision=collisionDraft(corridor,guidanceSupported);else delete next.collision;onChange(next);
  }}/>{t("collision.enabled")}</label>
  <p className={styles.hint}>{t(supported?"collision.help":"collision.unsupported")}</p>
  {enabled&&<>
   <Field label={t("guidance.mode")}><Select data-testid="guidance-mode" disabled={!guidanceSupported} value={String(guidance.motion_mode??"legacy")} onChange={event=>{const mode=event.target.value,complete=validGuidance({...guidanceDraft(),...guidance,motion_mode:mode,center_deadband:.1},corridor);setGuidance({motion_mode:mode,...(mode==="steer"&&!complete?{rotation_bounds:null}:{})});}}>{collision.guidance===undefined&&<option value="legacy">{t("guidance.mode.legacy")}</option>}{["rotate_then_drive","steer","auto"].map(mode=><option key={mode} value={mode}>{t("guidance.mode."+mode)}</option>)}</Select></Field>
   <p className={styles.hint}>{t(guidanceSupported?"guidance.help":"guidance.unsupported")}</p>
   {collision.guidance!==undefined&&<>
    <Field label={t("guidance.deadband")}><Input data-testid="guidance-deadband" type="number" min="0" max="25" value={Number(guidance.center_deadband)*100} onChange={event=>setGuidance({center_deadband:Number(event.target.value)/100})}/></Field>
    {guidance.motion_mode!=="steer"&&<><p className={styles.hint}>{t("guidance.rotationHelp")}</p><div className={styles.grid}>{["left","top","right","bottom"].map((key,index)=><Field key={key} label={t("guidance.rotation")+" · "+t("inputRegion."+key)+" (%)"}><Input data-testid={"guidance-rotation-"+key} type="number" min="0" max="100" step="1" value={Array.isArray(bounds)&&typeof bounds[index]==="number"?Number(bounds[index])*100:""} onChange={event=>{const next=Array.isArray(bounds)?[...bounds]:[null,null,null,null];next[index]=event.target.value===""?null:Number(event.target.value)/100;setGuidance({rotation_bounds:next});}}/></Field>)}</div><Button type="button" onClick={()=>setGuidance({rotation_bounds:null})}>{t("guidance.clearRotation")}</Button></>}
   </>}
   <div className={styles.grid}>{fields.map(([key,min,max])=><Field key={key} label={t("collision."+key)+" (%)"}><Input data-testid={"collision-"+key} type="number" min={min} max={max} step="1" value={Number(collision[key])*100} onChange={event=>set(key,Number(event.target.value)/100)}/></Field>)}</div>
   <p className={styles.hint}>{t("collision.reference")}</p><div className={styles.grid}>{["left","top","right","bottom"].map((key,index)=><Field key={key} label={t("inputRegion."+key)+" (%)"}><Input type="number" min="0" max="100" step="1" value={Number((collision.reference_corridor as number[])?.[index])*100} onChange={event=>{const ref=[...(collision.reference_corridor as number[])];ref[index]=Number(event.target.value)/100;set("reference_corridor",ref);}}/></Field>)}</div>
  </>}
 </fieldset>;
}
