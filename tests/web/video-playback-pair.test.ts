import assert from "node:assert/strict";
import test from "node:test";
import {videoPlaybackAsset} from "../../web-next/lib/analysis-video";
const session="1".repeat(32),run="2".repeat(32),sha="a".repeat(64);
const report={run_id:run,input_kind:"video",context:{input_source:{kind:"session-video",session_id:session}}};
const source={index:7,raw_sha256:sha};
const row={source_index:7,raw_sha256:sha,artifact_names:["0007-mask.png"],classification_layer:{version:1,palette_version:1,source_sha256:sha,artifact:"0007-mask.png",width:320,height:240}};
test("playback uses only the classification of the exact displayed RAW",()=>{
 const asset=videoPlaybackAsset(report,run,session,row,source);
 assert.equal(asset?.kind,"mask");assert.equal(asset?.width,320);assert.ok(asset?.path.endsWith("/0007-mask.png"));
});
test("same index with another RAW or another source index cannot be composed",()=>{
 assert.throws(()=>videoPlaybackAsset(report,run,session,row,{...source,raw_sha256:"b".repeat(64)}));
 assert.throws(()=>videoPlaybackAsset(report,run,session,row,{...source,index:8}));
 assert.throws(()=>videoPlaybackAsset(report,run,session,undefined,source));
});
test("switching studies or sessions cannot borrow a previous classification",()=>{
 assert.throws(()=>videoPlaybackAsset(report,"3".repeat(32),session,row,source));
 assert.throws(()=>videoPlaybackAsset(report,run,"4".repeat(32),row,source));
});
test("a rejected source without a layer remains RAW with no fabricated result",()=>{
 assert.equal(videoPlaybackAsset(report,run,session,{source_index:7,raw_sha256:sha,artifact_names:[],classification_layer:null},source),null);
});
