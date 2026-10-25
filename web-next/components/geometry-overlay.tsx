import {readGeometry,type Geometry} from "../lib/overlay-geometry";
export const GEOMETRY_STROKE={strokeWidth:1.5,vectorEffect:"non-scaling-stroke" as const};
export function GeometryShapes({geometry:g}:{geometry:Geometry}){
 const box=(b:number[],color:string,dash?:string)=><rect x={b[0]} y={b[1]} width={b[2]-b[0]} height={b[3]-b[1]} stroke={color} strokeDasharray={dash} {...GEOMETRY_STROKE}/>;
 const path=(p:(number[]|null)[])=>p.map((v,i)=>v?`${i===0||p[i-1]===null?"M":"L"}${v[0]},${v[1]}`:"").join(" ");
 return <g fill="none" data-testid="model-geometry-shapes">
 {box(g.input_bounds,"#7edcff","5 4")}{g.reference_bounds&&box(g.reference_bounds,"#7edcff","5 4")}{g.score_bounds&&box(g.score_bounds,"white")}
 {Object.entries(g.paths??{}).map(([k,p])=><path key={k} d={path(p)} stroke={k==="center"?"white":"#7edcff"} {...GEOMETRY_STROKE}/>)}
 {g.near&&<path d={path(g.near)} stroke={g.guard_clear?"#23d264":"#ffb923"} {...GEOMETRY_STROKE}/>}{g.floor_boundary&&<path d={path(g.floor_boundary)} stroke="#7edcff" {...GEOMETRY_STROKE}/>}
 {g.objects?.map(o=><g key={o.id}>{box(o.bounds,o.evidence==="approaching"?"#f03c41":"#ffb923","5 4")}</g>)}</g>;
}
export function GeometryOverlay({value,size}:{value:unknown;size?:[number,number]}){
 const g=readGeometry(value,size);if(!g)return null;
 return <svg data-testid="model-geometry-overlay" aria-hidden="true" viewBox={`0 0 ${g.width} ${g.height}`} preserveAspectRatio="xMidYMid meet" style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:"none"}}><GeometryShapes geometry={g}/></svg>;
}
