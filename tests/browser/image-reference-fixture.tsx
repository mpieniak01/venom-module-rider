import React from "react";
import {createRoot} from "react-dom/client";
import styles from "../../web-next/rider.module.css";
import {ImageReferenceControls,ImageReferenceOverlay,useImageReference} from "../../web-next/components/image-reference";
import {translate} from "../../web-next/lib/i18n";
import {record,type JsonRecord} from "../../web-next/lib/model";
import type {RiderClient} from "../../web-next/lib/use-rider";

declare global{interface Window{referenceFixtureSaves:JsonRecord[]}}
window.referenceFixtureSaves=[];
// Even a host API-base override cannot leave this inert same-origin fixture.
const localFetch=window.fetch.bind(window);
window.fetch=(input,options)=>{
 const url=typeof input==="string"?input:input instanceof URL?input.href:input.url;
 const path=new URL(url,location.href).pathname;
 if(path!=="/api/v1/rider/navigation/image-reference")throw new Error("outside_reference_fixture");
 return localFetch(path,options);
};
function Fixture(){
 const reference=useImageReference("reference-fixture");
 const size:[number,number]=location.hash==="#scaled"?[640,480]:[320,240];
 const client={canMutate:true,currentSession:()=>"reference-fixture",mutate:async(path:string,body:JsonRecord)=>{
  if(path!=="/navigation/image-reference")throw new Error("outside_reference_fixture");
  window.referenceFixtureSaves.push(body);
  return {meta:{envelope_version:1},ok:true,data:{version:1,revision:Number(body.revision)+1,purpose:"visual_reference_only",values:record(body.values)}};
 }}as unknown as RiderClient;
 const t=(key:string,values?:Record<string,string|number>)=>translate("pl",key,values);
 return <main className={styles.page+" fixture"} style={{padding:20}}>
  <ImageReferenceControls reference={reference} client={client} size={size} t={t}/>
  <output data-testid="reference-revision">{reference.state?.revision??"unavailable"}</output>
  <output data-testid="reference-read-error">{reference.error}</output>
  <output data-testid="reference-has-draft">{String(!!reference.draft)}</output>
  <div style={{position:"relative",width:640,height:480}}><ImageReferenceOverlay reference={reference} size={size} t={t}/></div>
 </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);