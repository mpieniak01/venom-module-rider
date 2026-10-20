import assert from "node:assert/strict";
import {test} from "node:test";
import {alignTrial,pulseEvidence,nearestTime,pointAt,profileAt,readTimeline,seriesSegments,streamTimes,type MotionPoint,type MotionStream} from "../../web-next/lib/motion-timeline";

function point(t:number,segment=1,unit="deg",valid=true):MotionPoint {
  return {id:"p"+t,t,segment,valid,instance_id:"boot",source_time:t,values:{yaw:t},units:{yaw:unit},quality:{}};
}
function stream():MotionStream {return {id:"s",device_id:"d",source_session:"host",clock:"host-reception-monotonic",origin:"first_receipt",range:[0,2],points:[point(0),point(1),point(2,2)],events:[],outputs:[],profiles:{}};}
test("one cursor never invents a reading between received samples",()=>{
  const s=stream();
  assert.equal(pointAt(s,.5),null);
  assert.equal(pointAt(s,1)?.id,"p1");
  assert.equal(nearestTime([0,1,2],1.7),2);
  assert.deepEqual(streamTimes(s,null),[0,1,2]);
});
test("line segments break on gaps, reset, stale state and incompatible units",()=>{
  const data=[point(0),point(.3),point(.6,2),point(.9,2,"rad"),point(1.2,2,"deg",false),point(1.5,2)];
  assert.deepEqual(seriesSegments(data,"yaw","deg").map(s=>s.map(p=>p.t)),[[0,.3],[.6],[1.2],[1.5]]);
});
test("archive selection validates bounds and preserves explicit empty streams",()=>{
  const data={schema_version:1,dataset_revision:"rev",session_id:"session",streams:[stream()]};
  assert.equal(readTimeline(data).streams.length,1);
  assert.throws(()=>readTimeline({...data,schema_version:2}));
  assert.throws(()=>readTimeline({...data,streams:[{...stream(),range:[NaN,1]}]}));
  assert.throws(()=>readTimeline({...data,streams:[{...stream(),range:[-1e308,1e308]}]}));
  assert.throws(()=>readTimeline({...data,streams:[{...stream(),points:[{...point(0),t:Infinity}]}]}));
});
test("profile follows recorded request identity, never current application settings",()=>{
  const s=stream();
  assert.equal(profileAt(s,1),null);
  s.profiles.hash={motion_revision:3};
  s.events=[{id:"e",t:.3,operation_id:"op",phase:"requested",intent:{action:"move"},profile_ref:"hash",outcome:"pending",reason:null,ack_status:null}];
  assert.equal(profileAt(s,0),null);
  assert.equal(profileAt(s,1)?.motion_revision,3);
});

test("trial alignment moves every channel and sparse impulses remain insufficient",()=>{
  const s=stream();
  s.profiles.hash={motion_revision:3};
  s.events=[{id:"event",t:1,operation_id:"op",phase:"requested",intent:{action:"move",duration_s:.15},profile_ref:"hash",outcome:"pending",reason:null,ack_status:null}];
  const aligned=alignTrial(s,"op")!;
  assert.equal(aligned.events[0].t,0);
  assert.deepEqual(aligned.points.map(p=>p.t),[-1,0,1]);
  assert.equal(s.events[0].t,1);
  assert.equal(pulseEvidence(aligned).peaks,null);
  assert.equal(pulseEvidence(aligned).profile?.motion_revision,3);
});
