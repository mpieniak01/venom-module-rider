import {readGeometry} from "./overlay-geometry";
import { record, str, type JsonRecord } from "./model";
import { SAMPLES_BASE } from "./samples";

export const ANALYSIS_BASE="/api/v1/rider/analysis";
export const VIDEO_PAGE_SIZE=500;
export type VideoFrame={index:number;source_time_s:number;frame_id:string|null;sequence:number|null;raw_sha256:string};
export type VideoPage={session_id:string;total:number;duration_s:number;frames:VideoFrame[];next_offset:number|null};
export type AnalysisState="clear"|"unknown"|"obstacle";
export type AnalysisRow=JsonRecord&{state?:string};
const ticket=/^[a-f0-9]{32}$/;
const hash=/^[a-f0-9]{64}$/;
const integer=(value:unknown,low=0):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=low;
const finite=(value:unknown):value is number=>typeof value==="number"&&Number.isFinite(value);
export function videoFramePath(session:string,index:number):string {
  if(!ticket.test(session)||!integer(index))throw new Error("analysis_video_identity");
  return SAMPLES_BASE+"/sessions/"+session+"/video/frames/"+index;
}
/** The recording index is immutable and contiguous; camera sequences may skip. */
export function readVideoPage(value:unknown,session:string,offset:number):VideoPage {
  const row=record(value);
  if(row.session_id!==session||!integer(row.total,1)||row.total>100000||!finite(row.duration_s)||row.duration_s<0
    ||!Array.isArray(row.frames)||row.frames.length<1||row.frames.length>VIDEO_PAGE_SIZE
    ||!row.frames.every((value,index)=>{
      const frame=record(value);
      return frame.index===offset+index&&integer(frame.index)&&frame.index<Number(row.total)
        &&finite(frame.source_time_s)&&frame.source_time_s>=0&&frame.source_time_s<=Number(row.duration_s)+.01
        &&(frame.frame_id===null||typeof frame.frame_id==="string")
        &&(frame.sequence===null||integer(frame.sequence))&&hash.test(str(frame.raw_sha256));
    })||(row.next_offset!==null&&(!integer(row.next_offset)||row.next_offset!==offset+row.frames.length))
    ||offset+row.frames.length>Number(row.total))throw new Error("analysis_video_metadata_invalid");
  const frames=row.frames as VideoFrame[];
  if(frames.some((frame,index)=>index>0&&frame.source_time_s<=frames[index-1].source_time_s))
    throw new Error("analysis_video_metadata_invalid");
  return row as VideoPage;
}
export function videoClock(value:number):string {
  if(!Number.isFinite(value)||value<0)return "—";
  const tenths=Math.floor(value*10),seconds=Math.floor(tenths/10);
  return Math.floor(seconds/60)+":"+String(seconds%60).padStart(2,"0")+"."+tenths%10;
}
export function analysisState(row:unknown):AnalysisState {
  const value=record(row).state;
  return value==="clear"||value==="obstacle"?value:"unknown";
}
export function stateHistogram(rows:AnalysisRow[]):Record<AnalysisState,number> {
  const counts={clear:0,unknown:0,obstacle:0};
  for(const row of rows)counts[analysisState(row)]++;
  return counts;
}
/** Return a new result position, wrapping once; unknown inputs never mean clear. */
export function nextStateFrame(rows:AnalysisRow[],state:AnalysisState,current:number):number|null {
  for(let step=1;step<=rows.length;step++){
    const index=(Math.max(-1,current)+step)%rows.length;
    if(analysisState(rows[index])===state)return index;
  }
  return null;
}
/** Three paths, not one DOM element per frame, preserve complete chronological order. */
export function timelinePaths(rows:AnalysisRow[]):Record<AnalysisState,string> {
  const paths={clear:"",unknown:"",obstacle:""};
  let start=0;
  while(start<rows.length){
    const state=analysisState(rows[start]);let end=start+1;
    while(end<rows.length&&analysisState(rows[end])===state)end++;
    paths[state]+="M"+start+" 0h"+(end-start)+"v1H"+start+"Z";
    start=end;
  }
  return paths;
}
export function videoRun(report:unknown):boolean{return record(report).input_kind==="video";}
export function reportChunks(report:unknown,runId:string):string[] {
  const row=record(report),names=row.row_chunks;
  if(row.run_id!==runId||!Array.isArray(names)||!names.length||names.length>1000
    ||!names.every((name,index)=>name==="rows-"+String(index).padStart(5,"0")+".json"))
    throw new Error("analysis_video_report_invalid");
  return names as string[];
}
export function readAnalysisChunk(value:unknown,firstIndex:number):AnalysisRow[] {
  if(!Array.isArray(value)||!value.length||value.length>128||!value.every((entry,index)=>{
    const row=record(entry);
    return row.source_index===firstIndex+index&&hash.test(str(row.raw_sha256))
      &&finite(row.source_time_s)&&row.source_time_s>=0
      &&["clear","unknown","obstacle"].includes(str(row.state))&&Array.isArray(row.artifact_names);
  }))throw new Error("analysis_video_report_invalid");
  return value as AnalysisRow[];
}
function normal(value:unknown):unknown {
  if(value===undefined||value===null)return undefined;
  if(Array.isArray(value))return value.map(normal);
  if(typeof value!=="object")return value;
  const entries=Object.entries(value as JsonRecord).sort(([a],[b])=>a.localeCompare(b))
    .map(([key,item])=>[key,normal(item)] as const).filter(([,item])=>item!==undefined&&!(typeof item==="object"&&!Array.isArray(item)&&Object.keys(item as object).length===0));
  return Object.fromEntries(entries);
}
/** Frozen run metadata always describes the run; the editor is only a future intent. */
function comparable(spec:JsonRecord):unknown {
  const perception=record(spec.perception);
  const semantic=["segformer-b0-ade20k","yolo26n-sem-ade20k","fast-scnn-floor"].includes(str(spec.profile));
  return normal({
    profile:spec.profile??"da2-default",window:spec.window??1,quality_mode:spec.quality_mode??"production",
    input_source:spec.input_source??"samples",corridor:spec.corridor??[.35,.8,.65,.95],
    behavior:{version:1,profile_id:"legacy",profile_revision:1,...record(spec.behavior)},
    perception:{engine:semantic?"segformer":"depth_plane",model_id:"",artifact_sha256:"",residual_limit:.06,outlier_limit:.005,slope_min:2,semantic_models:{},...perception,input_region:semantic?undefined:perception.input_region??{version:1,bounds:[0,0,1,1]}},
    geometry:{mode:"image",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45,...record(spec.geometry)},
  });
}
export function sameAnalysisSettings(report:unknown,current:JsonRecord):boolean {
  const frozen=record(record(record(report).context).spec);
  return JSON.stringify(comparable(frozen))===JSON.stringify(comparable(current));
}
export type FrameAsset={kind:"original"|"depth"|"overlay"|"mask";path:string;width?:number;height?:number};
export function analysisFrameAssets(report:unknown,runId:string,session:string,rowValue:unknown,position:number):FrameAsset[] {
  const reportRow=record(report),row=record(rowValue);
  if(reportRow.run_id!==runId||!ticket.test(runId)||!hash.test(str(row.raw_sha256)))throw new Error("analysis_frame_identity");
  let original:string;
  if(videoRun(report)){
    const source=record(record(reportRow.context).input_source);
    if(source.kind!=="session-video"||source.session_id!==session||!integer(row.source_index))
      throw new Error("analysis_frame_identity");
    original=videoFramePath(session,row.source_index)+"?sha256="+row.raw_sha256;
  }else{
    if(!ticket.test(str(row.sample_id)))throw new Error("analysis_frame_identity");
    original=SAMPLES_BASE+"/items/"+row.sample_id+"/original";
  }
  const expected=videoRun(report)?Number(row.source_index):position;
  if(!integer(expected))throw new Error("analysis_frame_identity");
  const result:FrameAsset[]=[{kind:"original",path:original}];
  const names=Array.isArray(row.artifact_names)?row.artifact_names:[];
  if(row.classification_layer!==undefined&&row.classification_layer!==null){
    const layer=record(row.classification_layer),name=String(expected).padStart(4,"0")+"-mask.png";
    if(![1,2].includes(Number(layer.version))||layer.version===2&&!readGeometry(row.overlay_geometry,[Number(layer.width),Number(layer.height)])||layer.palette_version!==1||layer.source_sha256!==row.raw_sha256||layer.artifact!==name
      ||!integer(layer.width,1)||layer.width>1280||!integer(layer.height,1)||layer.height>720
      ||names.filter(value=>value===name).length!==1||names.some(value=>typeof value==="string"&&value.endsWith("-overlay.jpg")))
      throw new Error("analysis_frame_identity");
    result.push({kind:"mask",path:ANALYSIS_BASE+"/runs/"+runId+"/artifacts/"+name,width:layer.width,height:layer.height});
  }else if(names.some(name=>typeof name==="string"&&name.endsWith("-mask.png")))throw new Error("analysis_frame_identity");
  for(const [kind,suffix]of [["depth","depth.png"],["overlay","overlay.jpg"]]as const){
    const files=names.filter(name=>typeof name==="string"&&new RegExp("^[0-9]{4,6}-"+suffix.replace(".","\\.")+"$").test(name));
    if(files.length>1||(files.length===1&&files[0]!==String(expected).padStart(4,"0")+"-"+suffix))throw new Error("analysis_frame_identity");
    if(files.length)result.push({kind,path:ANALYSIS_BASE+"/runs/"+runId+"/artifacts/"+files[0]});
  }
  return result;
}


/** A saved classification can only be composed with this exact displayed RAW. */
export function videoPlaybackAsset(report:unknown,runId:string,session:string,rowValue:unknown,source:{index:number;raw_sha256:string}):FrameAsset|null {
  const row=record(rowValue);
  if(!videoRun(report)||row.source_index!==source.index||row.raw_sha256!==source.raw_sha256)throw new Error("analysis_frame_identity");
  const assets=analysisFrameAssets(report,runId,session,row,source.index);
  return assets.find(asset=>asset.kind==="mask")??assets.find(asset=>asset.kind==="overlay")??assets.find(asset=>asset.kind==="depth")??null;
}
