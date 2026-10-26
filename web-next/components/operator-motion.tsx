import { calibrationDraft, directionDescriptor } from "../lib/calibration";
import { descriptorRange } from "../lib/field-range";
import { DIRECTIONS, record, settingsOf, type JsonRecord, type NumericDescriptor } from "../lib/model";
import { useOperatorTuning } from "../lib/operator-tuning";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Field, Input, NumberField, Select } from "./primitives";
import styles from "../rider.module.css";

export function OperatorMotion({client,t,manual,locked=false}:{client:RiderClient;t:Translator;manual:boolean;locked?:boolean}) {
 const {state,setState}=useOperatorTuning(),settings=settingsOf(client.status);
 const effective=state.manual??calibrationDraft(settings),direction=state.direction;
 const editDirection=(field:string,value:number)=>setState(previous=>({...previous,manual:{...effective,directions:{...effective.directions,[direction]:{...effective.directions[direction],[field]:value}}}}));
 const editValue=(field:string,value:number)=>setState(previous=>({...previous,manual:{...effective,values:{...effective.values,[field]:value}}}));
 const row=effective.directions[direction];
 if(!row||!settings.parameter_schema)return null;
 return <div className={styles.stack} data-testid="operator-motion">
  {manual&&<><Field label={t("directions")}><Select data-testid="operator-direction" value={direction} onChange={event=>setState(previous=>({...previous,direction:event.target.value as typeof direction}))} disabled={state.busy||locked}>{DIRECTIONS.map(key=><option key={key} value={key}>{t(key)}</option>)}</Select></Field>
   <div className={styles.grid3}>{['speed','maximum','duration'].map(field=>{
    const descriptor={...directionDescriptor(settings,field,row),...(field==='duration'?{}:{display_factor:1,display_unit:''})};
    const factor=descriptor.display_factor??1;
    return <Field key={field} label={t(field)+` (${descriptorRange(descriptor)})`}><Input data-testid={'operator-manual-'+field} type="number" value={Number.isFinite(row[field])?Number((Number(row[field])*factor).toFixed(6)):''} min={Number(descriptor.minimum??0)*factor} max={Number(descriptor.maximum)*factor} step={field==='duration'?10:.01} disabled={state.busy||locked} onChange={event=>editDirection(field,event.target.value===''?Number.NaN:Number(event.target.value)/factor)}/></Field>;
   })}</div></>}
  <h3>{t('operator.ramps')}</h3>
  <div className={styles.grid}>{Object.entries(record(record(settings.parameter_schema).value_fields)).map(([field,raw])=>{
   const descriptor={...record(raw),...(field==='reversal_pause'?{}:{display_factor:1,display_unit:'/s'})} as NumericDescriptor;
   return <NumberField key={field} testId={'operator-'+field} label={t(field)} descriptor={descriptor} value={Number(effective.values[field])} disabled={state.busy||locked} compact onChange={value=>editValue(field,value)}/>;
  })}<Field label={t('ramp_mode')}><Select data-testid="operator-ramp-mode" value={String(effective.values.ramp_mode)} onChange={event=>editValue('ramp_mode',Number(event.target.value))} disabled={state.busy||locked}>{(record(settings.parameter_schema).ramp_modes as JsonRecord[]??[]).map(mode=><option key={String(mode.value)} value={String(mode.value)}>{t(String(mode.id))}</option>)}</Select></Field></div>
 </div>;
}
