import React from "react";
import {createRoot} from "react-dom/client";
import {FiguresPanel} from "../../web-next/components/figures";
import {translate} from "../../web-next/lib/i18n";
import type {RiderClient} from "../../web-next/lib/use-rider";
const state={calls:[] as {kind:string;id?:string}[],complete:true,run:{phase:"completed",step:0,step_count:4} as Record<string,unknown>,counter:0};
declare global {interface Window{figureFixture:typeof state}}
window.figureFixture=state;
const ids=["sway","vertical","forward_backward","snake","lift_rotate","circular_sway"];
window.fetch=async(input,options)=>{
 const url=new URL(typeof input==="string"?input:input instanceof URL?input.href:input.url,location.href);
 if(!url.pathname.startsWith("/api/v1/rider/motion/figures"))throw new Error("Outside inert figure fixture");
 const path=url.pathname,method=options?.method??"GET";state.calls.push({kind:method+" "+path});
 let value:unknown;
 if(path.endsWith("/cancel")){state.run={...state.run,phase:"cancelled"};value={ok:true};}
 else if(path.endsWith("/figures"))value={version:1,implementation:"own_sequence",figures:ids.map(id=>({id,version:1,implementation:"own_sequence",qualification:"manual_trial",physical_completion:"unverified",duration_ms:4000,available:true,initial_height:id==="vertical"?90:null,initial_roll:id==="sway"?0:null,height_range:[85,95],roll_range:[-3,3],maximum_speed:.25,intensity_factor:.3}))};
 else value={version:1,run:state.run,stale:false};
 return new Response(JSON.stringify({meta:{envelope_version:1,contract_id:"rider.figures",contract_version:1},ok:true,data:value,error:null}),{status:200,headers:{"Content-Type":"application/json"}});
};
const client={
 status:{control:{safety:{state:"DISARMED",figures:{version:1}}}},canMutate:true,controlFresh:true,busy:null,
 currentSession:()=>"fixture-only",
 startFigure:async(id:string)=>{state.calls.push({kind:"start",id});state.counter=0;state.run={phase:"running",step:1,step_count:4,physical_completion:"unverified"};return "fixture-"+Date.now();},
 keepFigure:async()=>{state.calls.push({kind:"keepalive"});state.counter++;if(state.complete&&state.counter>=4)state.run={...state.run,phase:"completed",step:4};},
} as unknown as RiderClient;
createRoot(document.getElementById("root")!).render(<main style={{maxWidth:420,padding:16}}><FiguresPanel client={client} t={(key,vars)=>translate("pl",key,vars)}/></main>);
