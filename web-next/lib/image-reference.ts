export type ReferencePoint=[number,number];
export type ImageReference={visible:boolean;frame_size:[number,number];track_width_cm:number;robot_width_cm:number;camera_height_cm:number;reference_pose:"lowest";left_edge:[ReferencePoint,ReferencePoint];right_edge:[ReferencePoint,ReferencePoint];markers:ReferencePoint[]};
export type ReferenceState={version:number;revision:number;purpose:string;values:ImageReference|null};
export function readReference(value:unknown):ReferenceState|null{
  if(!value||typeof value!=="object")return null;
  const v=value as ReferenceState;
  return v.version===1&&Number.isInteger(v.revision)&&v.revision>=0&&v.purpose==="visual_reference_only"&&(v.values===null||validReference(v.values))?v:null;
}
const numeric=(v:unknown,min:number,max:number):v is number=>typeof v==="number"&&Number.isFinite(v)&&v>=min&&v<=max;
const point=(p:unknown):p is ReferencePoint=>Array.isArray(p)&&p.length===2&&p.every(v=>numeric(v,0,1));
export function validReference(v:ImageReference|null):v is ImageReference{
  return !!v&&typeof v.visible==="boolean"&&v.reference_pose==="lowest"&&Array.isArray(v.frame_size)&&v.frame_size.length===2&&v.frame_size.every(Number.isInteger)&&numeric(v.frame_size[0],32,1280)&&numeric(v.frame_size[1],32,720)
    &&[v.track_width_cm,v.robot_width_cm,v.camera_height_cm].every(n=>numeric(n,.1,1000))&&v.robot_width_cm<=v.track_width_cm
    &&[v.left_edge,v.right_edge].every(edge=>Array.isArray(edge)&&edge.length===2&&edge.every(point)&&edge[0][1]<edge[1][1])
    &&v.left_edge.every((p,i)=>Math.abs(p[1]-v.right_edge[i][1])<=1e-6&&v.right_edge[i][0]-p[0]>=.01)
    &&Array.isArray(v.markers)&&v.markers.length>=2&&v.markers.length<=8&&v.markers.every(p=>Array.isArray(p)&&p.length===2&&numeric(p[0],.1,1000)&&numeric(p[1],0,1))
    &&v.markers.every((p,i)=>!i||(p[0]>v.markers[i-1][0]&&p[1]<v.markers[i-1][1]));
}
export function emptyReference(size:[number,number]):ImageReference{return {visible:true,frame_size:size,track_width_cm:0,robot_width_cm:0,camera_height_cm:0,reference_pose:"lowest",left_edge:[[.4,.65],[.3,1]],right_edge:[[.6,.65],[.7,1]],markers:[[0,.95],[0,.7]]};}
export function bodyEdges(v:ImageReference):[ReferencePoint[],ReferencePoint[]]{
  const ratio=v.robot_width_cm/v.track_width_cm;
  return [v.left_edge.map((p,i)=>[(p[0]+v.right_edge[i][0])/2-(v.right_edge[i][0]-p[0])*ratio/2,p[1]]),v.right_edge.map((p,i)=>[(p[0]+v.left_edge[i][0])/2+(p[0]-v.left_edge[i][0])*ratio/2,p[1]])];
}
export function moveReference(v:ImageReference,handle:string,x:number,y:number):ImageReference{
  const next=structuredClone(v),[side,key]=handle.split(":"),i=Number(key),clamp=(n:number,a:number,b:number)=>Math.max(a,Math.min(b,n));
  if(!Number.isInteger(i)||!Number.isFinite(x)||!Number.isFinite(y))return next;
  if(side==="marker"&&i>=0&&i<next.markers.length){next.markers[i][1]=clamp(y,(next.markers[i+1]?.[1]??-.005)+.005,i?next.markers[i-1][1]-.005:1);return next;}
  if((side!=="left"&&side!=="right")||i<0||i>1)return next;
  const edge=side==="left"?next.left_edge:next.right_edge,other=side==="left"?next.right_edge:next.left_edge;
  edge[i][0]=clamp(x,side==="left"?0:other[i][0]+.01,side==="left"?other[i][0]-.01:1);
  edge[i][1]=other[i][1]=clamp(y,i?edge[0][1]+.02:0,i?1:edge[1][1]-.02);
  return next;
}