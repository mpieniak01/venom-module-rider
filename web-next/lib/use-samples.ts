"use client";
import { createBrowserUuid } from "@/lib/optional-modules/transport";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { getApiBaseUrl } from "@/lib/env";
import { errorCode } from "./model";
import { captureIntent, captureOutcome, recoverCapture, SAMPLES_BASE, type CaptureIntent, type CaptureOutcome, type Collection } from "./samples";
import type { Frame } from "./use-rider";

const STORAGE="venom.rider.pending-capture";
function remember(intent:CaptureIntent|null) {
  try { if(intent)sessionStorage.setItem(STORAGE,JSON.stringify(intent));else sessionStorage.removeItem(STORAGE); } catch { /* Data recovery remains available through the request ID. */ }
}
function finishDetached(id:string) {
  void fetch(getApiBaseUrl()+SAMPLES_BASE+"/sessions/"+id+"/end",{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({interrupted:true}),keepalive:true,
  }).catch(()=>undefined);
}
export function useSamples(deviceId:string|null, previewVisible:boolean, purpose?:string, sourceSession?:string|null) {
  const [session,setSession]=useState<Collection|null>(null);
  const [outcome,setOutcome]=useState<CaptureOutcome|null>(null);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const sessionRef=useRef<Collection|null>(null);
  const last=useRef<CaptureIntent|null>(null);
  const epoch=useRef(0);
  const alive=useRef(true);
  const unresolved=useRef(false);
  const invalidate=useCallback(()=>++epoch.current,[]);
  const syncSession=useCallback((next:Collection|null)=>{sessionRef.current=next;setSession(next);},[]);
  const check=useCallback(async()=>{
    const intent=last.current;
    if(!intent)return;
    try {
      const result=captureOutcome(await apiFetch(SAMPLES_BASE+"/requests/"+intent.capture_request_id));
      if(alive.current&&last.current===intent){unresolved.current=["queued","running"].includes(result.state);setOutcome(result);setError(result.error??"");}
    }catch(err){if(alive.current&&last.current===intent){setOutcome({id:intent.capture_request_id,state:"unknown"});setError(errorCode(err));}}
  },[]);
  const end=useCallback(async(interrupted=false)=>{
    const current=sessionRef.current;
    if(current?.state!=="collecting")return;
    const requestEpoch=invalidate();syncSession(null);setBusy(true);
    try{const closed=await apiFetch<Collection>(SAMPLES_BASE+"/sessions/"+current.session_id+"/end",{method:"POST",body:JSON.stringify({interrupted})});if(alive.current&&requestEpoch===epoch.current)syncSession(closed);}
    catch(err){if(alive.current&&requestEpoch===epoch.current){syncSession(current);setError(errorCode(err));}}
    finally{if(alive.current&&requestEpoch===epoch.current)setBusy(false);}
  },[invalidate,syncSession]);
  const start=useCallback(async(kind:"manual"|"sequence")=>{
    if(!deviceId)return;
    if(sessionRef.current?.state==="collecting")await end();
    const requestEpoch=invalidate();
    setBusy(true);setError("");
    try{
      const next=await apiFetch<Collection>(SAMPLES_BASE+"/sessions",{method:"POST",body:JSON.stringify({
        request_id:createBrowserUuid(),source_device_id:deviceId,
        policy:{kind,duration_s:10,interval_ms:purpose?.startsWith("motion-study-v1:")?1000:200,max_frames:purpose?.startsWith("motion-study-v1:")?150:50,...(purpose?.startsWith("motion-study-v1:")?{record_images:true}:{}),...(purpose?{scene_group:purpose}:{})},
      })});
      if(requestEpoch!==epoch.current||!alive.current){finishDetached(next.session_id);return;}
      syncSession(next);
    }catch(err){if(alive.current&&requestEpoch===epoch.current)setError(errorCode(err));}
    finally{if(alive.current&&requestEpoch===epoch.current)setBusy(false);}
  },[deviceId,end,invalidate,syncSession,purpose]);
  const save=useCallback(async(frame:Frame|null)=>{
    if(unresolved.current)return;
    const intent=captureIntent(sessionRef.current,frame,createBrowserUuid());
    if(!intent)return;
    unresolved.current=true;last.current=intent;remember(intent);setError("");
    setOutcome({id:intent.capture_request_id,state:"submitting"});
    try{
      const result=captureOutcome(await apiFetch(SAMPLES_BASE+"/capture",{method:"POST",body:JSON.stringify(intent)}));
      if(alive.current&&last.current===intent){unresolved.current=["queued","running"].includes(result.state);setOutcome(result);setError(result.error??"");}
    }catch(err){if(alive.current&&last.current===intent){setOutcome({id:intent.capture_request_id,state:"unknown"});setError(errorCode(err));}}
  },[]);
  const dismiss=useCallback(()=>{unresolved.current=false;last.current=null;remember(null);setOutcome(null);setError("");},[]);
  useEffect(()=>{
    alive.current=true;invalidate();syncSession(null);setBusy(false);setError("");setOutcome(null);unresolved.current=false;last.current=null;
    try{const previous=recoverCapture(JSON.parse(sessionStorage.getItem(STORAGE)??"null"));if(previous){unresolved.current=true;last.current=previous;void check();}}catch{/* Invalid browser metadata is ignored. */}
    const hidden=()=>{if(document.hidden&&sessionRef.current?.state==="collecting")void end(true);};
    document.addEventListener("visibilitychange",hidden);
    return()=>{alive.current=false;invalidate();document.removeEventListener("visibilitychange",hidden);const current=sessionRef.current;sessionRef.current=null;if(current?.state==="collecting")finishDetached(current.session_id);};
  },[check,deviceId,end,invalidate,syncSession,sourceSession]);
  useEffect(()=>{if(!previewVisible&&sessionRef.current?.state==="collecting")void end(true);},[previewVisible,end]);
  const activeId=session?.state==="collecting"?session.session_id:null;
  const pending=["queued","running","submitting"].includes(outcome?.state??"");
  useEffect(()=>{
    if(!activeId&&!pending)return;
    let stopped=false;
    let timer:ReturnType<typeof setTimeout>|undefined;
    const tick=async()=>{
      if(activeId){
        try{
          const row=await apiFetch<{session:Collection}>(SAMPLES_BASE+"/sessions/"+activeId+"?limit=1");
          if(!stopped&&sessionRef.current?.session_id===activeId)syncSession(row.session);
        }catch(err){if(!stopped)setError(errorCode(err));}
      }
      if(!stopped&&unresolved.current)await check();
      if(!stopped)timer=setTimeout(()=>void tick(),500);
    };
    timer=setTimeout(()=>void tick(),500);
    return()=>{stopped=true;clearTimeout(timer);};
  },[activeId,check,pending,syncSession]);
  return {session,outcome,error,busy,start,end,save,check,dismiss};
}
