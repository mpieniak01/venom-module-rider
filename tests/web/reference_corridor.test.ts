import assert from "node:assert/strict";
import {test} from "node:test";
import {initialReference} from "../../web-next/lib/image-reference";
import {collisionDraft,validCollision} from "../../web-next/lib/collision-geometry";
import {geometryFromReference,geometryFromSavedReference,referenceCorridor} from "../../web-next/lib/reference-corridor";

const roi=[.35,.8,.65,.95];
test("saved robot width and camera edges form a full-width conservative corridor",()=>{
 const ref=initialReference(),result=referenceCorridor(ref,roi)!;
 assert.deepEqual(result,[.320263,.8,.675029,.95]);
 assert(result[0]<.346542&&result[2]>.648749);
 for(const y of [.8,.85,.9,.95]){
  const t=(y-ref.left_edge[0][1])/(ref.left_edge[1][1]-ref.left_edge[0][1]);
  const l=ref.left_edge[0][0]+t*(ref.left_edge[1][0]-ref.left_edge[0][0]);
  const r=ref.right_edge[0][0]+t*(ref.right_edge[1][0]-ref.right_edge[0][0]);
  const half=(r-l)*(ref.robot_width_cm+2)/ref.track_width_cm/2,center=(l+r)/2;
  assert(result[0]<=center-half&&result[2]>=center+half);
 }
 assert(referenceCorridor({...ref,robot_width_cm:12},roi)![0]<result[0]);
 assert(referenceCorridor({...ref,track_width_cm:20},roi)![0]>result[0]);
});
test("unobserved reference rows and out-of-frame envelopes cannot become valid",()=>{
 const ref=initialReference();
 assert.equal(referenceCorridor(ref,[.35,.6,.65,.95]),null);
 assert.equal(referenceCorridor(ref,[.35,.8,.65,1]),null);
 assert.equal(referenceCorridor({...ref,robot_width_cm:16,left_edge:[[0,ref.left_edge[0][1]],[0,ref.left_edge[1][1]]],right_edge:[[1,ref.right_edge[0][1]],[1,ref.right_edge[1][1]]]},roi),null);
 const shifted={...ref,right_edge:[[ref.right_edge[0][0],ref.right_edge[0][1]+0.0000005],[...ref.right_edge[1]]] as typeof ref.right_edge};
 assert.equal(referenceCorridor(shifted,[.35,ref.left_edge[0][1],.65,.95]),null);
 assert(referenceCorridor(shifted,roi));
});
test("import changes the decision geometry and retains tuning and prior rotation",()=>{
 const collision=collisionDraft(roi,true);
 collision.left_shift=.2;collision.guidance!.rotation_bounds=[.2,.88,.8,.95];
 const result=geometryFromReference(initialReference(),roi,{mode:"image",collision})!;
 assert(validCollision(result.geometry.collision,result.corridor));
 assert.equal((result.geometry.collision as typeof collision).far_width_ratio,.811906);
 assert.equal((result.geometry.collision as typeof collision).left_shift,.2);
 assert.deepEqual((result.geometry.collision as typeof collision).guidance!.rotation_bounds,[.2,.88,.8,.95]);
 assert.notDeepEqual(result.corridor,roi);
});
test("taper contains the measured body and margin in every row",()=>{
 const ref=initialReference();
 for(const endFraction of [.05,.2,.5]){
  const collision={...collisionDraft(roi,true),end_fraction:endFraction};
  const result=geometryFromReference(ref,roi,{mode:"image",collision})!;
  const [x0,y0,x1,y1]=result.corridor,axis=(x0+x1)/2;
  const far=(result.geometry.collision as typeof collision).far_width_ratio;
  assert(far<1);
  for(let i=0;i<=100;i++){
   const y=y0+(y1-y0)*i/100,t=(y-ref.left_edge[0][1])/(ref.left_edge[1][1]-ref.left_edge[0][1]);
   const l=ref.left_edge[0][0]+t*(ref.left_edge[1][0]-ref.left_edge[0][0]);
   const r=ref.right_edge[0][0]+t*(ref.right_edge[1][0]-ref.right_edge[0][0]);
   const bodyHalf=(r-l)*(ref.robot_width_cm+2)/ref.track_width_cm/2,bodyCenter=(l+r)/2;
   const travel=Math.min((y1-y)/(y1-y0)/(1-endFraction),1);
   const pathHalf=(x1-x0)*(1+(far-1)*travel)/2;
   assert(axis-pathHalf<=bodyCenter-bodyHalf+1e-12);
   assert(axis+pathHalf>=bodyCenter+bodyHalf-1e-12);
  }
 }
});
test("only an existing Pi reference may change the collision policy draft",()=>{
 const geometry={mode:"image",collision:collisionDraft(roi,true)};
 const saved={version:1,revision:2,purpose:"visual_reference_only",values:initialReference()};
 assert(geometryFromSavedReference(saved,roi,geometry));
 assert.equal(geometryFromSavedReference({...saved,revision:0},roi,geometry),null);
 assert.equal(geometryFromSavedReference({...saved,values:null},roi,geometry),null);
 assert.equal(geometryFromSavedReference({...saved,purpose:"unknown"},roi,geometry),null);
 assert.equal(geometryFromSavedReference({...saved,values:{...saved.values,robot_width_cm:NaN}},roi,geometry),null);
});
