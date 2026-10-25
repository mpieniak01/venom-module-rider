/* eslint-disable @next/next/no-img-element -- Immutable stored RAW, reused directly to compose the matching result. */
import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import {getApiBaseUrl} from "@/lib/env";
import {videoPlaybackAsset,type AnalysisRow} from "../lib/analysis-video";
import {record,type JsonRecord} from "../lib/model";
import type {Translator} from "../lib/i18n";
import styles from "../rider.module.css";

export type VideoComparison={report:JsonRecord;runId:string;rows:AnalysisRow[]};
export type DisplayFrame={key:string;index:number;src:string;raw_sha256:string;width:number;height:number;comparison:VideoComparison|null};
export function VideoFramePair({frame,session,active,onReady,onFailure,t}:{frame:DisplayFrame;session:string;active:boolean;onReady:(frame:DisplayFrame)=>void;onFailure:()=>void;t:Translator}){
  const raw=useRef<HTMLImageElement>(null),canvas=useRef<HTMLCanvasElement>(null),layer=useRef<HTMLImageElement|null>(null);
  const rawLoaded=useRef(false),layerState=useRef("loading");
  const [failed,setFailed]=useState(false),[painted,setPainted]=useState(false);
  const comparison=frame.comparison,row=record(comparison?.rows[frame.index]);
  const result=useMemo(()=>{
    if(!comparison)return {asset:null,error:""};
    try{return {asset:videoPlaybackAsset(comparison.report,comparison.runId,session,comparison.rows[frame.index],frame),error:""};}
    catch{return {asset:null,error:"analysis.frameMismatch"};}
  },[comparison,session,frame]);
  const complete=useCallback(()=>{
    const source=raw.current;
    if(!rawLoaded.current||!source)return;
    if(result.asset&&layerState.current==="loading")return;
    const size={width:source.naturalWidth,height:source.naturalHeight};
    if(result.asset&&layerState.current==="ready"&&layer.current&&canvas.current){
      const image=layer.current,asset=result.asset;
      if(image.naturalWidth!==size.width||image.naturalHeight!==size.height||(asset.kind==="mask"&&(asset.width!==size.width||asset.height!==size.height))){setFailed(true);}
      else {
        const target=canvas.current;target.width=size.width;target.height=size.height;
        const context=target.getContext("2d");
        if(!context)setFailed(true);
        else {if(asset.kind==="mask")context.drawImage(source,0,0);context.drawImage(image,0,0);setPainted(true);}
      }
    }
    // Loaded image events promote both sides together. No second RAW request
    // or previous frame's classification is used to compose the result.
    onReady({...frame,...size});
  },[frame,result.asset,onReady]);
  useEffect(()=>{
    if(!result.asset)return;
    let alive=true;const image=new Image();layer.current=image;
    image.onload=()=>{if(alive){layerState.current="ready";complete();}};
    image.onerror=()=>{if(alive){layerState.current="failed";setFailed(true);complete();}};
    image.src=getApiBaseUrl()+result.asset.path;
    return()=>{alive=false;image.onload=null;image.onerror=null;layer.current=null;};
  },[result.asset,complete]);
  const error=result.error||(failed?"analysis.artifactFailed":"");
  const placeholder=error||(!comparison?"analysis.video.noResult":!result.asset?"analysis.noOverlay":"");
  return <div className={styles.videoPair} style={{visibility:active?"visible":"hidden"}} aria-hidden={!active} data-testid={active?"analysis-frame-media":undefined} data-run-id={comparison?.runId} data-source-index={frame.index}>
    <div className={styles.videoPairCell}>
      <p>{t("samples.original")}</p>
      <div className={styles.videoPairViewport} style={{aspectRatio:frame.width+" / "+frame.height}}>
        <img ref={raw} data-testid={active?"source-video-image":"source-video-pending-image"} data-source-index={frame.index} className={styles.sourceVideoFrame} src={frame.src} alt={t("analysis.video.sourceFrame",{frame:frame.index+1})} onLoad={event=>{const image=event.currentTarget;if(image.naturalWidth&&image.naturalHeight){rawLoaded.current=true;complete();}}} onError={onFailure}/>
      </div>
    </div>
    <div className={styles.videoPairCell}>
      <p>{t("analysis.video.processed")}</p>
      <div className={styles.videoPairViewport} style={{aspectRatio:frame.width+" / "+frame.height}} data-testid={active?"analysis-frame-composite":undefined}>
        <canvas ref={canvas} data-testid={active&&painted&&!error?"analysis-frame-overlay":undefined} data-source-index={frame.index} className={styles.sourceVideoFrame} style={{visibility:painted&&!error?"inherit":"hidden"}} role="img" aria-label={t("analysis.video.processed")}/>
        {(!painted||error)&&!!placeholder&&<p className={styles.videoPairPlaceholder} role={error?"alert":"status"} data-testid={active?"video-result-placeholder":undefined}>{t(placeholder)}</p>}
      </div>
      <p className={styles.small} data-testid={active?"video-frame-state":undefined}>{comparison?t("samples.value."+String(row.state??"unknown")):"\u00a0"}</p>
    </div>
  </div>;
}
