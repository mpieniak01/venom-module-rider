import assert from "node:assert/strict";
import test from "node:test";
import {analysisFrameAssets,analysisState,nextStateFrame,readAnalysisChunk,readVideoPage,reportChunks,sameAnalysisSettings,stateHistogram,timelinePaths,videoClock,videoFramePath} from "../../web-next/lib/analysis-video";
const sid="1".repeat(32),rid="2".repeat(32),sha="a".repeat(64);
const row={source_index:2,source_time_s:.4,state:"obstacle",raw_sha256:sha,sample_id:"3".repeat(32),artifact_names:["0002-overlay.jpg"]};
const report={run_id:rid,input_kind:"video",context:{input_source:{kind:"session-video",session_id:sid},spec:{profile:"da2-default"}},row_chunks:["rows-00000.json"]};
test("frame distribution counts frames including unknown, never pixels or rejected twice",()=>{
 const rows=[{state:"clear",floor_fraction:.01},{state:"unknown",rejected_input:true},{state:"obstacle",area_pixels:9000},{state:"bad"}];
 assert.deepEqual(stateHistogram(rows),{clear:1,unknown:2,obstacle:1});
 assert.equal(analysisState(null),"unknown");
 assert.equal(nextStateFrame(rows,"unknown",1),3);
 assert.equal(nextStateFrame(rows,"clear",3),0);
 assert.equal(nextStateFrame([],"clear",0),null);
});
test("timeline preserves chronological order in three compact paths",()=>{
 const paths=timelinePaths([{state:"clear"},{state:"clear"},{state:"obstacle"},{state:"unknown"},{state:"obstacle"}]);
 assert.equal(paths.clear,"M0 0h2v1H0Z");assert.equal(paths.unknown,"M3 0h1v1H3Z");
 assert.equal(paths.obstacle,"M2 0h1v1H2ZM4 0h1v1H4Z");
});
test("video frame source is explicitly hash locked and overlay index matches its source",()=>{
 const assets=analysisFrameAssets(report,rid,sid,row,2);
 assert.equal(assets[0].path,videoFramePath(sid,2)+"?sha256="+sha);
 assert.equal(assets[1].path,"/api/v1/rider/analysis/runs/"+rid+"/artifacts/0002-overlay.jpg");
 assert.throws(()=>analysisFrameAssets(report,rid,sid,{...row,artifact_names:["0003-overlay.jpg"]},2),/identity/);
 assert.throws(()=>analysisFrameAssets(report,"4".repeat(32),sid,row,2),/identity/);
 assert.throws(()=>analysisFrameAssets(report,rid,"5".repeat(32),row,2),/identity/);
});
test("sample sources remain legacy originals and rejected video input can have no overlay",()=>{
 const legacy={run_id:rid};
 assert.equal(analysisFrameAssets(legacy,rid,sid,{...row,artifact_names:["0000-depth.png","0000-overlay.jpg"]},0)[0].path,"/api/v1/rider/samples/items/"+row.sample_id+"/original");
 assert.equal(analysisFrameAssets(report,rid,sid,{...row,artifact_names:[]},2).length,1);
 assert.throws(()=>analysisFrameAssets(report,rid,sid,{...row,artifact_names:["0002-overlay.jpg","0003-overlay.jpg"]},2),/identity/);
});
test("immutable chunk sequence rejects missing, duplicate or foreign report sources",()=>{
 assert.deepEqual(reportChunks(report,rid),["rows-00000.json"]);
 assert.throws(()=>reportChunks({...report,row_chunks:["rows-00001.json"]},rid));
 assert.throws(()=>reportChunks({...report,run_id:"f".repeat(32)},rid));
 assert.equal(readAnalysisChunk([row],2)[0].source_index,2);
 assert.throws(()=>readAnalysisChunk([{...row,source_index:3}],2));
 assert.throws(()=>readAnalysisChunk([{...row,raw_sha256:"bad"}],2));
 assert.throws(()=>readAnalysisChunk(Array(129).fill(row),2));
});
test("video metadata validates identity, order, timing and recorded hashes",()=>{
 const frames=[0,1,2].map(index=>({index,source_time_s:index*.2,frame_id:"frame"+index,sequence:index*4,raw_sha256:sha}));
 const page={session_id:sid,total:3,duration_s:.4,frames,next_offset:null};
 assert.equal(readVideoPage(page,sid,0).frames.length,3);
 assert.throws(()=>readVideoPage({...page,session_id:rid},sid,0));
 assert.throws(()=>readVideoPage({...page,frames:[frames[0],frames[0]]},sid,0));
 assert.throws(()=>readVideoPage({...page,frames:[{...frames[0],source_time_s:NaN}]},sid,0));
 assert.throws(()=>videoFramePath("../../",0));
 assert.throws(()=>videoFramePath(sid,-1));
 assert.equal(videoClock(65.29),"1:05.2");
});
test("expanded backend defaults match an unchanged draft",()=>{
 const frozen={profile:"da2-default",window:1,quality_mode:"production",input_source:"samples",corridor:[.35,.8,.65,.95],
  behavior:{version:1,profile_id:"legacy",profile_revision:1},
  perception:{engine:"depth_plane",model_id:"",artifact_sha256:"",residual_limit:.06,outlier_limit:.005,slope_min:2,semantic_models:{}},
  geometry:{mode:"image",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45}};
 const saved={context:{spec:frozen}};
 const draft={profile:"da2-default",corridor:[.35,.8,.65,.95],perception:{engine:"depth_plane"},geometry:{mode:"image"},behavior:{}};
 assert.equal(sameAnalysisSettings(saved,draft),true);
 assert.equal(sameAnalysisSettings(saved,{...draft,perception:{engine:"depth_plane",residual_limit:.07}}),false);
 assert.equal(sameAnalysisSettings(saved,{...draft,behavior:{profile_id:"room_fluid",profile_revision:2}}),false);
 assert.equal(sameAnalysisSettings(saved,{...draft,input_source:"video"}),false);
 assert.equal(frozen.behavior.profile_id,"legacy");
});

test("compact video layers are bound to source SHA, index, supported version and dimensions",()=>{
 const layer={version:1,artifact:"0002-mask.png",width:64,height:48,palette_version:1,source_sha256:sha};
 const compact={...row,artifact_names:["0002-mask.png"],classification_layer:layer};
 const assets=analysisFrameAssets(report,rid,sid,compact,2);
 assert.deepEqual(assets[1],{kind:"mask",path:"/api/v1/rider/analysis/runs/"+rid+"/artifacts/0002-mask.png",width:64,height:48});
 for(const change of [{source_sha256:"b".repeat(64)},{version:2},{palette_version:2},{artifact:"0003-mask.png"},{width:0},{height:721}]){
  assert.throws(()=>analysisFrameAssets(report,rid,sid,{...compact,classification_layer:{...layer,...change}},2),/identity/);
 }
 assert.throws(()=>analysisFrameAssets(report,rid,sid,{...compact,classification_layer:null},2),/identity/);
 assert.throws(()=>analysisFrameAssets(report,rid,sid,{...compact,artifact_names:["0002-mask.png","0002-overlay.jpg"]},2),/identity/);
});
