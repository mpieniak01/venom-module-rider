import {useState} from "react";
import {attitudeAt,relativeAttitude,rotate,project,type Attitude,type Quaternion,type Vector3} from "../lib/attitude";
import {formatNumber} from "../lib/model";
import type {MotionStream} from "../lib/motion-timeline";
import type {Translator} from "../lib/i18n";
import {Button,Field,Select} from "./primitives";
import styles from "../rider.module.css";

const vertices:Vector3[]=[[-1,-.55,-.22],[1,-.55,-.22],[1,.55,-.22],[-1,.55,-.22],[-1,-.55,.22],[1,-.55,.22],[1,.55,.22],[-1,.55,.22]];
const faces=[[0,1,2,3],[4,7,6,5],[0,4,5,1],[3,2,6,7],[0,3,7,4],[1,5,6,2]];
const axes:Vector3[]=[[1.65,0,0],[0,1.65,0],[0,0,1.65]];
function Body({q,name,t}:{q:Quaternion;name:string;t:Translator}){
  const points=vertices.map(v=>project(rotate(q,v)));
  const ordered=faces.map((indices,i)=>({indices,i,depth:indices.reduce((n,index)=>n+points[index][2],0)/4})).sort((a,b)=>a.depth-b.depth);
  const coordinates=(v:Vector3)=>project(rotate(q,v));
  const origin=project([0,0,0]);
  const referenceOrigin=[270,180];
  const referencePoint=(v:Vector3)=>{const p=project(v);return [referenceOrigin[0]+.35*(p[0]-origin[0]),referenceOrigin[1]+.35*(p[1]-origin[1])];};
  return <svg data-testid={"attitude-body-"+name} viewBox="0 0 320 230" role="img" aria-label={t("attitude.body")+" "+name} className={styles.attitudeBody}>
    <title>{t("attitude.body")+" "+name+" · X "+t("attitude.front")+" / Y / Z"}</title>
    <g className={styles.attitudeReference}>{axes.map((axis,i)=>{const p=referencePoint(axis);return <g key={i}><line x1={referenceOrigin[0]} y1={referenceOrigin[1]} x2={p[0]} y2={p[1]}/><text x={p[0]} y={p[1]-4}>{"XYZ"[i]+"₀"}</text></g>;})}</g>
    <g className={name==="B"?styles.attitudeBodyB:styles.attitudeBodyA}>{ordered.map(({indices,i})=><polygon key={i} className={i===5?styles.attitudeFront:undefined} points={indices.map(n=>points[n].slice(0,2).join(",")).join(" ")} fillOpacity={i===5?.9:.55}/>)}</g>
    <g className={styles.attitudeAxes}>{axes.map((axis,i)=>{const v=coordinates(axis);return <g key={i}><line x1={origin[0]} y1={origin[1]} x2={v[0]} y2={v[1]}/><circle cx={v[0]} cy={v[1]} r={2}/><text x={v[0]+3} y={v[1]-4}>{"XYZ"[i]}</text></g>;})}</g>
    <text x={12} y={215}>{name+" · X = "+t("attitude.front")}</text>
  </svg>;
}
function Pose({value,name,t,language}:{value:Attitude;name:string;t:Translator;language:string}){
  return <div className={styles.attitudeCard} data-testid={"attitude-card-"+name}>
    <strong>{t("attitude.title")+" "+name}</strong>
    {value.state==="available"?<>
      <Body q={value.q} name={name} t={t}/>
      <p className={styles.small}>{t("attitude.sample",{time:formatNumber(value.point.t,language,3),source:formatNumber(value.point.source_time,language,6)})}</p>
      <p className={styles.small}>{t("attitude.declared")} · {value.reference} · {value.simulated?t("attitude.simulated"):t("attitude.measured")}</p>
      {value.singular&&<p role="status" className={styles.hint}>{t("attitude.singular")}</p>}
    </>:<div className={styles.attitudePlaceholder} role="status"><span>{t(value.state==="unqualified"?"attitude.unqualified":"attitude.unavailable")}</span><small>{t("attitude.reason."+value.reason)}</small></div>}
  </div>;
}
export function AttitudeComparison({a,b,time,t,language}:{a:MotionStream|null;b:MotionStream|null;time:number;t:Translator;language:string}){
  const [mode,setMode]=useState("source"),[reference,setReference]=useState<{a:Attitude;b:Attitude}|null>(null);
  const currentA=attitudeAt(a,time),currentB=attitudeAt(b,time);
  return <div data-testid="attitude-comparison">
    <Field label={t("attitude.frame")}><Select data-testid="attitude-frame" value={mode} onChange={e=>setMode(e.target.value)}><option value="source">{t("attitude.source")}</option><option value="relative">{t("attitude.relative")}</option></Select></Field>
    <Button data-testid="attitude-reference" disabled={currentA.state!=="available"&&currentB.state!=="available"} onClick={()=>{setReference({a:currentA,b:currentB});setMode("relative");}}>{t("attitude.setReference")}</Button>
    <div className={styles.attitudeGrid}><Pose value={mode==="source"?currentA:relativeAttitude(currentA,reference?.a??null)} name="A" t={t} language={language}/>{b&&<Pose value={mode==="source"?currentB:relativeAttitude(currentB,reference?.b??null)} name="B" t={t} language={language}/>}</div>
    <p className={styles.hint}>{t("attitude.limits")}</p>
  </div>;
}
