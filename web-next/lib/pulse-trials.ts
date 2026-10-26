import {data,record,finite,type JsonRecord} from "./model";
export type PulseProfile={id:"A"|"B"|"C";version:1;count:number;pulse_ms:number;gap_ms:number};
export type PulseEvent={kind:string;pulse_index:number;elapsed_ms:number};
export type PulseRun=Omit<PulseProfile,"id">&{profile_id:"A"|"B"|"C";profile_version:1;run_id:string;phase:string;reason:string;completed_pulses:number;elapsed_ms:number;group_id:string;common:JsonRecord;events:PulseEvent[];t:number;clock?:string};
const recipes={A:[1,500,0],B:[2,250,100],C:[5,100,100]} as const;
export function pulseProfiles(value:unknown):PulseProfile[]{
 const row=data(value);
 if(row.version!==1||row.supported!==true||!Array.isArray(row.profiles))return [];
 return row.profiles.filter((value):value is PulseProfile=>{const p=record(value),id=String(p.id) as keyof typeof recipes,r=recipes[id];return !!r&&p.version===1&&p.count===r[0]&&p.pulse_ms===r[1]&&p.gap_ms===r[2];});
}
export const pulseActive=(run:JsonRecord|null|undefined)=>run?.phase==="pulse"||run?.phase==="gap";
export function matchingPulse(profile:PulseProfile|undefined,directions:JsonRecord):boolean{
 return !!profile&&Math.abs(Number(record(directions.forward).duration)*1000-profile.pulse_ms)<.00001;
}
export function pulseRun(value:unknown):JsonRecord|null{
 const row=record(value);return row.version===1&&typeof row.run_id==="string"&&typeof row.phase==="string"&&finite(row.elapsed_ms)?row:null;
}
export function seriesFor(stream:{trials?:PulseRun[]}|null,id:string){return stream?.trials?.find(run=>"series:"+run.run_id===id)??null;}
