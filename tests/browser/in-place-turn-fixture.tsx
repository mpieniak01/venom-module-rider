import React,{useState} from "react";
import {createRoot} from "react-dom/client";
import {ManualPad} from "../../web-next/components/control";
import {HoldController,type MovementIntent} from "../../web-next/lib/hold-controller";
import {translate} from "../../web-next/lib/i18n";
import type {Direction} from "../../web-next/lib/model";
import type {RiderClient} from "../../web-next/lib/use-rider";
import styles from "../../web-next/rider.module.css";
const calls:(MovementIntent&{at:number})[]=[];
declare global {interface Window {spinCalls:typeof calls}}
window.spinCalls=calls;
function Fixture(){
 const [direction,setDirection]=useState<Direction|null>(null);
 const [mode,setMode]=useState<"hold"|"continuous"|null>(null);
 const [turnMode,setTurnMode]=useState<"in_place"|null>(null);
 const [supported,setSupported]=useState(true);
 const [hold]=useState(()=>new HoldController({
  timing:()=>({click_threshold_ms:180,hold_renewal_ms:150,hold_timeout_ms:500,request_ttl_ms:500,supported:["pulse","hold","release"]}),
  canMove:()=>true,onActive:setDirection,onMode:setMode,onTurnMode:setTurnMode,onError:()=>undefined,
  send:async intent=>{calls.push({...intent,at:performance.now()});if(intent.mode==="hold")await new Promise(resolve=>setTimeout(resolve,600));}
 }));
 const client={activeDirection:direction,manualMode:mode,manualTurnMode:turnMode,hold,canNavigate:true,canContinuous:true,
 status:{control:{safety:{state:"ARMED",owner:"manual"}},settings:{control_modes:{in_place_turn_version:supported?1:undefined},directions:Object.fromEntries(["forward","backward","left","right"].map(d=>[d,{speed:.5}]))}},
 stopManual:async()=>{hold.cancel();}
 } as unknown as RiderClient;
 return <main className={styles.page} style={{padding:16,maxWidth:440}}>
 <aside className={styles.tuningRail}><div className={styles.tuningBody}><ManualPad client={client} t={(k,v)=>translate("pl",k,v)} language="pl" compact minimal/></div></aside>
 <button data-testid="old-pi" onClick={()=>setSupported(false)}>Old Pi</button>
 </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
