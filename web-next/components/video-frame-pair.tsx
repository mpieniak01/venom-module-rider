import {GeometryOverlay} from "./geometry-overlay";
/* eslint-disable @next/next/no-img-element -- Immutable stored RAW, reused directly to compose the matching result. */
import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import {getApiBaseUrl} from "@/lib/env";
import {videoPlaybackAsset,type FrameAsset,type AnalysisRow} from "../lib/analysis-video";
import { str,record,type JsonRecord} from "../lib/model";
import type {Translator} from "../lib/i18n";
import styles from "../rider.module.css";

/** Compare decoded RAW, layer and declared mask dimensions before painting. */
function layerMatchesSize(width:number,height:number,rawWidth:number,rawHeight:number,kind:FrameAsset["kind"],maskWidth:number|undefined,maskHeight:number|undefined):boolean {
  return width===rawWidth&&height===rawHeight&&(kind!=="mask"||maskWidth===rawWidth&&maskHeight===rawHeight);
}
/** Resolve the placeholder without inferring that an absent comparison produced a result. */
function videoPlaceholder(error:string,hasComparison:boolean,hasAsset:boolean):string {
  if(error)return error;
  if(!hasComparison)return "analysis.video.noResult";
  return hasAsset?"":"analysis.noOverlay";
}
/** Keep decoded-overlay visibility and its test identity derived from the same flags. */
function videoComposition(active:boolean,painted:boolean,failed:boolean){
  const visible=painted&&!failed;
  return {visible,testId:active&&visible?"analysis-frame-overlay":undefined,visibility:visible?"inherit" as const:"hidden" as const};
}
export type VideoComparison={report:JsonRecord;runId:string;rows:AnalysisRow[]};
export type DisplayFrame={key:string;index:number;src:string;raw_sha256:string;width:number;height:number;comparison:VideoComparison|null};
/** Promote decoded source and matching classification together before drawing geometry. */
export function VideoFramePair({frame,session,active,requested,onReady,onFailure,t}:{frame:DisplayFrame;session:string;active:boolean;requested:boolean;onReady:(frame:DisplayFrame)=>void;onFailure:()=>void;t:Translator}){

  const raw=useRef<HTMLImageElement>(null),canvas=useRef<HTMLCanvasElement>(null),layer=useRef<HTMLImageElement|null>(null);
  const rawLoaded=useRef(false),layerState=useRef("loading");
  const [failed,setFailed]=useState(false),[painted,setPainted]=useState(false);
  const comparison=frame.comparison,row=record(comparison?.rows[frame.index]);
  const result=useMemo(()=>{
    if(!comparison)return {asset:null,error:""};
    try{return {asset:videoPlaybackAsset(comparison.report,comparison.runId,session,comparison.rows[frame.index],{index:frame.index,raw_sha256:frame.raw_sha256}),error:""};}
    catch{return {asset:null,error:"analysis.frameMismatch"};}
  },[comparison,session,frame.index,frame.raw_sha256]);
  /** Paint only a classification matching the just-decoded original dimensions. */
  const paintResult=useCallback((source:HTMLImageElement,size:{width:number;height:number})=>{
    if(result.asset&&layerState.current==="ready"&&layer.current&&canvas.current){
      const image=layer.current,asset=result.asset;
      if(!layerMatchesSize(image.naturalWidth,image.naturalHeight,size.width,size.height,asset.kind,asset.width,asset.height)){setFailed(true);}
      else {
        const target=canvas.current;target.width=size.width;target.height=size.height;
        const context=target.getContext("2d");
        if(!context)setFailed(true);
        else {if(asset.kind==="mask")context.drawImage(source,0,0);context.drawImage(image,0,0);setPainted(true);}
      }
    }
  },[result.asset]);
  /** Promote the captured RAW and optional painted result together. */
  const complete=useCallback(()=>{
    const source=raw.current;
    if(!rawLoaded.current||!source)return;
    if(result.asset&&layerState.current==="loading")return;
    const size={width:source.naturalWidth,height:source.naturalHeight};
    paintResult(source,size);
    // Loaded image events promote both sides together. No second RAW request
    // or previous frame's classification is used to compose the result.
    onReady({key:frame.key,index:frame.index,src:frame.src,raw_sha256:frame.raw_sha256,comparison,...size});
  },[frame.key,frame.index,frame.src,frame.raw_sha256,comparison,result.asset,onReady,paintResult]);
  useEffect(()=>{
    if(!result.asset)return;
    let alive=true;const image=new Image();layer.current=image;
    image.onload=()=>{if(alive){layerState.current="ready";complete();}};
    image.onerror=()=>{if(alive){layerState.current="failed";setFailed(true);complete();}};
    image.src=getApiBaseUrl()+result.asset.path;
    return()=>{alive=false;image.onload=null;image.onerror=null;layer.current=null;};
  },[result.asset,complete]);
  useEffect(()=>{
    const source=raw.current;
    if(requested&&rawLoaded.current&&source&&(!result.asset||layerState.current!=="loading")){
      onReady({...frame,width:source.naturalWidth,height:source.naturalHeight});
    }
  },[requested,frame,result.asset,onReady]);
  const error=result.error||(failed?"analysis.artifactFailed":"");
  const placeholder=videoPlaceholder(error,!!comparison,!!result.asset);
  const composition=videoComposition(active,painted,!!error);
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
        <canvas ref={canvas} data-testid={composition.testId} data-source-index={frame.index} className={styles.sourceVideoFrame} style={{visibility:composition.visibility}} role="img" aria-label={t("analysis.video.processed")}/>
        {composition.visible&&record(row.classification_layer).version===2&&<GeometryOverlay value={row.overlay_geometry} size={[frame.width,frame.height]}/>}
        {(!painted||error)&&!!placeholder&&<p className={styles.videoPairPlaceholder} role={error?"alert":"status"} data-testid={active?"video-result-placeholder":undefined}>{t(placeholder)}</p>}
      </div>
      <p className={styles.small} data-testid={active?"video-frame-state":undefined}>{comparison?t("samples.value."+str(row.state??"unknown")):"\u00a0"}</p>
    </div>
  </div>;
}
