import {useEffect,useRef,useState} from "react";
import {finite,formatNumber,record,str} from "../lib/model";
import {nearestTime,seriesSegments,type MotionStream} from "../lib/motion-timeline";
import type {Translator} from "../lib/i18n";
import {Field,Select} from "./primitives";
import styles from "../rider.module.css";

export function usePlotWidth(){
  const ref=useRef<HTMLDivElement>(null);
  const [width,setWidth]=useState(400);
  useEffect(()=>{const el=ref.current;if(!el)return;const update=()=>setWidth(Math.max(200,el.clientWidth));const observer=new ResizeObserver(update);observer.observe(el);return()=>observer.disconnect();},[]);
  return {ref,width};
}
export function MotionTrace({a,b,channel,range,cursor,onTime,t,language}:{a:MotionStream|null;b:MotionStream|null;channel:string;range:[number,number];cursor:number;onTime:(time:number)=>void;t:Translator;language:string}){
  const {ref,width}=usePlotWidth();
  const units=[...new Set([a,b].flatMap(s=>s?.points.filter(p=>finite(p.values[channel])).map(p=>p.units[channel])??[]))];
  const [chosen,setChosen]=useState("");
  const unit=units.includes(chosen)?chosen:units[0]??"unknown";
  const height=116,left=54,right=width-12,top=20,bottom=82;
  const xmin=range[0],span=Math.max(.001,range[1]-xmin);
  const x=(value:number)=>left+4+(value-xmin)/span*(right-left-8);
  const visible=[a,b].flatMap(s=>s?.points.filter(p=>p.t>=range[0]&&p.t<=range[1]&&p.units[channel]===unit&&finite(p.values[channel])).map(p=>p.values[channel] as number)??[]);
  const lo=Math.min(0,...visible),hi=Math.max(0,...visible);
  const magnitude=Math.max(1,Math.abs(lo),Math.abs(hi));
  const scale=(hi/magnitude-lo/magnitude)||1;
  const y=(value:number)=>bottom-4-(value/magnitude-lo/magnitude)/scale*(bottom-top-8);
  const tick=(v:number)=>Math.abs(v)>=10000?v.toExponential(1):formatNumber(v,language,1);
  return <div ref={ref} className={styles.motionTrace}>
    {units.length>1&&<Field label={t("study.unit")+" · "+channel}><Select value={unit} onChange={e=>setChosen(e.target.value)}>{units.map(u=><option key={u}>{u}</option>)}</Select></Field>}
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={channel+" · "+unit} onClick={e=>{const rect=e.currentTarget.getBoundingClientRect();const time=xmin+((e.clientX-rect.left)-left)/(right-left)*span;onTime(nearestTime([a,b].flatMap(s=>s?.points.map(p=>p.t)??[]),time));}}>
      <title>{channel+" · "+unit}</title>
      <text x={left} y={13}>{channel+" · "+unit}</text>
      <rect x={left} y={top} width={Math.max(1,right-left)} height={bottom-top} className={styles.motionPlotFrame}/>
      {[lo,hi].map((v,i)=><text key={i} x={left-5} y={top+(1-i)*(bottom-top)+4} textAnchor="end">{tick(v)}</text>)}
      {[0,1,2,3].map(i=>{const v=xmin+i*span/3;return <text key={i} x={x(v)} y={101} textAnchor={i===0?"start":i===3?"end":"middle"}>{formatNumber(v,language,2)} s</text>;})}
      {[a,b].map((s,index)=>s&&<g key={index} className={index?styles.motionSeriesB:styles.motionSeriesA}>
        {seriesSegments(s.points,channel,unit).map((points,key)=><g key={key}>
          {points.length>1&&<polyline points={points.filter(p=>p.t>=range[0]&&p.t<=range[1]).map(p=>`${x(p.t)},${y(p.values[channel] as number)}`).join(" ")} fill="none" strokeDasharray={index?"4 3":undefined}/>}
          {points.filter(p=>p.t>=range[0]&&p.t<=range[1]).map(p=><circle key={p.id} cx={x(p.t)} cy={y(p.values[channel] as number)} r={3} fillOpacity={p.valid?1:.35}><title>{`${index?"B":"A"} · ${formatNumber(p.t,language,3)} s · ${p.values[channel]} ${unit}`}</title></circle>)}
        </g>)}
      </g>)}
      <line x1={x(cursor)} x2={x(cursor)} y1={top} y2={bottom} className={styles.motionCursor}/>
    </svg>
  </div>;
}
export function CommandTrace({stream,range,cursor,t,language}:{stream:MotionStream|null;range:[number,number];cursor:number;t:Translator;language:string}){
  const {ref,width}=usePlotWidth();
  const span=Math.max(.001,range[1]-range[0]),left=54,right=width-12,x=(v:number)=>left+(v-range[0])/span*(right-left);
  return <div ref={ref} className={styles.motionTrace}><svg width="100%" height={82} viewBox={`0 0 ${width} 82`} role="img" aria-label={t("study.commands")}>
    <text x={left} y={13}>{t("study.commands")}</text>
    <line x1={left} x2={right} y1={48} y2={48} className={styles.motionCursor}/>
    {stream?.events.filter(e=>e.t>=range[0]&&e.t<=range[1]).map(e=><g key={e.id} className={styles.motionSeriesA}>
      {e.phase==="requested"&&finite(record(e.intent).duration_s)&&<rect x={x(e.t)} y={25} width={Math.max(1,x(Math.min(range[1],e.t+Number(e.intent.duration_s)))-x(e.t))} height={10} opacity={.25}/>}
      <circle cx={x(e.t)} cy={e.phase==="requested"?30:48} r={3}><title>{formatNumber(e.t,language,3)+" s · "+str(e.intent.action)+" · "+str(e.intent.direction)+" · "+str(e.intent.mode)+" · "+e.phase+" · "+e.outcome+" · "+formatNumber(finite(e.intent.intensity)?e.intent.intensity*100:null,language,1)+" % · "+formatNumber(e.intent.duration_s,language,3)+" s"}</title></circle>
    </g>)}
    <line x1={x(cursor)} x2={x(cursor)} y1={20} y2={55} className={styles.motionCursor}/>
  </svg></div>;
}

export function outputStream(stream:MotionStream|null):MotionStream|null {
  if(!stream)return null;
  return {...stream,points:stream.outputs.map((item,index)=>({
    id:str(item.id),t:Number(item.t),segment:index,valid:true,instance_id:null,source_time:null,
    values:{lx:finite(record(item.output).lx)?Number(record(item.output).lx)*100:null,az:finite(record(item.output).az)?Number(record(item.output).az)*100:null},
    units:{lx:"% SDK",az:"% SDK"},quality:{},
  }))};
}
