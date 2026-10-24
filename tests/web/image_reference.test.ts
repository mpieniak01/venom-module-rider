import assert from "node:assert/strict";
import test from "node:test";
import {bodyEdges,moveReference,readReference,validReference,type ImageReference} from "../../web-next/lib/image-reference";
import {imagePoint} from "../../web-next/lib/corridor-editor";
const fixture=():ImageReference=>({visible:true,frame_size:[320,240],track_width_cm:16,robot_width_cm:11.5,camera_height_cm:14,reference_pose:"lowest",left_edge:[[.39,.65],[.29,1]],right_edge:[[.58,.65],[.74,1]],markers:[[18,1],[20,227/240],[40,181/240],[60,160/240]]});
test("measured body width occupies the same proportion of the reference track at both rows",()=>{
  const v=fixture(),[left,right]=bodyEdges(v);
  for(let i=0;i<2;i++)assert.ok(Math.abs((right[i][0]-left[i][0])/(v.right_edge[i][0]-v.left_edge[i][0])-11.5/16)<1e-9);
  assert.ok(validReference(v));
});
test("marker drag preserves distance labels, ordering and the saved reference",()=>{
  const original=fixture(),next=moveReference(original,"marker:1",.5,.2);
  assert.equal(original.markers[1][1],227/240);
  assert.equal(next.markers[1][0],20);
  assert.ok(next.markers[1][1]>next.markers[2][1]&&validReference(next));
});
test("edge drag keeps both row coordinates aligned and cannot invert the track",()=>{
  const next=moveReference(fixture(),"left:0",1,1);
  assert.equal(next.left_edge[0][1],next.right_edge[0][1]);
  assert.ok(next.left_edge[0][0]<next.right_edge[0][0]&&validReference(next));
});
test("invalid dimensions and out-of-order markers cannot be saved",()=>{
  for(const bad of [{...fixture(),camera_height_cm:NaN},{...fixture(),robot_width_cm:17},{...fixture(),markers:[[40,.8],[20,.9]]}])assert.equal(validReference(bad as ImageReference),false);
});
test("pointer conversion respects the complete image inside horizontal letterboxing",()=>{
  const p=imagePoint(54.6666667,0,896,590,320,240);
  assert.ok(p&&Math.abs(p[0])<1e-6&&p[1]===0);
  assert.equal(imagePoint(10,100,896,590,320,240),null);
});
test("navigation authority or malformed replies cannot become reference configuration",()=>{
  assert.equal(readReference({version:1,revision:2,purpose:"navigation",values:fixture()}),null);
  assert.equal(readReference({version:1,revision:-1,purpose:"visual_reference_only",values:fixture()}),null);
  assert.equal(readReference({}),null);
  assert.equal(readReference({version:1,revision:2,purpose:"visual_reference_only",values:fixture()})?.revision,2);
});