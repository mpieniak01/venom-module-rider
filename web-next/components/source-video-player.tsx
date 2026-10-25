import {useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState} from "react";
import {apiFetch} from "@/lib/api-client";
import {getApiBaseUrl} from "@/lib/env";
import {errorCode} from "../lib/model";
import {readVideoPage,VIDEO_PAGE_SIZE,videoClock,videoFramePath,type VideoFrame,type VideoPage} from "../lib/analysis-video";
import type {SessionVideo} from "../lib/samples";
import type {Translator} from "../lib/i18n";
import {Button,Field,Input,Select} from "./primitives";
import {VideoFramePair,type DisplayFrame,type VideoComparison} from "./video-frame-pair";
import styles from "../rider.module.css";

export type SourcePlaybackRate=1|2|5;

export function SourceVideoPlayer({session,video,position,onPositionChange,rate,onRateChange,comparison=null,onDisplayed,t}:{session:string;video:SessionVideo;position:{index:number;playing:boolean};onPositionChange:(value:{index:number;playing:boolean})=>void;rate:SourcePlaybackRate;onRateChange:(rate:SourcePlaybackRate)=>void;comparison?:VideoComparison|null;onDisplayed?:(index:number)=>void;t:Translator}){
  const [frames,setFrames]=useState<Map<number,VideoFrame>>(new Map());
  const [meta,setMeta]=useState<VideoPage|null>(null);
  const {index,playing}=position;
  const [failure,setFailure]=useState<{offset:number;code:string}|null>(null),[attempt,setAttempt]=useState(0);
  const [shown,setShown]=useState<DisplayFrame|null>(null);
  const [heldNext,setHeldNext]=useState("");
  const [decoded,setDecoded]=useState(new Map<string,DisplayFrame>());
  const timeline=useRef<{wall:number;source:number;rate:number;index:number}|null>(null);
  const total=meta?.total??video.frames,current=frames.get(index);
  const offset=Math.floor(index/VIDEO_PAGE_SIZE)*VIDEO_PAGE_SIZE;
  const wantedOffset=playing&&index%VIDEO_PAGE_SIZE===VIDEO_PAGE_SIZE-1&&index+1<total?offset+VIDEO_PAGE_SIZE:offset;
  const error=failure&&(failure.offset===offset||failure.offset===wantedOffset)?failure.code:"";

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
  const expectedKey=useRef(imageKey);
  useLayoutEffect(()=>{expectedKey.current=imageKey;},[imageKey]);
  const ready=shown?.key===imageKey;
  const next=frames.get(index+1);
  const nextKey=next?session+":"+(index+1)+":"+next.raw_sha256+":"+(comparison?.runId??"raw"):"";
  const promote=useCallback((frame:DisplayFrame)=>{
    setDecoded(old=>old.has(frame.key)?old:new Map([...old].slice(-2).concat([[frame.key,frame]])));
    if(frame.key===expectedKey.current)setShown(old=>old?.key===frame.key?old:frame);
  },[]);
  useEffect(()=>{
    if(!playing){timeline.current=null;return;}
    if(!current||!ready)return;
    let clock=timeline.current;
    if(!clock||clock.rate!==rate||clock.index!==index){
      clock={wall:performance.now(),source:current.source_time_s,rate,index};
      timeline.current=clock;
    }
    if(index+1>=total)return;
    if(!next||!decoded.has(nextKey))return;
    let deadline=clock.wall+(next.source_time_s-clock.source)*1000/rate;
    const late=performance.now()-deadline;
    // Rebase only after genuine buffering; never flash several late frames.
    if(late>10){clock.wall+=late;deadline+=late;}
    const timer=setTimeout(()=>{
      clock.index=index+1;
      onPositionChange({index:index+1,playing:index+2<total});
    },Math.max(0,deadline-performance.now()));
    return()=>clearTimeout(timer);
  },[playing,current,next,nextKey,index,total,ready,rate,onPositionChange,decoded]);
  const src=useMemo(()=>getApiBaseUrl()+videoFramePath(session,index)+"?sha256="+(current?.raw_sha256??""),[session,index,current?.raw_sha256]);
  const candidate=useMemo<DisplayFrame|null>(()=>current?{key:imageKey,index,src,width:4,height:3,raw_sha256:current.raw_sha256,comparison}:null,[current,imageKey,index,src,comparison]);
  const upcoming=useMemo<DisplayFrame|null>(()=>(playing||heldNext===nextKey||decoded.has(nextKey))&&next?{key:nextKey,index:index+1,src:getApiBaseUrl()+videoFramePath(session,index+1)+"?sha256="+next.raw_sha256,width:4,height:3,raw_sha256:next.raw_sha256,comparison}:null,[playing,heldNext,decoded,next,nextKey,index,session,comparison]);
  useEffect(()=>{if(shown)onDisplayed?.(shown.index);},[shown,onDisplayed]);
  // Keep the decoded element mounted while the next JPEG loads. Its key stays
  // in this same list, so promoting a pending image never fetches it twice.
  const images:DisplayFrame[]=[...new Map([shown,candidate,upcoming].filter((frame):frame is DisplayFrame=>!!frame).map(frame=>[frame.key,shown?.key===frame.key?shown:frame])).values()];
  const visible=shown?frames.get(shown.index):undefined;
  const move=(value:number)=>onPositionChange({index:Math.max(0,Math.min(total-1,value)),playing:false});
  return <section data-testid="source-video-player" aria-label={t("analysis.video.title")}>
    {error&&<p role="alert" data-testid="source-video-error">{t("analysis.video.failure")} · <code>{error}</code> <Button onClick={()=>{setFrames(new Map());setFailure(null);setAttempt(value=>value+1);}}>{t("refresh")}</Button></p>}
    <div data-testid="source-video-viewport" className={styles.videoPairStack} aria-busy={!ready}>
      {!images.length&&<div className={styles.videoPair} aria-hidden="true">{["samples.original","analysis.video.processed"].map(label=><div className={styles.videoPairCell} key={label}><p>{t(label)}</p><div className={styles.videoPairViewport} style={{aspectRatio:"4 / 3"}}/>{label==="analysis.video.processed"&&<p className={styles.small}>&nbsp;</p>}</div>)}</div>}
      {images.map(image=><VideoFramePair key={image.key} frame={image} session={session} active={image.key===shown?.key} requested={image.key===imageKey} onReady={promote} onFailure={()=>{setFailure({offset,code:"analysis_video_frame_unavailable"});onPositionChange({index,playing:false});}} t={t}/>)}
    </div>
    <div className={styles.samplePlaybackControls} data-testid="source-video-controls">
    <div className={styles.fieldRow}>
      <Button data-testid="source-video-play" disabled={!!error||!current} onClick={()=>{if(playing)setHeldNext(nextKey);onPositionChange({index:index+1>=total?0:index,playing:!playing});}}>{t(playing?"analysis.video.pause":"analysis.video.play")}</Button>
      <Button data-testid="source-video-previous" disabled={index===0} onClick={()=>move(index-1)}>{t("samples.previous")}</Button>
      <Button data-testid="source-video-next" disabled={index+1>=total} onClick={()=>move(index+1)}>{t("samples.next")}</Button>
      <span data-testid="source-video-position">{t("analysis.video.position",{frame:(shown?.index??index)+1,total,time:videoClock(visible?.source_time_s??NaN),duration:videoClock(meta?.duration_s??video.duration_s)})}</span>
    <Field label={t("analysis.video.speed")}><Select data-testid="source-video-speed" value={rate} onChange={event=>{const value=Number(event.target.value);if(value===1||value===2||value===5)onRateChange(value);}}>{[1,2,5].map(value=><option key={value} value={value}>×{value}</option>)}</Select></Field>
    <Field label={t("analysis.video.frame")}><Input aria-label={t("analysis.video.frame")} data-testid="analysis-frame" type="number" min={1} max={total} value={index+1} onChange={event=>{const value=Number(event.target.value)-1;if(Number.isSafeInteger(value)&&value>=0&&value<total)move(value);}}/></Field>
    </div>
    <Field label={t("analysis.video.seek")}><Input data-testid="source-video-seek" type="range" min={0} max={Math.max(0,total-1)} step={1} value={index} onChange={event=>move(Number(event.target.value))}/></Field>
    </div>
  </section>;
}
