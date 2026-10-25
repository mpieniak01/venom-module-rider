import React,{useState} from "react";
import {createRoot} from "react-dom/client";
import {SamplesPanel} from "../../web-next/components/samples-panel";
import {StudyRecordingBar} from "../../web-next/components/study-recording";
import {useStudyRecording} from "../../web-next/lib/use-study-recording";
import {translate} from "../../web-next/lib/i18n";
import type {RiderClient} from "../../web-next/lib/use-rider";
const localFetch=window.fetch.bind(window);
window.fetch=(input,options)=>{
 const value=typeof input==="string"?input:input instanceof URL?input.href:input.url;
 const path=new URL(value,location.href).pathname;
 if(!path.startsWith("/api/v1/rider/"))throw new Error("outside_recording_fixture");
 return localFetch(new URL(value,location.href).pathname+new URL(value,location.href).search,options);
};
const client={canMutate:true,config:{session:"fixture-source"},status:null,camera:{url:"data:image/svg+xml,"+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"><rect width="64" height="48" fill="tan"/></svg>'),captureKey:"f".repeat(32),decoded:true,stale:false},markCameraDecoded:()=>{},stopManual:async()=>{throw new Error("fixture_no_actuation");},setNotice:()=>{}} as unknown as RiderClient;
function Fixture(){
 const [samples,setSamples]=useState(false);
 const recording=useStudyRecording("fixture-robot","fixture-source");
 const t=(key:string,values?:Record<string,string|number>)=>translate("pl",key,values);
 return <main style={{padding:20}}><h1>Session archive fixture — no hardware</h1><StudyRecordingBar recording={recording} client={client} t={t}/><nav><button data-testid="fixture-control" onClick={()=>setSamples(false)}>Sterowanie</button><button data-testid="fixture-samples" onClick={()=>setSamples(true)}>Próbki</button></nav>{samples&&<SamplesPanel recording={recording} client={client} t={t} language="pl"/>}</main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
