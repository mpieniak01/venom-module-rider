import styles from "../../web-next/rider.module.css";
import React, {useState} from "react";
import {createRoot} from "react-dom/client";
import original from "../fixtures/policy-catalog-v2.json";
const definition=structuredClone(original);
const fluid=definition.profiles.find(p=>p.id==="fluid")!;
definition.profiles.push({...fluid,id:"room_fluid",parameters:{...fluid.parameters,object_tracking_enabled:true,object_policy_enabled:true,thin_span_fraction:1}} as typeof fluid);
Object.assign(definition.parameter_schema,{object_policy_enabled:{kind:"bool",min:0,max:1,step:1,group:"objects"}});
import {NavigationProfiles} from "../../web-next/components/navigation-profiles";
import {translate,type RiderLanguage} from "../../web-next/lib/i18n";
import {record,type JsonRecord} from "../../web-next/lib/model";
import type {RiderClient} from "../../web-next/lib/use-rider";
let revision=9, selection:JsonRecord={...definition.active};
const calls:JsonRecord[]=[];
declare global{interface Window{parameterFixtureCalls:JsonRecord[]}}
window.parameterFixtureCalls=calls;
function snapshot():JsonRecord{
 const preset=definition.profiles.find(p=>p.id===selection.profile_id)!;
 return {revision,mode:"observe",perception:{engine:"depth_plane"},behavior:structuredClone(selection),behavior_catalog:{...structuredClone(definition),active:structuredClone(selection),effective_policy_sha256:revision.toString(16).padStart(64,"0"),active_base_parameters:{...preset.parameters},active_parameters:{...preset.parameters,...record(selection.overrides)}}};
}
function Fixture(){
 const [policy,setPolicy]=useState(snapshot),[lang,setLang]=useState<RiderLanguage>("pl");
 const [online,setOnline]=useState(true);
 const client={config:{session:"fixture"},currentSession:()=>"fixture",canMutate:true,controlFresh:true,
 pipeline:{status:{profile:{analysis_location:"venom"}}},status:{connection:{session:"fixture",status:online?"online":"offline"},control:{safety:{state:"DISARMED",moving:false}},errors:{}},
 mutate:async(path:string,body:JsonRecord)=>{
  if(path!=="/navigation/config"||body.revision!==revision)throw {data:{detail:{code:"navigation_configuration_conflict"}}};
  calls.push({path,body});selection=record(record(body.values).behavior);revision++;const config=snapshot();setPolicy(config);return {config};
 }}as unknown as RiderClient;
 return <main className={styles.page+" fixture"} style={{maxWidth:1100,margin:"auto",padding:16}}>
 <NavigationProfiles client={client} policy={policy} goals={{}} locked={false} t={(k,v)=>translate(lang,k,v)} onApplied={setPolicy} onRefresh={()=>setPolicy(snapshot())}/>
 <button data-testid="external-change" onClick={()=>{revision++;setPolicy(snapshot());}}>External revision</button>
 <button data-testid="fixture-offline" onClick={()=>setOnline(!online)}>Connection</button>
 <select data-testid="fixture-language" value={lang} onChange={e=>setLang(e.target.value as RiderLanguage)}><option value="pl">PL</option><option value="en">EN</option><option value="de">DE</option></select>
 </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);