import {GeometryOverlay} from "./geometry-overlay";
/* eslint-disable @next/next/no-img-element -- Frozen layers compose only with their hash-locked source. */
import {useState,type SyntheticEvent} from "react";
import {getApiBaseUrl} from "@/lib/env";
import {analysisFrameAssets,type FrameAsset} from "../lib/analysis-video";
import type {JsonRecord} from "../lib/model";
import type {Translator} from "../lib/i18n";
import styles from "../rider.module.css";

/** Compose archived artifacts and geometry only with their hash-locked RAW source. */
export function AnalysisFrameMedia({report,runId,session,row,index,t}:{report:JsonRecord;runId:string;session:string;row:JsonRecord;index:number;t:Translator}){
  let assets:FrameAsset[]=[];
  try{assets=analysisFrameAssets(report,runId,session,row,index);}catch{}
  const [imageSize,setImageSize]=useState<[number,number]>([0,0]);
  const [loaded,setLoaded]=useState<string[]>([]),[failed,setFailed]=useState(false);
  const mask=assets.find(asset=>asset.kind==="mask"),original=assets[0];
  const processed=assets.find(asset=>asset.kind==="overlay")??assets.find(asset=>asset.kind==="depth");
  const keys=mask?["original","mask-source","mask"]:["original",...(processed?[processed.kind]:[])];
  const ready=!!assets.length&&keys.every(key=>loaded.includes(key));
  const load=(key:string,matchSize=false)=>(event:SyntheticEvent<HTMLImageElement>)=>{
    if(key==="original")setImageSize([event.currentTarget.naturalWidth,event.currentTarget.naturalHeight]);
    if(mask&&matchSize&&(event.currentTarget.naturalWidth!==mask.width||event.currentTarget.naturalHeight!==mask.height)){setFailed(true);return;}
    setLoaded(previous=>previous.includes(key)?previous:[...previous,key]);
  };
  const imageStyle={visibility:ready&&!failed?"visible"as const:"hidden"as const};
  if(!original)return <p role="alert" data-testid="analysis-frame-mismatch">{t("analysis.frameMismatch")}</p>;
  return <div data-testid="analysis-frame-media" data-run-id={runId} data-source-index={row.source_index??index}>
    {failed&&<p role="alert">{t("analysis.artifactFailed")}</p>}
    <div className={styles.analysisMedia}>
      <div><p>{t("samples.original")}</p>
        <img data-testid="analysis-frame-original" className={styles.analysisImage} style={imageStyle} src={getApiBaseUrl()+original.path} alt={t("samples.original")} onLoad={load("original",!!mask)} onError={()=>setFailed(true)}/>
      </div>
      {mask?<div><p>{t("analysis.overlay")}</p>
        <div data-testid="analysis-frame-composite" style={{position:"relative",aspectRatio:mask.width+"/"+mask.height,...imageStyle}}>
          <img data-testid="analysis-frame-overlay-source" className={styles.analysisImage} src={getApiBaseUrl()+original.path} alt={t("samples.original")} onLoad={load("mask-source",true)} onError={()=>setFailed(true)}/>
          <img data-testid="analysis-frame-overlay" src={getApiBaseUrl()+mask.path} alt={t("analysis.overlay")} style={{position:"absolute",inset:0,width:"100%",height:"100%",objectFit:"contain"}} onLoad={load("mask",true)} onError={()=>setFailed(true)}/>
          {ready&&!failed&&<GeometryOverlay value={row.overlay_geometry} size={[Number(mask.width),Number(mask.height)]}/>}
        </div>
      </div>:(processed?[processed]:[]).map(asset=><div key={asset.kind}>
        <p>{t(asset.kind==="depth"?"analysis.depth":"analysis.overlay")}</p>
        <div style={{position:"relative"}}><img data-testid={"analysis-frame-"+asset.kind} className={styles.analysisImage} style={imageStyle} src={getApiBaseUrl()+asset.path} alt={t(asset.kind==="depth"?"analysis.depth":"analysis.overlay")} onLoad={load(asset.kind)} onError={()=>setFailed(true)}/>{ready&&!failed&&asset.kind==="overlay"&&<GeometryOverlay value={row.overlay_geometry} size={imageSize}/>}</div>
      </div>)}
    </div>
    {!mask&&!assets.some(asset=>asset.kind==="overlay")&&<p className={styles.hint}>{t("analysis.noOverlay")}</p>}
  </div>;
}
