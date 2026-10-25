import assert from "node:assert/strict";
import { test } from "node:test";
import { journeyBlocks, journeyView } from "../../web-next/lib/journey";
import { journeyOwnsMotion, navigationAvailable, operatorSessionActive } from "../../web-next/lib/navigation";
import type { JsonRecord, RiderStatus } from "../../web-next/lib/model";

function scenario() {
  const policy: JsonRecord = {mode:"observe",speed:.1,max_motion_ms:150,verified:true,calibration_id:"operator",model_sha256:"a".repeat(64),avoidance:{enabled:true,protocol_version:3,sweep_verified:true,geometry:{mode:"ground",fx:.5,fy:.5,cx:.5,cy:.5,radius_ratio:.3,lookahead_ratio:2,max_heading_deg:45}}};
  const detector: JsonRecord = {configured:true,loaded:true,sha256:"a".repeat(64),ground_geometry_supported:true,navigation_journey_version:1};
  const status = {connection:{status:"online",configured:true},errors:{},settings:{revision:1},control:{safety:{state:"DISARMED",generation:"fixture",navigation_journey_version:1,remote_decision_deadline_supported:true,scoped_stop_generation_supported:true,capabilities:{move:{available:true}}}}} as unknown as RiderStatus;
  return {policy,detector,status,runtime:{drive:true,observe:true}};
}
test("journey requires negotiated capability on Pi and detector, with concrete configuration gaps",()=>{
  const {policy,detector,status,runtime}=scenario();
  assert.deepEqual(journeyBlocks(policy,runtime,detector,true,false,status),[]);
  assert.deepEqual(journeyBlocks({...policy,avoidance:{...(policy.avoidance as JsonRecord),geometry:{mode:"image"}}},runtime,detector,true,false,status),[]);
  assert.ok(journeyBlocks(policy,runtime,{...detector,navigation_journey_version:undefined},true,false,status).includes("journeyUpdateRequired"));
  (status.control!.safety as JsonRecord).navigation_journey_version=undefined;
  assert.ok(journeyBlocks(policy,runtime,detector,true,false,status).includes("journeyUpdateRequired"));
  const missing={...policy,avoidance:{enabled:false,protocol_version:2,sweep_verified:false,geometry:{mode:"image"}}};
  const blocks=journeyBlocks(missing,runtime,detector,true,false,status);
  for(const key of ["journeyAvoidanceRequired","journeySweepRequired"])assert.ok(blocks.includes(key),key);
});
test("journey uses actual saved geometry and preserves small motion limits",()=>{
  const {policy,detector,status,runtime}=scenario(),avoidance=policy.avoidance as JsonRecord,geometry=avoidance.geometry as JsonRecord;
  for(const key of ["fx","fy","cx","cy","radius_ratio","lookahead_ratio","max_heading_deg"]){
    for(const value of [undefined,null,NaN]){
      const invalid={...policy,avoidance:{...avoidance,geometry:{...geometry,[key]:value}}};
      assert.ok(journeyBlocks(invalid,runtime,detector,true,false,status).includes("geometryCalibrationMissing"),key);
    }
  }
  for(const patch of [{speed:.11},{max_motion_ms:151}])assert.ok(journeyBlocks({...policy,...patch},runtime,detector,true,false,status).includes("journeyMotionLimits"));
  assert.ok(journeyBlocks(policy,runtime,{...detector,sha256:"b".repeat(64)},true,false,status).includes("loopUnverified"));
  assert.deepEqual(journeyBlocks(policy,runtime,{...detector,loaded:false,sha256:""},true,false,status),[]);
  assert.ok(journeyBlocks(policy,runtime,detector,true,true,status).includes("loopUnsaved"));
  assert.ok(journeyBlocks(policy,runtime,detector,false,false,status).includes("loopOffline"));
});
test("journey status does not invent live motion from stale or unknown observations",()=>{
  const value={version:1,id:"journey",active:true,state:"forward",elapsed_s:35,maneuver_epoch:2,reason:"clear"};
  assert.deepEqual(journeyView({journey:value}),{active:true,phase:"forward",historical:false,elapsed:35,maneuver:2,reason:"clear",id:"journey"});
  assert.equal(journeyView({stale:true,journey:value}).phase,"unknown");
  assert.equal(journeyView({journey:{...value,state:"invented"}}).phase,"unknown");
  assert.equal(journeyView({journey:{...value,active:false,state:"stopped"}}).phase,"stopped");
  assert.equal(journeyView({}).phase,"idle");
  assert.equal(journeyView({journey:{...value,version:undefined}}).phase,"idle");
  assert.equal(journeyView({journey:{...value,elapsed_s:NaN}}).elapsed,null);
});
test("navigation journey requires explicit STOP and manual takeover; trial warm intervention remains available",()=>{
  const {status}=scenario();
  const safety=status.control!.safety as JsonRecord;
  Object.assign(safety,{state:"ARMED",owner:"navigation",arming_mode:"journey",panel_motion_enabled:true,panel_control:{version:1,holder:"panel"},operator_control:{version:1,state:"active"}});
  assert.equal(journeyOwnsMotion(status),true);
  assert.equal(navigationAvailable(status,"panel"),false);
  assert.equal(operatorSessionActive(status),false);
  safety.arming_mode="trial";
  assert.equal(journeyOwnsMotion(status),false);
  assert.equal(navigationAvailable(status,"panel"),true);
  assert.equal(operatorSessionActive(status),true);
});

