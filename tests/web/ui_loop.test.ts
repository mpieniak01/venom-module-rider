import assert from "node:assert/strict";
import { test } from "node:test";
import { loopBlock, loopFailure, loopPhase } from "../../web-next/lib/loop-controls";
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
 const policy={mode:"drive",verified:true,model_sha256:"sha"};
 const detector={configured:true,sha256:"sha"};
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
