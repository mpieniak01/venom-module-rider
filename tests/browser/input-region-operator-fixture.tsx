import React,{useState} from "react";
import {createRoot} from "react-dom/client";
import {ModelTuning} from "../../web-next/components/model-tuning";
import {translate} from "../../web-next/lib/i18n";
import {record,type JsonRecord} from "../../web-next/lib/model";
import type {RiderClient} from "../../web-next/lib/use-rider";
import styles from "../../web-next/rider.module.css";
declare global{interface Window{inputRegionWrites:JsonRecord[]}}
window.inputRegionWrites=[];
function Fixture(){
 const [policy,setPolicy]=useState<JsonRecord>({revision:9,input_region_supported:true,corridor:[.35,.8,.65,.95],perception:{engine:"depth_plane",model_id:"depth-anything-v2-indoor-small",residual_limit:.06,outlier_limit:.005,slope_min:2}});
 const client={config:{session:"fixture"},currentSession:()=>"fixture",canMutate:true,controlFresh:true,pipeline:{status:{revision:1,profile:{analysis_location:"venom"}}},status:{connection:{session:"fixture",status:"online"},control:{safety:{state:"DISARMED",moving:false}},errors:{}},invalidatePerceptionPreview:()=>{},mutate:async(path:string,body:JsonRecord)=>{
 if(path!=="/navigation/config"||body.revision!==policy.revision)throw new Error("wrong_revision");
 window.inputRegionWrites.push(body);const config={...policy,...record(body.values),revision:Number(policy.revision)+1};setPolicy(config);return {meta:{envelope_version:1},ok:true,data:{config}};
 }}as unknown as RiderClient;
 return <main className={styles.page} style={{padding:24}}><ModelTuning client={client} policy={policy} goals={{}} locked={false} t={(k,v)=>translate("pl",k,v)} onApplied={setPolicy} onRefresh={()=>{}}/><button data-testid="external-revision" onClick={()=>setPolicy({...policy,revision:Number(policy.revision)+1})}>External revision</button></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
