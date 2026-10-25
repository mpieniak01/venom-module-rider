import {useMemo} from "react";
import {analysisState,nextStateFrame,stateHistogram,timelinePaths,type AnalysisRow,type AnalysisState} from "../lib/analysis-video";
import type {Translator} from "../lib/i18n";
import {Button,Field,Input} from "./primitives";
import styles from "../rider.module.css";

const colors:Record<AnalysisState,string>={clear:"#16a34a",unknown:"#eab308",obstacle:"#dc2626"};
export function AnalysisTimeline({rows,index,onSelect,t,language}:{rows:AnalysisRow[];index:number;onSelect:(index:number)=>void;t:Translator;language:string}){
  const histogram=useMemo(()=>stateHistogram(rows),[rows]);
  const paths=useMemo(()=>timelinePaths(rows),[rows]);
  const percentage=(count:number)=>new Intl.NumberFormat(language,{maximumFractionDigits:1}).format(rows.length?count/rows.length*100:0);
  const states=["clear","unknown","obstacle"] as const;
  return <section data-testid="analysis-timeline" aria-label={t("analysis.histogram.title")}>
    <h4>{t("analysis.histogram.title")}</h4><p className={styles.hint}>{t("analysis.histogram.hint")}</p>
    <div className={styles.flex} style={{flexWrap:"wrap"}}>{states.map(state=><Button key={state} data-testid={"analysis-histogram-"+state} disabled={!histogram[state]} style={{borderColor:colors[state]}} onClick={()=>{const next=nextStateFrame(rows,state,index);if(next!==null)onSelect(next);}}>
      <span aria-hidden="true" style={{background:colors[state],display:"inline-block",width:10,height:10,borderRadius:2}}/> {t("analysis.histogram.entry",{state:t("samples.value."+state),count:histogram[state],percent:percentage(histogram[state])})}
    </Button>)}</div>
    {!!rows.length&&<>
      <svg data-testid="analysis-state-axis" viewBox={"0 0 "+rows.length+" 1"} preserveAspectRatio="none" style={{display:"block",width:"100%",height:28,cursor:"pointer",marginTop:10}} aria-hidden="true" onClick={event=>{const bounds=event.currentTarget.getBoundingClientRect();onSelect(Math.max(0,Math.min(rows.length-1,Math.floor((event.clientX-bounds.left)/bounds.width*rows.length))));}}>
        {states.map(state=><path key={state} d={paths[state]} fill={colors[state]}/>)}
      </svg>
      <Field label={t("analysis.histogram.seek")}><Input data-testid="analysis-state-seek" type="range" min={0} max={rows.length-1} step={1} value={index} onChange={event=>onSelect(Number(event.target.value))}/></Field>
      <p data-testid="analysis-timeline-position">{t("analysis.histogram.position",{frame:index+1,total:rows.length,state:t("samples.value."+analysisState(rows[index]))})}</p>
    </>}
  </section>;
}
