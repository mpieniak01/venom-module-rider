import {record,type JsonRecord} from "./model";

export type Guidance = {version:1;motion_mode:"rotate_then_drive"|"steer"|"auto";center_deadband:number;rotation_bounds:number[]|null};
export type Collision = {version:1;far_width_ratio:number;left_shift:number;right_shift:number;end_fraction:number;reference_corridor:number[];guidance?:Guidance};
export function guidanceDraft():Guidance{return {version:1,motion_mode:"rotate_then_drive",center_deadband:.1,rotation_bounds:null};}
export function geometryMode(geometry:JsonRecord,mode:string):JsonRecord {
 const next:JsonRecord={...geometry,mode};if(mode!=="image")delete next.collision;return next;
}
export function withoutCollision(geometry:JsonRecord):JsonRecord {
 const next={...geometry};delete next.collision;return next;
}
/** Initial draft preserves forward width; acceptance must cover the whole body maneuver. */
export function collisionDraft(corridor:number[],guided=false):Collision {
 const [a,b,c,d]=corridor,round=(n:number)=>Number(n.toFixed(12)),width=round(c-a);
 return {version:1,far_width_ratio:1,left_shift:width,right_shift:width,end_fraction:.2,
  reference_corridor:[Math.max(0,Math.min(a,(a+c)/2-.15)),Math.max(0,Math.min(b,d-.3)),Math.min(1,Math.max(c,(a+c)/2+.15)),d].map(round),...(guided?{guidance:guidanceDraft()}:{})};
}
export function validCollision(value:unknown,corridor:unknown):boolean {
 const row=record(value),number=(v:unknown,lo:number,hi:number)=>typeof v==="number"&&Number.isFinite(v)&&v>=lo&&v<=hi;
 if(row.version!==1||Object.keys(row).length!==("guidance" in row?7:6)||!number(row.far_width_ratio,Number.EPSILON,1)||!number(row.left_shift,0,1)||!number(row.right_shift,0,1)||!number(row.end_fraction,.05,.5))return false;
 const ref=row.reference_corridor,roi=corridor;
 if(!Array.isArray(ref)||!Array.isArray(roi)||ref.length!==4||roi.length!==4||!ref.every(v=>number(v,0,1))||!roi.every(v=>number(v,0,1)))return false;
 if("guidance" in row&&!validGuidance(row.guidance,roi))return false;
 return ref[0]<=roi[0]&&roi[0]<roi[2]&&roi[2]<=ref[2]&&ref[1]<=roi[1]&&roi[1]<roi[3]&&roi[3]<=ref[3]
  &&ref[2]-ref[0]+1e-9>=Math.min(.3,roi[2])&&ref[3]-ref[1]+1e-9>=Math.min(.3,roi[3]);
}
export function collisionNavigationValid(base:JsonRecord,draft:JsonRecord):boolean {
 const avoidance=record(draft.avoidance),geometry=record(avoidance.geometry);
 if(!("collision" in geometry))return true;
 if(base.collision_geometry_supported!==true||geometry.mode!=="image"||avoidance.enabled!==true||avoidance.protocol_version!==3||!validCollision(geometry.collision,draft.corridor))return false;
 if("guidance" in record(geometry.collision)&&base.axis_guidance_supported!==true)return false;
 const input=record(record(draft.perception).input_region).bounds,ref=record(geometry.collision).reference_corridor as number[];
 return input===undefined||Array.isArray(input)&&input.length===4&&input[0]<=ref[0]&&input[1]<=ref[1]&&input[2]>=ref[2]&&input[3]>=ref[3];
}

/** Display evidence already accepted by Pi; the device verifies the SHA-256 identity. */
export function collisionDecisionValid(policy:JsonRecord|null,decision:JsonRecord):boolean {
 const avoidance=record(policy?.avoidance),geometry=record(avoidance.geometry);
 if(!("collision" in geometry))return decision.geometry!=="image_collision_corridor"&&!("collision_geometry" in decision);
 const proof=record(decision.collision_geometry),paths=record(decision.paths),center=record(paths.center),guard=record(paths.guard),action=String(decision.action);
 const qualified=(path:JsonRecord,clear:boolean)=>path.coverage_complete===true&&path.geometry==="image_collision_corridor"&&typeof path.zone_pixels==="number"&&Number.isInteger(path.zone_pixels)&&path.zone_pixels>=64&&(!clear||path.state==="clear");
 const side=action==="steer_left"?"left":action==="steer_right"?"right":action==="turn_left"||action==="turn_right"?"rotation":null;
 return avoidance.enabled===true&&avoidance.protocol_version===3&&geometry.mode==="image"&&validCollision(geometry.collision,policy?.corridor)
  &&decision.protocol_version===3&&decision.geometry==="image_collision_corridor"&&proof.version===1&&typeof proof.sha256==="string"&&/^[0-9a-f]{64}$/.test(proof.sha256)
  &&qualified(center,["go","slow"].includes(action))&&qualified(guard,true)
  &&(side===null||qualified(record(paths[side]),true))
  &&guidanceDecisionValid(geometry,decision);
}

export function validGuidance(value:unknown,corridor:number[]):boolean {
 const g=record(value),b=g.rotation_bounds;
 if(g.version!==1||Object.keys(g).length!==4||!["rotate_then_drive","steer","auto"].includes(String(g.motion_mode))||typeof g.center_deadband!=="number"||!Number.isFinite(g.center_deadband)||g.center_deadband<0||g.center_deadband>.25)return false;
 return b===null||Array.isArray(b)&&b.length===4&&b.every(v=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=1)&&b[0]<b[2]&&b[1]<b[3]&&b[0]<=corridor[0]&&b[2]>=corridor[2]&&b[1]<corridor[3]&&b[3]>=corridor[3];
}
export function guidanceReady(policy:JsonRecord):boolean {
 const g=record(record(record(policy.avoidance).geometry).collision).guidance;
 if(record(g).motion_mode!=="rotate_then_drive")return true;
 const b=record(g).rotation_bounds,input=record(record(policy.perception).input_region).bounds;
 return Array.isArray(b)&&b.length===4&&(input===undefined||Array.isArray(input)&&input.length===4&&input[0]<=b[0]&&input[1]<=b[1]&&input[2]>=b[2]&&input[3]>=b[3]);
}
function guidanceDecisionValid(geometry:JsonRecord,decision:JsonRecord):boolean {
 const g=record(record(geometry.collision).guidance),proof=record(decision.guidance);
 if(!("version" in g))return !("guidance" in decision);
 if(proof.version!==1||proof.motion_mode!==g.motion_mode)return false;
 if(String(decision.action).startsWith("steer_")&&g.motion_mode==="rotate_then_drive")return false;
 if(String(decision.action).startsWith("turn_")){
  const rotation=record(record(decision.paths).rotation);
  return g.motion_mode!=="steer"&&Array.isArray(g.rotation_bounds)&&rotation.state==="clear"&&rotation.coverage_complete===true&&rotation.geometry==="image_collision_corridor";
 }
 return true;
}