test("warm observation retains the ended journey outcome without reviving its authority",()=>{
  const ended={version:1,id:"ended",active:false,state:"failed",reason:"decision_invalid_or_expired",elapsed_s:6.32,maneuver_epoch:0};
  const result=journeyView({mode:"observe",running:true,last_trial:{journey:ended}});
  assert.equal(result.historical,true);assert.equal(result.phase,"failed");assert.equal(result.active,false);
  assert.equal(result.reason,ended.reason);assert.equal(result.elapsed,6.32);
  assert.equal(journeyView({last_trial:{journey:{...ended,active:true}}}).phase,"idle");
  const current=journeyView({journey:{...ended,id:"new",active:true,state:"qualifying"},last_trial:{journey:ended}});
  assert.equal(current.historical,false);assert.equal(current.id,"new");assert.equal(current.active,true);
});

test("confirm-and-start readiness omits only persisted acceptance and still blocks control/configuration failures",()=>{
  const {policy,detector,status,runtime}=scenario();
  const unaccepted={...policy,verified:false,calibration_id:"",model_sha256:"",avoidance:{...(policy.avoidance as JsonRecord),sweep_verified:false}};
  assert.ok(journeyBlocks(unaccepted,runtime,detector,true,false,status).includes("loopUnverified"));
  assert.deepEqual(journeyBlocks(unaccepted,runtime,detector,true,false,status,false),[]);
  const safety=status.control!.safety as JsonRecord;
  safety.state="ESTOP";
  assert.ok(journeyBlocks(unaccepted,runtime,detector,true,false,status,false).includes("trialEstop"));
  safety.state="DISARMED";safety.stale=true;
  assert.ok(journeyBlocks(unaccepted,runtime,detector,true,false,status,false).includes("trialControlStale"));
  safety.stale=false;safety.execution={inhibit:"local_inhibit"};
  assert.ok(journeyBlocks(unaccepted,runtime,detector,true,false,status,false).includes("trialExecutionInhibited"));
  delete safety.execution;
  assert.ok(journeyBlocks(unaccepted,runtime,detector,false,false,status,false).includes("loopOffline"));
  assert.ok(journeyBlocks(unaccepted,runtime,detector,true,true,status,false).includes("loopUnsaved"));
  assert.ok(journeyBlocks({...unaccepted,speed:.11},runtime,detector,true,false,status,false).includes("journeyMotionLimits"));
  assert.ok(journeyBlocks(unaccepted,{...runtime,drive:false},detector,true,false,status,false).includes("loopRuntimeMissing"));
  assert.ok(journeyBlocks(unaccepted,runtime,{...detector,navigation_journey_version:undefined},true,false,status,false).includes("journeyUpdateRequired"));
});

test("extended pulses require the Pi capability and preserve the speed limit",()=>{
 const {policy,detector,status,runtime}=scenario();
 for(const duration of [190,250]){
  assert.ok(journeyBlocks({...policy,max_motion_ms:duration},runtime,detector,true,false,status).includes("journeyMotionLimits"));
  assert.ok(!journeyBlocks({...policy,max_motion_ms:duration,motion_limits:{version:1,max_motion_ms:250}},runtime,detector,true,false,status).includes("journeyMotionLimits"));
 }
 assert.ok(journeyBlocks({...policy,max_motion_ms:251,motion_limits:{version:1,max_motion_ms:250}},runtime,detector,true,false,status).includes("journeyMotionLimits"));
});
