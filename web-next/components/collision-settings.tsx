import {collisionDraft} from "../lib/collision-geometry";
import {record,type JsonRecord} from "../lib/model";
import type {Translator} from "../lib/i18n";
import {Checkbox,Field,Input} from "./primitives";
import styles from "../rider.module.css";

/** Explicit draft settings; saving uses the existing stopped, revisioned Pi transaction. */
export function CollisionSettings({geometry,corridor,supported,disabled,onChange,t}:{geometry:JsonRecord;corridor:number[];supported:boolean;disabled:boolean;onChange:(value:JsonRecord)=>void;t:Translator}){
 if(geometry.mode!=="image")return null;
 const collision=record(geometry.collision),enabled="collision" in geometry;
 const set=(key:string,value:unknown)=>onChange({...geometry,collision:{...collision,[key]:value}});
 const fields=[["far_width_ratio",1,100],["left_shift",0,100],["right_shift",0,100],["end_fraction",5,50]] as const;
 return <fieldset disabled={disabled} className={styles.stack} data-testid="collision-settings"><legend>{t("collision.title")}</legend>
  <label className={styles.check}><Checkbox data-testid="collision-enabled" type="checkbox" disabled={!supported&&!enabled} checked={enabled} onChange={event=>{
   const next={...geometry};if(event.target.checked)next.collision=collisionDraft(corridor);else delete next.collision;onChange(next);
  }}/>{t("collision.enabled")}</label>
  <p className={styles.hint}>{t(supported?"collision.help":"collision.unsupported")}</p>
  {enabled&&<><div className={styles.grid}>{fields.map(([key,min,max])=><Field key={key} label={t("collision."+key)+" (%)"}><Input data-testid={"collision-"+key} type="number" min={min} max={max} step="1" value={Number(collision[key])*100} onChange={event=>set(key,Number(event.target.value)/100)}/></Field>)}</div>
   <p className={styles.hint}>{t("collision.reference")}</p><div className={styles.grid}>{["left","top","right","bottom"].map((key,index)=><Field key={key} label={t("inputRegion."+key)+" (%)"}><Input type="number" min="0" max="100" step="1" value={Number((collision.reference_corridor as number[])?.[index])*100} onChange={event=>{const ref=[...(collision.reference_corridor as number[])];ref[index]=Number(event.target.value)/100;set("reference_corridor",ref);}}/></Field>)}</div>
  </>}
 </fieldset>;
}
