import {record} from "./model";
export type Geometry={version:1;width:number;height:number;input_bounds:number[];score_bounds?:number[];reference_bounds?:number[];paths?:Record<string,(number[]|null)[]>;near?:(number[]|null)[];floor_boundary?:(number[]|null)[];guard_clear?:boolean;objects?:{id:number;bounds:number[];evidence:string}[]};
export function readGeometry(value:unknown,size?:[number,number]):Geometry|null{
 const g=record(value),w=Number(g.width),h=Number(g.height),integer=(v:unknown)=>typeof v==="number"&&Number.isSafeInteger(v);
 if(g.version!==1||!integer(g.width)||!integer(g.height)||w<1||w>1280||h<1||h>720||size&&(w!==size[0]||h!==size[1]))return null;
 const box=(b:unknown):boolean=>Array.isArray(b)&&b.length===4&&b.every(integer)&&0<=b[0]&&b[0]<b[2]&&b[2]<=w&&0<=b[1]&&b[1]<b[3]&&b[3]<=h;
 if(!box(g.input_bounds)||["score_bounds","reference_bounds"].some(k=>g[k]!==undefined&&!box(g[k])))return null;
 const points=(v:unknown)=>Array.isArray(v)&&v.length<=32&&v.every(p=>p===null||Array.isArray(p)&&p.length===2&&p.every(integer)&&p[0]>=0&&p[0]<w&&p[1]>=0&&p[1]<h);
 if(g.paths!==undefined&&(typeof g.paths!=="object"||g.paths===null||Array.isArray(g.paths)||Object.entries(record(g.paths)).some(([k,v])=>!["left","center","right"].includes(k)||!points(v))))return null;
 if(["near","floor_boundary"].some(k=>g[k]!==undefined&&!points(g[k])))return null;
 if(g.objects!==undefined&&(!Array.isArray(g.objects)||g.objects.length>4||g.objects.some(o=>!integer(record(o).id)||!box(record(o).bounds)||typeof record(o).evidence!=="string")))return null;
 return g as Geometry;
}
