import {record,str,type JsonRecord} from "./model";
export const FIGURE_IDS=["sway","vertical","forward_backward","snake","lift_rotate","circular_sway"] as const;
export type FigureRecipe={id:string;version:number;available:boolean;reason:string;duration_ms:number;initial_height:number|null;initial_roll:number|null;height_range:number[]|null;roll_range:number[]|null;maximum_speed:number;intensity_factor:number};
export function figureCatalog(value:unknown):FigureRecipe[]{
 const row=record(value);
 if(row.version!==1||row.implementation!=="own_sequence"||!Array.isArray(row.figures))return [];
 return row.figures.flatMap(item=>{
  const r=record(item);
  if(!FIGURE_IDS.includes(r.id as typeof FIGURE_IDS[number])||r.version!==1||r.implementation!=="own_sequence"||r.qualification!=="manual_trial"||r.physical_completion!=="unverified"||!Number.isInteger(r.duration_ms)||Number(r.duration_ms)<=0||Number(r.duration_ms)>10000)return [];
  if(typeof r.maximum_speed!=="number"||!Number.isFinite(r.maximum_speed)||r.maximum_speed<=0||r.maximum_speed>1||typeof r.intensity_factor!=="number"||!Number.isFinite(r.intensity_factor)||r.intensity_factor<=0||r.intensity_factor>1)return [];
  return [{id:str(r.id),version:1,available:r.available===true,reason:str(r.reason),duration_ms:Number(r.duration_ms),initial_height:typeof r.initial_height==="number"?r.initial_height:null,initial_roll:typeof r.initial_roll==="number"?r.initial_roll:null,height_range:Array.isArray(r.height_range)?r.height_range as number[]:null,roll_range:Array.isArray(r.roll_range)?r.roll_range as number[]:null,maximum_speed:Number(r.maximum_speed),intensity_factor:Number(r.intensity_factor)}];
 });
}
export function figureActive(run:JsonRecord|null):boolean{return !!run&&["preparing","running"].includes(str(run.phase));}
