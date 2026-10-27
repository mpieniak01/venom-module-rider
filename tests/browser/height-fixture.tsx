import React from "react";
import {createRoot} from "react-dom/client";
import {Posture} from "../../web-next/components/control";
import {useRider} from "../../web-next/lib/use-rider";
import {translate} from "../../web-next/lib/i18n";
import type {JsonRecord} from "../../web-next/lib/model";

const state={calls:[] as JsonRecord[],target:null as number|null,generation:"g0",owner:null as string|null,holder:null as string|null,phase:"DISARMED",supported:true,serial:0,operation:""};
declare global {interface Window {heightFixture:typeof state}}
window.heightFixture=state;
const settings={revision:45,parameter_schema:{actuator_fields:{height:{type:"integer",minimum:75,maximum:115,step:1,display_unit:"SDK"},balance:{type:"boolean"}}},
 control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
const safety=()=>({state:state.phase,owner:state.owner,generation:state.generation,last_seq:0,ready:true,stale:false,observed_at:Date.now()/1000,
 operator_intervention_version:1,operator_height_intervention_version:state.supported?1:undefined,panel_control:{version:1,holder:state.holder},
 operator_control:{version:1,state:"paused",generation:state.generation,operation_id:state.operation},actuators:{height:{value:state.target}},capabilities:{height:{available:true},move:{available:false}}});
window.fetch=async(input,options)=>{
 const url=new URL(typeof input==="string"?input:input instanceof URL?input.href:input.url,location.href);
 if(!url.pathname.startsWith("/api/v1/rider/"))throw new Error("Outside inert height fixture");
 const path=url.pathname.slice("/api/v1/rider".length),method=options?.method??"GET";
 const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers:{"Content-Type":"application/json"}});
 if(method!=="GET"){
  const body=JSON.parse(String(options?.body??"{}"));state.calls.push(body);
  if(path!=="/control"||body.action!=="height")throw new Error("Unexpected actuator in inert height fixture");
  if(body.generation!==state.generation||state.phase==="ESTOP")return response({error:"stale_generation"},409);
  if(!body.operator_consent||body.operator_intervention_version!==1||body.owner!=="manual"||!body.intervene)throw new Error("Height did not use explicit atomic consent");
  const origin=state.generation;state.generation="g"+(++state.serial);state.phase="ARMED";state.owner="manual";state.holder=body.panel_id;state.target=body.height;state.operation=body.request_id;
  return response({ok:true,confirmed:true,server_time:Date.now()/1000,safety:safety(),ack:{request_id:body.request_id,action:"height",source:"manual",seq:body.seq,status:"executed",accepted:true,command_generation:origin,generation:state.generation}});
 }
 if(path==="/config")return response({devices:[{id:"height-fixture",mode:"demo",label:"Height fixture"}],device_id:"height-fixture",session:"height-fixture"});
 if(path==="/status")return response({connection:{configured:true,status:"online",device_id:"height-fixture",mode:"demo",session:"height-fixture"},polling:{control_ms:100},control:{server_time:Date.now()/1000,safety:safety()},settings,errors:{},health:{},resources:{},catalog:{features:[]},logic:{},face:{}});
 return response({});
};
function Fixture(){const client=useRider("control");return <main style={{maxWidth:420,padding:16}}>
 <Posture client={client} t={(key,values)=>translate("pl",key,values)} language="pl" compact/>
 <button data-testid="height-after-figure" onClick={()=>{state.phase="DISARMED";state.owner=null;state.holder=null;state.generation="g"+(++state.serial);void client.poll();}}>After completed figure</button>
 <button data-testid="height-estop" onClick={()=>{state.phase="ESTOP";state.owner=null;void client.poll();}}>E-Stop fixture</button>
 <button data-testid="height-old-pi" onClick={()=>{state.supported=false;state.phase="DISARMED";state.owner=null;void client.poll();}}>Old Pi fixture</button>
 </main>;}
createRoot(document.getElementById("root")!).render(<Fixture/>);
