import {useState} from "react";
import {inputBounds,region,RECOMMENDED_INPUT,FULL_INPUT} from "../lib/input-region";
import {record,type JsonRecord} from "../lib/model";
import type {Translator} from "../lib/i18n";
import {Field,Input,Select} from "./primitives";
import styles from "../rider.module.css";
/** Edit operator input bounds without replacing an explicit custom choice. */
export function InputRegionControls({value,onChange,disabled,t}:{value:unknown;onChange:(v:JsonRecord)=>void;disabled?:boolean;t:Translator}){
 const raw=record(value).bounds;const bounds=Array.isArray(raw)&&raw.length===4?raw as number[]:inputBounds(value),same=(b:number[])=>JSON.stringify(bounds)===JSON.stringify(b);
 const [customAt,setCustomAt]=useState<string|null>(null);
 const mode=customAt===JSON.stringify(bounds)?"custom":same(FULL_INPUT)?"full":same(RECOMMENDED_INPUT)?"recommended":"custom";
 return <fieldset className={styles.stack} disabled={disabled} data-testid="input-region-controls"><legend>{t("inputRegion.title")}</legend>
 <Field label={t("inputRegion.preset")}><Select data-testid="input-region-preset" value={mode} onChange={e=>{const preset=e.target.value;setCustomAt(preset==="custom"?JSON.stringify(bounds):null);onChange(region(preset==="full"?FULL_INPUT:preset==="recommended"?RECOMMENDED_INPUT:bounds.length?bounds:RECOMMENDED_INPUT));}}>
 <option value="recommended">{t("inputRegion.recommended")}</option><option value="full">{t("inputRegion.full")}</option><option value="custom">{t("inputRegion.custom")}</option></Select></Field>
 <div className={styles.grid2}>{["left","top","right","bottom"].map((key,i)=><Field key={key} label={t("inputRegion."+key)+" · %"}><Input data-testid={"input-region-"+key} type="number" min="0" max="100" step="1" value={Number.isFinite(bounds[i])?Math.round(bounds[i]*10000)/100:""} onChange={e=>{const b=bounds.length?[...bounds]:[...RECOMMENDED_INPUT];b[i]=e.target.value===""?NaN:Number(e.target.value)/100;onChange(region(b));}}/></Field>)}</div>
 <p className={styles.hint}>{t("inputRegion.hint")}</p></fieldset>;
}
