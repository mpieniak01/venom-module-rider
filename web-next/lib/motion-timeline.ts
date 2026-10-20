import {finite,record,str,type JsonRecord} from "./model";
export type MotionPoint={id:string;t:number;segment:number;valid:boolean;instance_id:string|null;source_time:number|null;values:Record<string,number|null>;units:Record<string,string>;quality:JsonRecord};
export type MotionEvent={id:string;t:number;operation_id:string;phase:"requested"|"result";intent:JsonRecord;profile_ref:string|null;outcome:string;reason:string|null;ack_status:string|null};
export type MotionOutput={id:string;t:number;instance_id:string|null;source_time:number|null;target:Record<string,number|null>;output:Record<string,number|null>;state:string|null;quality:JsonRecord};
export type MotionStream={id:string;device_id:string;source_session:string;clock:string;origin:string;range:[number,number];points:MotionPoint[];events:MotionEvent[];outputs:MotionOutput[];profiles:Record<string,JsonRecord>};
export type MotionTimeline={schema_version:1;dataset_revision:string;session_id:string;context:string|null;streams:MotionStream[];collection_status:JsonRecord;counts:JsonRecord;sampling:JsonRecord;source_gaps_s:JsonRecord};
export function readTimeline(value:unknown):MotionTimeline {
  const r=record(value);
  if(r.schema_version!==1||!str(r.dataset_revision)||!str(r.session_id)||!Array.isArray(r.streams)||r.streams.length>2048)throw new Error("motion_timeline_invalid");
  let total=0;
  for(const value of r.streams){
    const s=record(value);
    if(!str(s.id)||!Array.isArray(s.points)||!Array.isArray(s.events)||!Array.isArray(s.outputs)||!Array.isArray(s.range)||s.range.length!==2||!s.range.every(finite)||s.range[1]<s.range[0]||!finite(s.range[1]-s.range[0]))throw new Error("motion_timeline_invalid");
    total+=s.points.length+s.events.length+s.outputs.length;
    if(total>2048)throw new Error("motion_timeline_invalid");
    for(const item of [...s.points,...s.events,...s.outputs])if(!finite(record(item).t))throw new Error("motion_timeline_invalid");
  }
  return r as unknown as MotionTimeline;
}
export const streamTimes=(a:MotionStream|null,b:MotionStream|null)=>[...new Set([a,b].flatMap(s=>s?[...s.points,...s.events,...s.outputs].map(p=>p.t):[]))].sort((x,y)=>x-y);
export function pointAt(stream:MotionStream|null,time:number):MotionPoint|null {
  return stream?.points.find(p=>Math.abs(p.t-time)<.0006)??null;
}
export function nearestTime(times:number[],value:number):number {
  return times.reduce((best,t)=>Math.abs(t-value)<Math.abs(best-value)?t:best,times[0]??0);
}
export function profileAt(stream:MotionStream|null,time:number):JsonRecord|null {
  const event=stream?.events.filter(e=>e.phase==="requested"&&e.t<=time).sort((a,b)=>b.t-a.t)[0];
  return event?.profile_ref?stream?.profiles[event.profile_ref]??null:null;
}
export function seriesSegments(points:MotionPoint[],channel:string,unit:string):MotionPoint[][] {
  const result:MotionPoint[][]=[];
  let segment:MotionPoint[]=[];
  for(const p of points){
    if(!finite(p.values[channel])||p.units[channel]!==unit){if(segment.length)result.push(segment);segment=[];continue;}
    const prev=segment.at(-1);
    if(prev&&(!prev.valid||!p.valid||prev.segment!==p.segment||p.t<=prev.t)){result.push(segment);segment=[];}
    segment.push(p);
  }
  if(segment.length)result.push(segment);
  return result;
}

export const trialRequests=(stream:MotionStream|null)=>stream?.events.filter(e=>e.phase==="requested"&&e.intent.action==="move")??[];
export function alignTrial(stream:MotionStream|null,id:string):MotionStream|null {
  if(!stream)return null;
  const anchor=trialRequests(stream).find(e=>e.operation_id===id);
  if(!anchor)return stream;
  const offset=anchor.t;
  return {...stream,origin:"selected_move_request",range:[stream.range[0]-offset,stream.range[1]-offset],
    points:stream.points.map(p=>({...p,t:p.t-offset})),events:stream.events.map(e=>({...e,t:e.t-offset})),outputs:stream.outputs.map(o=>({...o,t:o.t-offset}))};
}
export function pulseEvidence(stream:MotionStream|null){
  const event=trialRequests(stream).find(e=>Math.abs(e.t)<.0006);
  const duration=event?.intent.duration_s;
  const profile=event?.profile_ref?stream?.profiles[event.profile_ref]??null:null;
  if(!event||!finite(duration)||duration<=0)return {event,result:stream?.events.find(e=>e.phase==="result"&&e.operation_id===event?.operation_id)??null,profile,peaks:null};
  const points=stream!.points.filter(p=>p.t>=0&&p.t<=duration);
  if(points.length<3||points.some(p=>!p.valid||p.segment!==points[0].segment))return {event,result:stream?.events.find(e=>e.phase==="result"&&e.operation_id===event?.operation_id)??null,profile,peaks:null};
  const peaks:Record<string,{value:number;unit:string}>={};
  for(const axis of ["roll","pitch","yaw"]){
    const values=points.map(p=>p.values[axis]);
    const unit=points[0].units[axis];
    if(!unit||unit==="unknown"||points.some(p=>p.units[axis]!==unit)||!values.every(finite))continue;
    const value=Math.max(...values)-Math.min(...values);
    if(finite(value))peaks[axis]={value,unit};
  }
  return {event,result:stream?.events.find(e=>e.phase==="result"&&e.operation_id===event?.operation_id)??null,profile,peaks:Object.keys(peaks).length?peaks:null};
}
