/* eslint-disable @next/next/no-img-element -- Immutable JPEG frames from a stored recording. */
import {useEffect,useMemo,useState} from "react";
import {apiFetch} from "@/lib/api-client";
import {getApiBaseUrl} from "@/lib/env";
import {errorCode} from "../lib/model";
import {readVideoPage,VIDEO_PAGE_SIZE,videoClock,videoFramePath,type VideoFrame,type VideoPage} from "../lib/analysis-video";
import type {SessionVideo} from "../lib/samples";
import type {Translator} from "../lib/i18n";
import {Button,Field,Input} from "./primitives";
import styles from "../rider.module.css";

export function SourceVideoPlayer({session,video,position,onPositionChange,t}:{session:string;video:SessionVideo;position:{index:number;playing:boolean};onPositionChange:(value:{index:number;playing:boolean})=>void;t:Translator}){
  const [frames,setFrames]=useState<Map<number,VideoFrame>>(new Map());
  const [meta,setMeta]=useState<VideoPage|null>(null);
  const {index,playing}=position;
  const [failure,setFailure]=useState<{offset:number;code:string}|null>(null),[attempt,setAttempt]=useState(0);
  const [decoded,setDecoded]=useState("");
  const total=meta?.total??video.frames,current=frames.get(index);
  const offset=Math.floor(index/VIDEO_PAGE_SIZE)*VIDEO_PAGE_SIZE;
  const wantedOffset=playing&&index%VIDEO_PAGE_SIZE===VIDEO_PAGE_SIZE-1&&index+1<total?offset+VIDEO_PAGE_SIZE:offset;
  const error=failure?.offset===offset?failure.code:"";
  const loading=!frames.has(wantedOffset)&&!error;

  useEffect(()=>{
    if(frames.has(wantedOffset))return;
    const abort=new AbortController();
    void apiFetch<unknown>(videoFramePath(session,0).replace(/\/0$/,"")+"?offset="+wantedOffset+"&limit="+VIDEO_PAGE_SIZE,{signal:abort.signal,cache:"no-store"})
      .then(value=>{
        if(abort.signal.aborted)return;
        const page=readVideoPage(value,session,wantedOffset);
        if(page.total!==video.frames)throw new Error("analysis_video_metadata_invalid");
        setMeta(page);setFailure(null);setFrames(old=>new Map([...old,...page.frames.map(frame=>[frame.index,frame] as const)]));
      }).catch(err=>{if(!abort.signal.aborted){setFailure({offset:wantedOffset,code:errorCode(err)});onPositionChange({index,playing:false});}});
    return()=>abort.abort();
  },[session,wantedOffset,attempt,frames,video.frames,index,onPositionChange]);

  const imageKey=session+":"+index;
  const ready=decoded===imageKey;
  const next=frames.get(index+1);
  useEffect(()=>{
    if(!playing||!current||!ready)return;
    if(index+1>=total)return;
    if(!next)return;
    const timer=setTimeout(()=>onPositionChange({index:index+1,playing:index+2<total}),Math.max(10,(next.source_time_s-current.source_time_s)*1000));
    return()=>clearTimeout(timer);
  },[playing,current,next,index,total,ready,onPositionChange]);
  const src=useMemo(()=>getApiBaseUrl()+videoFramePath(session,index)+"?sha256="+(current?.raw_sha256??""),[session,index,current?.raw_sha256]);
  const move=(value:number)=>onPositionChange({index:Math.max(0,Math.min(total-1,value)),playing:false});
  return <section data-testid="source-video-player" aria-label={t("analysis.video.title")}>
    <h4>{t("analysis.video.title")}</h4>
    <p className={styles.hint}>{t("analysis.video.readOnly",{fps:video.target_fps})}</p>
    {error&&<p role="alert" data-testid="source-video-error">{t("analysis.video.failure")} · <code>{error}</code> <Button onClick={()=>{setFrames(new Map());setFailure(null);setAttempt(value=>value+1);}}>{t("refresh")}</Button></p>}
    {current&&<img key={imageKey} data-testid="source-video-image" data-source-index={index} className={styles.analysisImage} style={{maxWidth:640}} src={src} alt={t("analysis.video.sourceFrame",{frame:index+1})} onLoad={()=>setDecoded(imageKey)} onError={()=>{setFailure({offset,code:"analysis_video_frame_unavailable"});onPositionChange({index,playing:false});}}/>}
    {(!current||!ready)&&!error&&<p role="status">{t("analysis.video.loading")}</p>}
    <div className={styles.flex}>
      <Button data-testid="source-video-play" disabled={!!error||!current} onClick={()=>onPositionChange({index:index+1>=total?0:index,playing:!playing})}>{t(playing?"analysis.video.pause":"analysis.video.play")}</Button>
      <Button data-testid="source-video-previous" disabled={index===0} onClick={()=>move(index-1)}>{t("samples.previous")}</Button>
      <Button data-testid="source-video-next" disabled={index+1>=total} onClick={()=>move(index+1)}>{t("samples.next")}</Button>
      <span data-testid="source-video-position">{t("analysis.video.position",{frame:index+1,total,time:videoClock(current?.source_time_s??NaN),duration:videoClock(meta?.duration_s??video.duration_s)})}</span>
    </div>
    <Field label={t("analysis.video.seek")}><Input data-testid="source-video-seek" type="range" min={0} max={Math.max(0,total-1)} step={1} value={index} onChange={event=>move(Number(event.target.value))}/></Field>
    {loading&&current&&ready&&<span className={styles.small} role="status">{t("analysis.video.loading")}</span>}
  </section>;
}
