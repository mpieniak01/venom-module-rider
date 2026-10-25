import {useCallback,useEffect,useMemo,useState} from "react";
import {apiFetch} from "@/lib/api-client";
import {getApiBaseUrl} from "@/lib/env";
import {errorCode} from "../lib/model";
import {readVideoPage,VIDEO_PAGE_SIZE,videoClock,videoFramePath,type VideoFrame,type VideoPage} from "../lib/analysis-video";
import type {SessionVideo} from "../lib/samples";
import type {Translator} from "../lib/i18n";
import {Button,Field,Input} from "./primitives";
import {VideoFramePair,type DisplayFrame,type VideoComparison} from "./video-frame-pair";
import styles from "../rider.module.css";

export function SourceVideoPlayer({session,video,position,onPositionChange,comparison=null,onDisplayed,t}:{session:string;video:SessionVideo;position:{index:number;playing:boolean};onPositionChange:(value:{index:number;playing:boolean})=>void;comparison?:VideoComparison|null;onDisplayed?:(index:number)=>void;t:Translator}){
  const [frames,setFrames]=useState<Map<number,VideoFrame>>(new Map());
  const [meta,setMeta]=useState<VideoPage|null>(null);
  const {index,playing}=position;
  const [failure,setFailure]=useState<{offset:number;code:string}|null>(null),[attempt,setAttempt]=useState(0);
  const [shown,setShown]=useState<DisplayFrame|null>(null);
  const total=meta?.total??video.frames,current=frames.get(index);
  const offset=Math.floor(index/VIDEO_PAGE_SIZE)*VIDEO_PAGE_SIZE;
  const wantedOffset=playing&&index%VIDEO_PAGE_SIZE===VIDEO_PAGE_SIZE-1&&index+1<total?offset+VIDEO_PAGE_SIZE:offset;
  const error=failure&&(failure.offset===offset||failure.offset===wantedOffset)?failure.code:"";
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

  const imageKey=session+":"+index+":"+(current?.raw_sha256??"")+":"+(comparison?.runId??"raw");
  const ready=shown?.key===imageKey;
  const next=frames.get(index+1);
  useEffect(()=>{
    if(!playing||!current||!ready)return;
    if(index+1>=total)return;
    if(!next)return;
    const timer=setTimeout(()=>onPositionChange({index:index+1,playing:index+2<total}),Math.max(10,(next.source_time_s-current.source_time_s)*1000));
    return()=>clearTimeout(timer);
  },[playing,current,next,index,total,ready,onPositionChange]);
  const src=useMemo(()=>getApiBaseUrl()+videoFramePath(session,index)+"?sha256="+(current?.raw_sha256??""),[session,index,current?.raw_sha256]);
  const candidate=useMemo<DisplayFrame|null>(()=>current?{key:imageKey,index,src,width:4,height:3,raw_sha256:current.raw_sha256,comparison}:null,[current,imageKey,index,src,comparison]);
  const promote=useCallback((frame:DisplayFrame)=>setShown(old=>old?.key===frame.key?old:frame),[]);
  useEffect(()=>{if(shown)onDisplayed?.(shown.index);},[shown,onDisplayed]);
  // Keep the decoded element mounted while the next JPEG loads. Its key stays
  // in this same list, so promoting a pending image never fetches it twice.
  const images:DisplayFrame[]=shown&&shown.key!==imageKey?[shown,...(candidate?[candidate]:[])]:candidate?[candidate]:[];
  const visible=shown?frames.get(shown.index):undefined;
  const move=(value:number)=>onPositionChange({index:Math.max(0,Math.min(total-1,value)),playing:false});
  return <section data-testid="source-video-player" aria-label={t("analysis.video.title")}>
    <h4>{t("analysis.video.title")}</h4>
    <p className={styles.hint}>{t("analysis.video.readOnly",{fps:video.target_fps})}</p>
    {error&&<p role="alert" data-testid="source-video-error">{t("analysis.video.failure")} · <code>{error}</code> <Button onClick={()=>{setFrames(new Map());setFailure(null);setAttempt(value=>value+1);}}>{t("refresh")}</Button></p>}
    <div data-testid="source-video-viewport" className={styles.videoPairStack} aria-busy={!ready}>
      {!images.length&&<div className={styles.videoPair} aria-hidden="true">{["samples.original","analysis.video.processed"].map(label=><div className={styles.videoPairCell} key={label}><p>{t(label)}</p><div className={styles.videoPairViewport} style={{aspectRatio:"4 / 3"}}/>{label==="analysis.video.processed"&&<p className={styles.small}>&nbsp;</p>}</div>)}</div>}
      {images.map(image=><VideoFramePair key={image.key} frame={image} session={session} active={image.key===shown?.key} onReady={promote} onFailure={()=>{setFailure({offset,code:"analysis_video_frame_unavailable"});onPositionChange({index,playing:false});}} t={t}/>)}
      {(!current||!ready||loading)&&!error&&<span className={styles.sourceVideoLoading} role="status">{t("analysis.artifactLoading")}</span>}
    </div>
    <div className={styles.flex}>
      <Button data-testid="source-video-play" disabled={!!error||!current} onClick={()=>onPositionChange({index:index+1>=total?0:index,playing:!playing})}>{t(playing?"analysis.video.pause":"analysis.video.play")}</Button>
      <Button data-testid="source-video-previous" disabled={index===0} onClick={()=>move(index-1)}>{t("samples.previous")}</Button>
      <Button data-testid="source-video-next" disabled={index+1>=total} onClick={()=>move(index+1)}>{t("samples.next")}</Button>
      <span data-testid="source-video-position">{t("analysis.video.position",{frame:(shown?.index??index)+1,total,time:videoClock(visible?.source_time_s??NaN),duration:videoClock(meta?.duration_s??video.duration_s)})}</span>
    </div>
    <Field label={t("analysis.video.frame")}><Input aria-label={t("analysis.video.frame")} data-testid="analysis-frame" type="number" min={1} max={total} value={index+1} onChange={event=>{const value=Number(event.target.value)-1;if(Number.isSafeInteger(value)&&value>=0&&value<total)move(value);}}/></Field>
    <Field label={t("analysis.video.seek")}><Input data-testid="source-video-seek" type="range" min={0} max={Math.max(0,total-1)} step={1} value={index} onChange={event=>move(Number(event.target.value))}/></Field>
  </section>;
}
