import {record,type JsonRecord} from "./model";
export const RECOMMENDED_INPUT = [0,.4,1,1];
export const FULL_INPUT = [0,0,1,1];
/** Read validated source bounds; absence retains the legacy full frame. */
export function inputBounds(value:unknown):number[]{
  if(value===undefined)return [...FULL_INPUT];
  const row=record(value),b=row.bounds;
  return row.version===1&&Object.keys(row).every(k=>["version","bounds"].includes(k))&&Array.isArray(b)&&b.length===4&&b.every(v=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=1)&&b[0]<b[2]&&b[1]<b[3]?[...b]:[];
}
/** Require complete assessment and floor-reference coverage before submission. */
export function inputCovers(value:unknown,corridor:unknown):boolean{
  const b=inputBounds(value);
  if(!b.length||!Array.isArray(corridor)||corridor.length!==4)return false;
  const [a,y,c,d]=corridor;
  return b[0]<=a&&b[1]<=Math.min(y,Math.max(0,Math.round((d-.3)*1e12)/1e12))&&b[2]>=c&&b[3]>=d;
}
export const region=(bounds:number[]):JsonRecord=>({version:1,bounds:[...bounds]});

/** Require the whole explicit floor reference inside the model input. */
export function inputCoversReference(value:unknown,reference:unknown):boolean {
  const b=inputBounds(value);
  return b.length===4&&Array.isArray(reference)&&reference.length===4&&reference.every(v=>typeof v==="number"&&Number.isFinite(v))&&b[0]<=reference[0]&&b[1]<=reference[1]&&b[2]>=reference[2]&&b[3]>=reference[3];
}
