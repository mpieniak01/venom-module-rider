import {collisionDraft} from "./collision-geometry";
import {record,type JsonRecord} from "./model";
import {readReference,validReference,type ImageReference} from "./image-reference";

/** Fit the existing tapered path to the measured edges plus 1 cm per side. */
function projectReference(reference:ImageReference,corridor:number[],endFraction:number):{corridor:number[];farWidthRatio:number}|null {
 if(!validReference(reference)||!Array.isArray(corridor)||corridor.length!==4||
    !corridor.every(n=>typeof n==="number"&&Number.isFinite(n))||
    !(0<=corridor[0]&&corridor[0]<corridor[2]&&corridor[2]<=1&&0<=corridor[1]&&corridor[1]<corridor[3]&&corridor[3]<=1)||
    !Number.isFinite(endFraction)||endFraction<.05||endFraction>.5)return null;
 const top=Math.max(reference.left_edge[0][1],reference.right_edge[0][1]);
 const bottom=Math.min(reference.left_edge[1][1],reference.right_edge[1][1]);
 if(corridor[1]<top||corridor[3]>bottom)return null;
 const ratio=(reference.robot_width_cm+2)/reference.track_width_cm;
 // The existing envelope reaches its terminal width early, then keeps that
 // width through the endpoint. Check both ends of this terminal segment.
 const transition=corridor[1]+(corridor[3]-corridor[1])*endFraction;
 const bounds=[corridor[1],transition,corridor[3]].map(y=>{
  const interpolate=(points:number[][])=>points[0][0]+(y-points[0][1])/(points[1][1]-points[0][1])*(points[1][0]-points[0][0]);
  const left=interpolate(reference.left_edge),right=interpolate(reference.right_edge),center=(left+right)/2,half=(right-left)*ratio/2;
  return [center-half,center+half];
 });
 const center=(bounds[2][0]+bounds[2][1])/2;
 const radius=Math.max(...bounds.flatMap(p=>[center-p[0],p[1]-center]));
 const left=Math.floor((center-radius)*1e6)/1e6;
 const right=Math.ceil((center+radius)*1e6)/1e6;
 if(left<0||right>1||left>=right)return null;
 const axis=(left+right)/2,nearHalf=(right-left)/2;
 const farHalf=Math.max(...bounds.slice(0,2).flatMap(p=>[axis-p[0],p[1]-axis]));
 const farWidthRatio=Math.min(1,Math.max(.01,Math.ceil(farHalf/nearHalf*1e6)/1e6));
 return {corridor:[left,corridor[1],right,corridor[3]],farWidthRatio};
}

/** The ROI preserves near width; the taper is installed with the geometry. */
export function referenceCorridor(reference:ImageReference,corridor:number[]):number[]|null {
 return projectReference(reference,corridor,.2)?.corridor??null;
}

/** Keep existing movement tuning and the separately calibrated rotation envelope. */
export function geometryFromReference(reference:ImageReference, corridor:number[], geometry:JsonRecord):{corridor:number[];geometry:JsonRecord}|null {
 const old=record(geometry.collision),endFraction=old.end_fraction;
 if(typeof endFraction!=="number")return null;
 const projection=projectReference(reference,corridor,endFraction),next=projection?.corridor;
 if(!next||!projection||geometry.mode!=="image"||!("collision" in geometry))return null;
 const base=collisionDraft(next),previous=old.reference_corridor;
 const floor=Array.isArray(previous)&&previous.length===4&&previous.every(n=>typeof n==="number"&&Number.isFinite(n))
  ?[Math.min(base.reference_corridor[0],previous[0]),Math.min(base.reference_corridor[1],previous[1]),Math.max(base.reference_corridor[2],previous[2]),Math.max(base.reference_corridor[3],previous[3])]
  :base.reference_corridor;
 return {corridor:next,geometry:{...geometry,collision:{...old,far_width_ratio:projection.farWidthRatio,reference_corridor:floor}}};
}

/** Never promote unsaved defaults or a malformed reference into a driving draft. */
export function geometryFromSavedReference(value:unknown,corridor:number[],geometry:JsonRecord):{corridor:number[];geometry:JsonRecord}|null {
 const state=readReference(value);
 return state&&state.revision>0&&state.values?geometryFromReference(state.values,corridor,geometry):null;
}
