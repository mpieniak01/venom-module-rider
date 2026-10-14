import assert from "node:assert/strict";
import { test } from "node:test";
import { calibrationAcceptance, calibrationMatches, calibrationScope, freshAnalysisFrame, loopBlock, loopFailure, loopPhase, loopSettingsValues } from "../../web-next/lib/loop-controls";
import { featureRows, frameRequested, type RiderStatus } from "../../web-next/lib/model";

test("confirmed stopped goals stay stopped when the last loop file has aged",()=>{
 assert.equal(loopPhase(true,{running:false,stale:true},{observe:false,drive:false}),"stopped");
 assert.equal(loopPhase(true,{running:true,stale:true},{observe:true,drive:false}),"unknown");
 assert.equal(loopPhase(false,{running:true,stale:false},{observe:true}),"offline");
 assert.equal(loopPhase(true,{running:true,stale:false},{observe:true}),"running");
 assert.equal(loopPhase(true,{},{}),"unknown");
});
test("observation is independent of manual ARM; draft, absent runtime and model block start",()=>{
 const policy={mode:"observe"},runtime={observe:true},detector={configured:true};
 assert.equal(loopBlock(policy,runtime,detector,true,false,null),"");
 assert.equal(loopBlock(policy,runtime,detector,true,true,null),"loopUnsaved");
 assert.equal(loopBlock(policy,{},detector,true,false,null),"loopRuntimeMissing");
 assert.equal(loopBlock(policy,runtime,{},true,false,null),"loopNoModel");
});
test("drive requires validated current model and fresh navigation authority",()=>{
 const policy={mode:"drive",verified:true,calibration_id:"operator-test",model_sha256:"a".repeat(64)};
 const detector={configured:true,sha256:"a".repeat(64)};
 const state=(state:string,owner:string)=>({control:{safety:{state,owner}}} as unknown as RiderStatus);
 assert.equal(loopBlock(policy,{drive:true},detector,true,false,state("ARMED","manual")),"loopNeedsArm");
 assert.equal(loopBlock(policy,{drive:true},detector,true,false,state("DISARMED","navigation")),"loopNeedsArm");
 assert.equal(loopBlock(policy,{drive:true},detector,true,false,state("ARMED","navigation")),"");
 assert.equal(loopBlock(policy,{drive:true},{...detector,sha256:"changed"},true,false,null),"loopUnverified");
 const stale=state("ARMED","navigation");Object.assign(stale.control!.safety as object,{stale:true});
 assert.equal(loopBlock(policy,{drive:true},detector,true,false,stale),"loopNeedsArm");
});
test("camera routing accepts the shared vision workspace and rejects background tabs",()=>{
 const media={camera:"venom" as const,face:false};
 assert.equal(frameRequested("camera","/camera/venom","vision",media),true);
 assert.equal(frameRequested("camera","/camera/raw","vision",media),false);
 assert.equal(frameRequested("camera","/camera/venom","compute",media),false);
});
test("real Pi task rows nested under state override catalog observations",()=>{
 const status={catalog:{features:[{name:"s15_venom_perception",requested:false}]},logic:{state:{features:[{name:"s15_venom_perception",requested:true,state:"running"}]}}} as unknown as RiderStatus;
 assert.equal(featureRows(status)[0].requested,true);
 assert.equal(featureRows(status)[0].state,"running");
});
test("transport failures and unknown operation outcome have specific operator guidance",()=>{
 assert.equal(loopFailure({data:{detail:{code:"pi_unreachable"}}}),"loopOffline");
 assert.equal(loopFailure({data:{detail:{code:"pi_operation_timeout"}}}),"operationUnconfirmed");
});

test("a latched remote stop is a fault and never a starting or running loop",()=>{
 assert.equal(loopPhase(true,{running:false,stale:false,latched:true},{drive:true,observe:false}),"fault");
 assert.equal(loopPhase(true,{running:false,stale:false,latched:true},{drive:false,observe:false}),"stopped");
});

test("calibration acceptance binds the loaded model and a non-empty operator record",()=>{
 const policy={revision:4,protocol_version:1,mode:"observe",speed:.1,corridor:[.35,.65,.65,.95]};
 const detector={sha256:"a".repeat(64)};
 const values=calibrationAcceptance(policy,detector,"operator-test");
 assert.equal(values.model_sha256,detector.sha256);
 assert.equal(values.calibration_id,"operator-test");
 assert.equal(values.verified,true);
 assert.equal(values.mode,"observe");
 assert.equal(values.revision,undefined);
 assert.equal(calibrationMatches(values,detector),true);
 assert.equal(calibrationMatches({...values,calibration_id:""},detector),false);
 assert.equal(calibrationMatches(values,{sha256:"b".repeat(64)}),false);
 assert.throws(()=>calibrationAcceptance(policy,{sha256:""},"operator-test"));
 assert.throws(()=>calibrationAcceptance(policy,detector," "));
});
test("changing the stopping zone, speed or deadlines revokes prior acceptance; mode alone preserves it",()=>{
 const policy={revision:4,mode:"observe",verified:true,calibration_id:"operator-test",model_sha256:"a".repeat(64),speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95]};
 for(const patch of [{speed:.12},{max_motion_ms:100},{decision_budget_ms:200},{corridor:[.2,.5,.8,.9]}]){
  const values=loopSettingsValues(policy,{...policy,...patch});
  assert.equal(values.verified,false);assert.equal(values.calibration_id,"");assert.equal(values.model_sha256,"");
  assert.notEqual(calibrationScope(policy,{}),calibrationScope({...policy,...patch},{}));
 }
 assert.equal(loopSettingsValues(policy,{...policy,mode:"drive"}).verified,true);
 assert.notEqual(calibrationScope(policy,{sha256:"a"}),calibrationScope(policy,{sha256:"b"}));
});
test("zone acceptance requires a visible fresh processed image, never RAW, stale or diagnostic defaults",()=>{
 const frame={decoded:true,stale:false,knownFreshness:true,fileAt:"100",id:"frame",visionState:"clear",visionPolicy:"pi_config"};
 assert.equal(freshAnalysisFrame(frame,"venom",100500),true);
 assert.equal(freshAnalysisFrame(frame,"raw",100500),false);
 assert.equal(freshAnalysisFrame(null,"venom",100500),false);
 assert.equal(freshAnalysisFrame(frame,"venom",104000),false);
 for(const patch of [{decoded:false},{decoded:undefined},{stale:true},{knownFreshness:false},{visionPolicy:"diagnostic_default"},{id:""},{fileAt:""},{visionState:""}]){
  assert.equal(freshAnalysisFrame({...frame,...patch},"venom",100500),false);
 }
});
