import React, {useState} from "react";
import {createRoot} from "react-dom/client";
import {LoopTimingPanel} from "../../web-next/components/loop-timing";
import {translate,type RiderLanguage} from "../../web-next/lib/i18n";
import styles from "../../web-next/rider.module.css";
import type {RiderClient} from "../../web-next/lib/use-rider";

const defaults={control_ms:500,calibration_ms:1000,overview_ms:2000,idle_ms:5000,telemetry_ms:2000,preview_ms:500};
const limits={control_ms:[500,1000],calibration_ms:[500,2000],overview_ms:[1000,5000],idle_ms:[2000,10000],telemetry_ms:[500,5000],preview_ms:[500,2000]};
const fixture = {
  schema_version:1,scope:"venom-host",revision:0,effective:defaults,persistent:false,
  fields:Object.fromEntries(Object.entries(defaults).map(([key,value])=>[key,{default:value,minimum:limits[key as keyof typeof limits][0],maximum:limits[key as keyof typeof limits][1],step:100}])),
  reads:{telemetry:{requests:4,errors:0,cache_hits:6,coalesced:1,duration_ms:{p50:25,p95:60,max:70},start_gaps_ms:{p50:2000,p95:2050,max:2100}}},
};
declare global { interface Window { timingFixture: {language:(value:RiderLanguage)=>void; conflict:()=>void; writes:unknown[]}; } }
function App(){
  const [timing,setTiming]=useState(fixture);
  const [language,setLanguage]=useState<RiderLanguage>("pl");
  const writes:unknown[] = window.timingFixture?.writes??[];
  window.timingFixture={language:setLanguage,conflict:()=>setTiming(old=>({...old,revision:old.revision+1})),writes};
  const client={
    status:{connection:{session:"fixture"},loop_timing:timing},canMutate:true,
    mutate:async(path:string,body:{revision:number;values:typeof defaults},method:string)=>{
      writes.push({path,body,method});
      setTiming(old=>({...old,revision:old.revision+1,effective:body.values}));
      return {revision:body.revision+1,effective:body.values};
    },
  } as unknown as RiderClient;
  return <main className={styles.page}><LoopTimingPanel client={client} t={key=>translate(language,key)} language={language}/></main>;
}
createRoot(document.getElementById("root")!).render(<App/>);
