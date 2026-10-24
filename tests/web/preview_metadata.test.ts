import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { readAnalysis, PREVIEW_ANALYSIS_MAX_CHARS } from "../../web-next/lib/vision-frame";
import { nativeAnalysisMatches } from "../../web-next/lib/camera-pipeline";
import { previewNotice } from "../../web-next/lib/loop-controls";
import { perceptionResultMatches } from "../../web-next/lib/perception";
import { displayedPreview, previewLifetime } from "../../web-next/lib/preview-media";
import { sectorPresentation, type SectorFrame } from "../../web-next/lib/preview-sectors";

const fixture = JSON.parse(readFileSync(new URL("../fixtures/preview-metadata-v1.json", import.meta.url), "utf8"));

test("the same producer fixture fits the browser bound and exact-source contract", () => {
  const original = JSON.stringify(fixture.result);
  assert.ok(original.length > PREVIEW_ANALYSIS_MAX_CHARS);
  assert.equal(readAnalysis(original), undefined); // Reproduces the production regression.
  const header = JSON.stringify(fixture.preview), analysis = readAnalysis(header);
  assert.ok(header.length < PREVIEW_ANALYSIS_MAX_CHARS);
  assert.deepEqual(analysis, fixture.preview);
  assert.equal(nativeAnalysisMatches(analysis, fixture.preview.source), true);
  for (const model_id of ["segformer-b0-ade20k", "depth-anything-v2-indoor-small"]) {
    const selected = { ...analysis, model_id, artifact_sha256: "d".repeat(64) };
    assert.equal(perceptionResultMatches(selected, {model_id, artifact_sha256:"d".repeat(64), revision:fixture.preview.config_revision}), true);
  }
});

test("fresh image replacement and a short source gap do not flash unknown cards or a stale notice", () => {
  const make = (i:number, receivedAt:number, recovery=false) => {
    const source = { ...fixture.preview.source, sequence:i, frame_id:"capture-"+i };
    const header = JSON.stringify({ ...fixture.preview, source, sequence:i,
      ...(recovery ? {state:"unknown", reason:"navigation_frame_gap", vision_action:"stop"} : {}) });
    const analysis = readAnalysis(header);
    assert.equal(nativeAnalysisMatches(analysis, source), true);
    return {id:source.frame_id,url:"blob:"+i,analysis,receivedAt,ageMs:160,stale:false,knownFreshness:true,decoded:true};
  };
  let frame = make(0, 1000), sample = sectorPresentation(null,frame,false,1000);
  const seen = new Set<string>();
  for(let i=1;i<=100;i++){
    const now = 1000+i*100;
    // A failed inference yields no new preview part. Keep aging the original image.
    if(i!==40 && i!==41 && i!==42) frame=make(i,now,i===43);
    const visible=displayedPreview(frame,frame,now)!;
    assert.equal(previewNotice(visible.stale,"venom",true),null);
    sample=sectorPresentation(sample,visible,i%10===0,now);
    const states=sample.view.rows.map(row=>row.state).join(",");
    seen.add(states);
    assert.equal(states,"obstacle,clear,clear");
  }
  assert.equal(seen.size,1);
  assert.equal(previewLifetime(frame,frame.receivedAt+1000),0);
  const expired={...frame,stale:true,knownFreshness:false};
  assert.equal(previewNotice(expired.stale,"venom",true),"previewDelayedNotice");
  assert.ok(sectorPresentation(sample,expired,false,frame.receivedAt+1000).view.rows.every(row=>row.state==="unknown"));
});

test("changed source and missing coverage cannot borrow an earlier clear corridor", () => {
  const base = fixture.preview;
  const frame:SectorFrame = {id:base.source.frame_id,analysis:base,receivedAt:1000,ageMs:20,stale:false,knownFreshness:true,decoded:true};
  const sample=sectorPresentation(null,frame,false,1000);
  const changed={...frame,id:"other-source"};
  assert.ok(sectorPresentation(sample,changed,false,1100).view.rows.every(row=>row.state==="unknown"));
  assert.equal(nativeAnalysisMatches(base,{...base.source,sha256:"f".repeat(64)}),false);
});
