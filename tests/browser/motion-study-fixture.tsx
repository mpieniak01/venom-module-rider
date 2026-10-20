import React,{useState} from "react";
import {createRoot} from "react-dom/client";
import {useStudyRecording} from "../../web-next/lib/use-study-recording";
import {StudyRecordingBar} from "../../web-next/components/study-recording";
import {Calibration} from "../../web-next/components/calibration";
import {translate,type RiderLanguage} from "../../web-next/lib/i18n";
import type {RiderClient} from "../../web-next/lib/use-rider";
import styles from "../../web-next/rider.module.css";
declare global { interface Window { studyFixture:{language:(value:RiderLanguage)=>void}; } }
function App({initial}:{initial:{config:unknown;status:unknown}}){
  const [language,setLanguage]=useState<RiderLanguage>("pl");
  window.studyFixture={language:setLanguage};
  const client={
    ...initial,canMutate:true,canMove:false,canNavigate:false,canContinuous:false,canArm:false,activeDirection:null,busy:null,
    hold:{start:()=>{},end:()=>{},release:()=>{},releasePointer:()=>{},toggle:()=>{}},
    command:async()=>{throw new Error("Unexpected fixture actuator command");},
    mutate:async()=>{throw new Error("Unexpected fixture mutation");},
  } as unknown as RiderClient;
  const recording=useStudyRecording(client.config?.device_id??null,client.config?.session??null);
  return <main className={styles.page}><header className={styles.header}><StudyRecordingBar recording={recording} client={client} t={key=>translate(language,key)}/></header><Calibration recording={recording} client={client} t={(key,values)=>translate(language,key,values)} language={language}/></main>;
}
void fetch("/fixture").then(r=>r.json()).then(initial=>createRoot(document.getElementById("root")!).render(<App initial={initial}/>));
