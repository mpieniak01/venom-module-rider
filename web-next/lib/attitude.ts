import {finite,record,str} from "./model";
import {pointAt,type MotionPoint,type MotionStream} from "./motion-timeline";

export type Quaternion=[number,number,number,number]; // w,x,y,z; active body -> reference
export type Vector3=[number,number,number];
export type Attitude={state:"available";q:Quaternion;point:MotionPoint;identity:string;reference:string;simulated:boolean;singular:boolean;provisional?:boolean;unitAssumption?:boolean}|{state:"unavailable"|"unqualified";reason:string};
export function multiply(a:Quaternion,b:Quaternion):Quaternion {
  const [w,x,y,z]=a,[v,i,j,k]=b;
  return [w*v-x*i-y*j-z*k,w*i+x*v+y*k-z*j,w*j-x*k+y*v+z*i,w*k+x*j-y*i+z*v];
}
export const inverse=(q:Quaternion):Quaternion=>[q[0],-q[1],-q[2],-q[3]];
export function eulerZYX(roll:number,pitch:number,yaw:number):Quaternion {
  const cr=Math.cos(roll/2),sr=Math.sin(roll/2),cp=Math.cos(pitch/2),sp=Math.sin(pitch/2),cy=Math.cos(yaw/2),sy=Math.sin(yaw/2);
  return [cy*cp*cr+sy*sp*sr,cy*cp*sr-sy*sp*cr,cy*sp*cr+sy*cp*sr,sy*cp*cr-cy*sp*sr];
}
export function rotate(q:Quaternion,v:Vector3):Vector3 {
  const r=multiply(multiply(q,[0,...v]),inverse(q));
  return [r[1],r[2],r[3]];
}
export type PreviewOptions={enabled:boolean;assumedUnit:"deg"|"rad"};
/** Require the complete device orientation convention and sample identity. */
function qualifiedOrientation(point:MotionPoint,meta:ReturnType<typeof record>):boolean {
  return meta.convention==="rider-body-zyx-v1"&&meta.revision===1&&meta.verified===true&&!!str(meta.qualification_ref)&&str(meta.qualification_ref).length<=128&&(meta.unit==="deg"||meta.unit==="rad")&&!!point.instance_id&&meta.instance_id===point.instance_id;
}
type AttitudeAxis={value:number;stamp:number;unit:string;assumed:boolean}|{error:Attitude};
/** Validate one axis and normalize its angle only after sensor coherence checks. */
function attitudeAxis(point:MotionPoint,meta:ReturnType<typeof record>,qualified:boolean,preview:PreviewOptions|undefined,axis:string):AttitudeAxis {
  const q=record(point.quality),value=point.values[axis],sensor=record(record(q.sensors)[axis]);
  const declared=point.angle_semantics?.[axis]?.unit??point.units[axis];
  const known=declared==="deg"||declared==="rad";
  let unit:unknown;
  if(qualified)unit=meta.unit;
  else unit=known?declared:preview!.assumedUnit;
  if(!finite(value)||Math.abs(value)>1e9||qualified&&(point.units[axis]!==unit||sensor.unit!==unit||Math.abs(value)>(unit==="deg"?360:2*Math.PI)))return {error:{state:"unqualified",reason:"units"}};
  if(sensor.unit!==point.units[axis]||sensor.ready!==true||sensor.stale!==false||sensor.offline!==false||sensor.instance_id!==point.instance_id||!finite(sensor.observed_at))return {error:{state:"unavailable",reason:"coherence"}};
  const period=unit==="deg"?360:2*Math.PI;
  return {value:((value%period)+period)%period*(unit==="deg"?Math.PI/180:1),unit:str(unit),stamp:sensor.observed_at,assumed:!known&&!qualified};
}
/** Qualify a coherent orientation before producing any reconstructed attitude. */
export function qualifyAttitude(point:MotionPoint|null,preview?:PreviewOptions):Attitude {
  if(!point)return {state:"unavailable",reason:"noSample"};
  const q=record(point.quality),meta=record(q.orientation);
  const qualified=qualifiedOrientation(point,meta);
  if(!qualified&&!preview?.enabled)return {state:"unqualified",reason:"convention"};
  if(!point.valid||!point.instance_id||q.ready!==true||q.stale!==false||q.offline!==false)return {state:"unavailable",reason:"quality"};
  const values:number[]=[],stamps:number[]=[],units:string[]=[];
  let assumed=false;
  for(const axis of ["roll","pitch","yaw"]){
    const result=attitudeAxis(point,meta,qualified,preview,axis);
    if("error" in result)return result.error;
    if(result.assumed)assumed=true;
    values.push(result.value);units.push(result.unit);stamps.push(result.stamp);
  }
  if(!finite(point.source_time)||Math.max(...stamps)-Math.min(...stamps)>1e-6||Math.abs(Math.max(...stamps)-point.source_time)>1e-6)return {state:"unavailable",reason:"coherence"};
  const identity=qualified?[meta.convention,meta.revision,meta.unit,meta.qualification_ref,point.instance_id]:["provisional-axes-123-v1",units,point.instance_id];
  return {state:"available",q:eulerZYX(values[0],values[1],values[2]),point,identity:JSON.stringify(identity),reference:qualified?str(meta.qualification_ref):"provisional-axes-123-v1",provisional:!qualified,unitAssumption:assumed,simulated:q.simulated!==false,singular:Math.abs(Math.cos(values[1]))<.01};
}
export const attitudeAt=(stream:MotionStream|null,time:number,preview?:PreviewOptions)=>qualifyAttitude(pointAt(stream,time),preview);
export function relativeAttitude(current:Attitude,reference:Attitude|null):Attitude {
  if(current.state!=="available")return current;
  if(!reference||reference.state!=="available"||reference.identity!==current.identity||reference.point.segment!==current.point.segment)return {state:"unavailable",reason:"reference"};
  return {...current,q:multiply(inverse(reference.q),current.q)};
}
/** Replay advances only to retained observations/events and pauses across long gaps. */
export function replayStep(times:number[],cursor:number):{next:number|null;delay:number;gap:boolean}{
  const next=times.find(t=>t>cursor+1e-9);
  if(next===undefined)return {next:null,delay:0,gap:false};
  const delta=next-cursor;
  return {next,delay:Math.min(1000,Math.max(100,delta*1000)),gap:delta>1};
}
/** Fixed isometric camera, independent of body orientation. */
export function project(v:Vector3):[number,number,number] {
  const [x,y,z]=v;
  return [160+58*(.78*x-.78*y),110+58*(.34*x+.34*y-.9*z),.702*x+.702*y+.5304*z];
}
