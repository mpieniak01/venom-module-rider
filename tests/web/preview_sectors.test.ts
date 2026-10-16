import assert from "node:assert/strict";
import {test} from "node:test";
import {previewSectors, type SectorFrame} from "../../web-next/lib/preview-sectors";

function frame():SectorFrame {
  return {id:"capture-7",decoded:true,stale:false,knownFreshness:true,ageMs:20,receivedAt:1000,
    analysis:{run_id:"run-one",frame_id:"capture-7",sequence:7,config_revision:39,state:"clear",sector_space:"image",
      sectors:{left:{state:"obstacle",coverage_complete:true,corridor:[.05,.8,.35,.95]},
        center:{state:"clear",coverage_complete:true,corridor:[.35,.8,.65,.95]},
        right:{state:"clear",coverage_complete:true,corridor:[.65,.8,.95,.95]}}}};
}
test("cards describe the same decoded image, not a separately polled loop decision",()=>{
  const image=frame();
  const view=previewSectors(image,1040);
  assert.equal(view.current,true);assert.equal(view.space,"image");
  assert.deepEqual(view.rows.map(r=>[r.code,r.state]),[["L","obstacle"],["C","clear"],["R","clear"]]);
  image.analysis!.sequence=8;image.analysis!.config_revision=40;
  assert.deepEqual(previewSectors(image,1040).rows.map(r=>r.state),["obstacle","clear","clear"]);
});
test("missing, undecoded, stale, expired or unconfirmed images cannot report a clear side",()=>{
  const base=frame();
  for(const image of [null,{...base,decoded:false},{...base,stale:true},{...base,knownFreshness:false},{...base,ageMs:2000},{...base,receivedAt:undefined},{...base,id:"another-capture"}]){
    const view=previewSectors(image,1040);
    assert.equal(view.current,false);
    assert.ok(view.rows.every(r=>r.state==="unknown"));
  }
});
test("native camera identity is bound to source rather than the different job identifier",()=>{
  const image=frame();
  image.analysis!.frame_id="pi-decision-job-4";
  image.analysis!.source={frame_id:"capture-7",sequence:327,camera_session:"camera"};
  assert.equal(previewSectors(image,1040).current,true);
  image.analysis!.source={frame_id:"capture-8"};
  assert.equal(previewSectors(image,1040).current,false);
});
test("partial coverage and unknown classes remain unknown even if a clear state is supplied",()=>{
  const image=frame();
  image.analysis!.sectors={left:{state:"clear",coverage_complete:false},center:{state:"clear"},right:{state:"other",coverage_complete:true}};
  const view=previewSectors(image,1040);
  assert.equal(view.rows[0].incomplete,true);
  assert.ok(view.rows.every(r=>r.state==="unknown"));
});
test("image and ground labels are selected by this analysis, never the current policy form",()=>{
  const image=frame();
  image.analysis!.sector_space="projected_paths";
  assert.equal(previewSectors(image,1040).space,"projected_paths");
  for(const space of [null,"image_thirds","unavailable",undefined]){
    image.analysis!.sector_space=space;
    const view=previewSectors(image,1040);
    assert.equal(view.current,false);assert.equal(view.space,"unavailable");
  }
});
test("malformed or absent sector metadata cannot borrow the overall clear verdict",()=>{
  const image=frame();image.analysis!.sectors=[];
  assert.ok(previewSectors(image,1040).rows.every(r=>r.state==="unknown"&&!r.marked));
});

test("region labels and uncertainty explanations are translated in PL EN DE",async()=>{
  const {translate}=await import("../../web-next/lib/i18n");
  for(const language of ["pl","en","de"] as const)
    for(const key of ["sectorDraftHint","frameAnalysisRevision","sectorsTitle","sectorsImageLegend","sectorsProjectedLegend","sectorsUnavailable","sectorsHistorical","sectorIncomplete","sectorsDecisionHint",...["left","center","right"].flatMap(side=>["sector."+side,"sector.path."+side])])
      assert.notEqual(translate(language,key),key);
});
