import assert from "node:assert/strict";
import {test} from "node:test";
import {attitudeAt,eulerZYX,inverse,multiply,project,qualifyAttitude,relativeAttitude,replayStep,rotate,type Vector3} from "../../web-next/lib/attitude";
import type {MotionPoint,MotionStream} from "../../web-next/lib/motion-timeline";
const close=(a:number,b:number)=>assert.ok(Math.abs(a-b)<1e-10,a+" != "+b);
const vector=(a:Vector3,b:Vector3)=>a.forEach((v,i)=>close(v,b[i]));
function point(values={roll:0,pitch:0,yaw:0}):MotionPoint {
  return {id:"p",t:0,segment:1,valid:true,instance_id:"boot",source_time:100,values,units:{roll:"deg",pitch:"deg",yaw:"deg"},quality:{
    ready:true,stale:false,offline:false,simulated:false,
    orientation:{convention:"rider-body-zyx-v1",revision:1,verified:true,qualification_ref:"known-poses-v1",instance_id:"boot",unit:"deg"},
    sensors:Object.fromEntries(["roll","pitch","yaw"].map(axis=>[axis,{ready:true,stale:false,offline:false,instance_id:"boot",unit:"deg",observed_at:100}]))}};
}
test("active right-handed single-axis and ordered composite rotations",()=>{
  vector(rotate(eulerZYX(Math.PI/2,0,0),[0,1,0]),[0,0,1]);
  vector(rotate(eulerZYX(0,Math.PI/2,0),[1,0,0]),[0,0,-1]);
  vector(rotate(eulerZYX(0,0,Math.PI/2),[1,0,0]),[0,1,0]);
  // Rx first, then Ry, then Rz: Y -> Z -> X -> Y.
  vector(rotate(eulerZYX(Math.PI/2,Math.PI/2,Math.PI/2),[0,1,0]),[0,1,0]);
  const q=eulerZYX(.3,-.8,1.1);
  vector(rotate(multiply(inverse(q),q),[1,2,3]),[1,2,3]);
});
test("yaw wrap is a two-degree orientation change without artificial full turns",()=>{
  const a=eulerZYX(0,0,179*Math.PI/180),b=eulerZYX(0,0,-179*Math.PI/180);
  const delta=multiply(inverse(a),b);
  close(2*Math.acos(Math.abs(delta[0]))*180/Math.PI,2);
});
test("relative attitude uses a rotation and rejects a reset or different convention",()=>{
  const a=qualifyAttitude(point({roll:40,pitch:30,yaw:20}));
  const b=qualifyAttitude(point({roll:45,pitch:50,yaw:70}));
  const relative=relativeAttitude(b,a);
  assert.equal(relative.state,"available");
  if(relative.state!=="available"||a.state!=="available"||b.state!=="available")throw Error();
  vector(rotate(multiply(a.q,relative.q),[1,0,0]),rotate(b.q,[1,0,0]));
  const reset={...b,point:{...b.point,segment:2}};
  assert.equal(relativeAttitude(reset,a).state,"unavailable");
  const bad={...b,identity:"other"};
  assert.equal(relativeAttitude(bad,a).state,"unavailable");
});
test("qualified degrees and radians are equivalent and singular Euler data stays finite",()=>{
  const deg=qualifyAttitude(point({roll:0,pitch:90,yaw:0}));
  assert.equal(deg.state,"available");
  if(deg.state!=="available")throw Error();
  assert.equal(deg.singular,true);
  assert.ok(deg.q.every(Number.isFinite));
  const rad=point({roll:0,pitch:Math.PI/2,yaw:0});
  rad.units={roll:"rad",pitch:"rad",yaw:"rad"};
  (rad.quality.orientation as Record<string,unknown>).unit="rad";
  for(const row of Object.values(rad.quality.sensors as Record<string,Record<string,unknown>>))row.unit="rad";
  const r=qualifyAttitude(rad);
  assert.equal(r.state,"available");
  if(r.state==="available")r.q.forEach((v,i)=>close(v,deg.q[i]));
});
test("missing units, metadata, freshness and incoherent axes never produce geometry",()=>{
  const cases:Array<(p:MotionPoint)=>void>=[
    p=>{delete p.quality.orientation;},
    p=>{p.units.yaw="sdk_raw";},
    p=>{p.quality.stale=true;},
    p=>{p.quality.offline="unknown";},
    p=>{p.values.roll=Infinity;},
    p=>{p.values.pitch=500;},
    p=>{p.instance_id=null;},
    p=>{(p.quality.orientation as Record<string,unknown>).verified=false;},
    p=>{(p.quality.orientation as Record<string,unknown>).convention="intrinsic-XYZ";},
    p=>{((p.quality.sensors as Record<string,Record<string,unknown>>).yaw).observed_at=100.01;},
    p=>{((p.quality.sensors as Record<string,Record<string,unknown>>).roll).instance_id="other";},
  ];
  for(const change of cases){const p=point();change(p);assert.notEqual(qualifyAttitude(p).state,"available");}
  assert.equal(qualifyAttitude(null).state,"unavailable");
});
test("exact cursor and replay do not interpolate gaps or contact a transport",()=>{
  const stream={points:[point()]} as MotionStream;
  assert.equal(attitudeAt(stream,.2).state,"unavailable");
  assert.deepEqual(replayStep([0,.2,2],0),{next:.2,delay:200,gap:false});
  assert.deepEqual(replayStep([0,.2,2],.2),{next:2,delay:1000,gap:true});
  assert.deepEqual(replayStep([0,.2,2],2),{next:null,delay:0,gap:false});
  assert.ok(project([1,1,1]).every(Number.isFinite));
});

test("provisional axes explicitly allow an assumed unit without qualifying source data",()=>{
  const p=point({roll:10,pitch:20,yaw:30});
  delete p.quality.orientation;
  p.units={roll:"sdk_raw",pitch:"sdk_raw",yaw:"sdk_raw"};
  for(const row of Object.values(p.quality.sensors as Record<string,Record<string,unknown>>))row.unit="sdk_raw";
  const strict=qualifyAttitude(p);
  assert.equal(strict.state,"unqualified");
  const preview=qualifyAttitude(p,{enabled:true,assumedUnit:"deg"});
  assert.equal(preview.state,"available");
  if(preview.state==="available"){assert.equal(preview.provisional,true);assert.equal(preview.unitAssumption,true);}
  assert.equal(p.units.yaw,"sdk_raw");
  assert.equal(qualifyAttitude({...p,source_time:0},{enabled:true,assumedUnit:"deg"}).state,"unavailable");
});
