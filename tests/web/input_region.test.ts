import assert from "node:assert/strict";
import {test} from "node:test";
import {inputBounds,inputCovers,region,RECOMMENDED_INPUT} from "../../web-next/lib/input-region";
import {readGeometry} from "../../web-next/lib/overlay-geometry";
import {sameAnalysisSettings} from "../../web-next/lib/analysis-video";
import {analysisProfileSettings,riderAnalysisSettings} from "../../web-next/lib/analysis-profile";
import {tuningWrite} from "../../web-next/lib/model-tuning";
const crop=region(RECOMMENDED_INPUT),corridor=[.35,.8,.65,.95];
test("legacy is full frame, new recommended preserves reference; malformed/partial is rejected",()=>{
 assert.deepEqual(inputBounds(undefined),[0,0,1,1]);assert(inputCovers(crop,corridor));
 for(const b of [[0,.7,1,1],[.4,.4,.6,1],[0,NaN,1,1]])assert(!inputCovers(region(b),corridor));
 assert.deepEqual(inputBounds({version:true,bounds:[0,0,1,1]}),[]);
});
test("crop is frozen and only DA2 inherits it",()=>{
 const p={input_region:crop,residual_limit:.06};
 assert.deepEqual(analysisProfileSettings({id:"da2-default",installed:true,windows:[1]},1,{mode:"image"},p).perception.input_region,crop);
 assert.equal(analysisProfileSettings({id:"other",installed:true,windows:[1]},1,{mode:"image"},p).perception.input_region,undefined);
 const old={context:{spec:{profile:"da2-default"}}};
 assert(sameAnalysisSettings(old,{profile:"da2-default",perception:{input_region:region([0,0,1,1])}}));
 assert(!sameAnalysisSettings(old,{profile:"da2-default",perception:p}));
 const copied=riderAnalysisSettings({revision:1,corridor,perception:p});assert.deepEqual(copied.perception.input_region,crop);
});
test("operator cannot submit crop to unsupported backend or exclude reference",()=>{
 const perception={engine:"depth_plane",model_id:"depth-anything-v2-indoor-small",residual_limit:.06,outlier_limit:.005,slope_min:2,input_region:crop};
 const policy={revision:1,corridor,perception,input_region_supported:true},draft={revision:1,session:"s",modelId:perception.model_id,perception};
 assert(tuningWrite(draft,policy,"s"));assert.equal(tuningWrite(draft,{...policy,input_region_supported:false},"s"),null);
 assert.equal(tuningWrite({...draft,perception:{...perception,input_region:region([0,.7,1,1])}},policy,"s"),null);
});
test("drawing accepts only bounded geometry matching the decoded frame",()=>{
 const g={version:1,width:320,height:240,input_bounds:[0,96,320,240],score_bounds:[112,192,208,228]};
 assert(readGeometry(g,[320,240]));assert.equal(readGeometry(g,[640,480]),null);
 assert.equal(readGeometry({...g,paths:{center:[[1,NaN]]}}),null);
 assert.equal(readGeometry({...g,objects:[{id:1,bounds:[0,0,321,240],evidence:"transient"}]}),null);
});
