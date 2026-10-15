import { useRef, type PointerEvent } from "react";
import { dragCorridor, imagePoint, type Corridor } from "../lib/corridor-editor";
import type { Translator } from "../lib/i18n";

export type CorridorEditorProps={value:number[];disabled:boolean;onChange:(value:Corridor)=>void};
export function CorridorOverlay({editor,size,t}:{editor:CorridorEditorProps;size:[number,number];t:Translator}){
  const drag=useRef<{point:[number,number];roi:Corridor;handle:"move"|"start"|"end"}|null>(null);
  if(editor.value.length!==4||!editor.value.every(Number.isFinite))return null;
  const [x0,y0,x1,y1]=editor.value,[w,h]=size,handleSize=Math.min(w,h)*.03;
  const point=(e:PointerEvent<SVGSVGElement>)=>{const box=e.currentTarget.getBoundingClientRect();return imagePoint(e.clientX-box.left,e.clientY-box.top,box.width,box.height,w,h);};
  return <svg data-testid="corridor-editor" aria-label={t("zoneEdit")} viewBox={"0 0 "+w+" "+h} preserveAspectRatio="xMidYMid meet"
    style={{position:"absolute",inset:0,width:"100%",height:"100%",touchAction:"none",pointerEvents:editor.disabled?"none":"auto"}}
    onPointerDown={e=>{if(editor.disabled)return;const p=point(e);if(!p)return;const handle=(e.target as SVGElement).getAttribute("data-handle");if(handle!=="move"&&handle!=="start"&&handle!=="end")return;drag.current={point:p,roi:[x0,y0,x1,y1],handle};e.currentTarget.setPointerCapture(e.pointerId);}}
    onPointerMove={e=>{const p=point(e),start=drag.current;if(!p||!start||editor.disabled)return;editor.onChange(dragCorridor(start.roi,p[0]-start.point[0],p[1]-start.point[1],start.handle));}}
    onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
    <rect x={x0*w} y={y0*h} width={(x1-x0)*w} height={(y1-y0)*h} fill="#ffffff20" stroke="white" strokeWidth="2" vectorEffect="non-scaling-stroke" data-handle="move" style={{cursor:"move"}}/>
    {([[x0,y0,"start"],[x1,y1,"end"]] as const).map(([x,y,handle])=><rect key={handle} x={x*w-handleSize/2} y={y*h-handleSize/2} width={handleSize} height={handleSize} fill="#00e6b0" data-handle={handle} style={{cursor:"nwse-resize"}}/>)}
  </svg>;
}
