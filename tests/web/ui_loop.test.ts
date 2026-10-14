import assert from "node:assert/strict";
import { test } from "node:test";
import { calibrationAcceptance, calibrationMatches, executionHeadline, previewNotice, calibrationScope, decisionView, freshAnalysisFrame, loopBlock, loopFailure, loopPhase, loopSettingsValues, motionPermission, trialBlock } from "../../web-next/lib/loop-controls";
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


test("image GO remains distinct from observation STOP and from stale records",()=>{
 const state={running:true,stale:false,mode:"observe",decision:{state:"clear",clear_count:3,vision_action:"go",action:"stop",motion_reason:"observe_only",floor_fraction:1,confidence_fraction:1}};
 const view=decisionView(state,{clear_frames_required:3});
 assert.equal(view.vision,"go");assert.equal(view.reply,"stop");assert.equal(view.reason,"observe_only");
 assert.equal(view.floor,1);assert.equal(view.confidence,1);
 assert.equal(decisionView({...state,stale:true},{}).vision,"none");
 assert.equal(decisionView({...state,latched:true},{}).reply,"none");
});
test("warmup, uncertainty and missing replies cannot appear as image GO",()=>{
 const state={running:true,stale:false,mode:"observe"};
 assert.equal(decisionView({...state,decision:{state:"clear",clear_count:2,action:"stop"}},{}).reason,"clear_frames_pending");
 assert.equal(decisionView({...state,decision:{state:"unknown",clear_count:5,vision_action:"go"}},{}).vision,"stop");
 assert.equal(decisionView(state,{}).reply,"none");
});
test("trial preparation does not demand prior manual or navigation ARM",()=>{
 const policy={mode:"observe",verified:true,calibration_id:"test",model_sha256:"a".repeat(64)};
 const runtime={observe:true,drive:true},detector={configured:true,loaded:true,sha256:"a".repeat(64)};
 const status=(state:string,owner:string|null)=>({connection:{configured:true},errors:{},control:{safety:{state,owner,generation:"one",stale:false,capabilities:{move:{available:true}},remote_decision_deadline_supported:true}}} as unknown as RiderStatus);
 assert.equal(trialBlock(policy,runtime,detector,true,false,status("DISARMED",null)),"");
 assert.equal(trialBlock(policy,runtime,detector,true,false,status("ARMED","manual")),"");
 assert.equal(trialBlock(policy,runtime,detector,true,false,status("ESTOP",null)),"trialEstop");
 assert.equal(trialBlock(policy,runtime,{configured:true,loaded:false,sha256:""},true,false,status("DISARMED",null)),"");
 const stale=status("DISARMED",null);Object.assign(stale.control!.safety as object,{stale:true});
 assert.equal(trialBlock(policy,runtime,detector,true,false,stale),"trialControlStale");
 const unsupported=status("DISARMED",null);Object.assign(unsupported.control!.safety as object,{remote_decision_deadline_supported:false});
 assert.equal(trialBlock(policy,runtime,detector,true,false,unsupported),"loopExecutorUpdate");
});

const permissionPolicy={verified:true,calibration_id:"fixture",model_sha256:"a".repeat(64)};
const permissionDetector={sha256:"a".repeat(64)};
const permissionLoop={running:true,stale:false,mode:"drive",decision:{state:"clear",clear_count:3,action:"go",vision_action:"go",valid_for_ms:150}};
const permissionStatus=(owner="navigation")=>({
 connection:{configured:true,status:"online"},errors:{},settings:{revision:1},
 control:{safety:{generation:"one",state:"ARMED",owner,can_move:true,panel_motion_enabled:true,stale:false,ready:true,capabilities:{move:{available:true}},remote_decision_deadline_supported:true,execution:{}}}
} as unknown as RiderStatus);
test("manual ARM is explicit manual permission, never Venom permission",()=>{
 const result=motionPermission({...permissionLoop,mode:"observe"},{observe:true,drive:false},permissionPolicy,permissionDetector,permissionStatus("manual"));
 assert.equal(result.manual,true);assert.equal(result.venom,false);assert.equal(result.reason,"permissionObservation");
});
test("Venom permission requires navigation ARM, a drive goal and a current valid GO",()=>{
 const check=(state=permissionLoop,goals={drive:true},status=permissionStatus(),policy=permissionPolicy)=>motionPermission(state,goals,policy,permissionDetector,status);
 assert.equal(check().venom,true);assert.equal(check().manual,false);
 assert.equal(check(permissionLoop,{drive:false}).venom,false);
 assert.equal(check({...permissionLoop,mode:"observe"}).venom,false);
 for(const decision of [{...permissionLoop.decision,action:"stop"},{...permissionLoop.decision,clear_count:2},{...permissionLoop.decision,valid_for_ms:49},{...permissionLoop.decision,valid_for_ms:undefined}]){
  assert.equal(check({...permissionLoop,decision} as typeof permissionLoop).venom,false);
 }
 assert.equal(check({...permissionLoop,latched:true} as typeof permissionLoop).venom,false);
 assert.equal(check({...permissionLoop,stale:true}).venom,false);
 assert.equal(check(permissionLoop,{drive:true},permissionStatus(),{...permissionPolicy,verified:false}).venom,false);
});
test("unconfirmed control is explicit unknown; executor inhibition blocks both routes",()=>{
 const stale=permissionStatus();Object.assign(stale.control!.safety as object,{stale:true});
 assert.equal(motionPermission(permissionLoop,{drive:true},permissionPolicy,permissionDetector,stale).venom,null);
 assert.equal(motionPermission(permissionLoop,{drive:true},permissionPolicy,permissionDetector,stale).manual,null);
 const inhibited=permissionStatus("manual");Object.assign(inhibited.control!.safety as object,{execution:{inhibit:"fixture"}});
 const result=motionPermission(permissionLoop,{drive:true},permissionPolicy,permissionDetector,inhibited);
 assert.equal(result.manual,false);assert.equal(result.venom,false);assert.equal(result.reason,"permissionExecutorBlocked");
});

test("image STOP is explained before manual ownership or observation mode",()=>{
 const result=motionPermission({...permissionLoop,mode:"observe",decision:{...permissionLoop.decision,state:"unknown",vision_action:"stop",action:"stop",clear_count:0}},{observe:true,drive:false},permissionPolicy,permissionDetector,permissionStatus("manual"));
 assert.equal(result.manual,true);assert.equal(result.venom,false);assert.equal(result.reason,"permissionDecisionStop");
});

test("fresh image GO in observation is independent of DISARMED executor",()=>{
 const state={...permissionLoop,mode:"observe",decision:{...permissionLoop.decision,action:"stop",motion_reason:"observe_only"}};
 const status=permissionStatus();Object.assign(status.control!.safety as object,{state:"DISARMED",owner:null,can_move:false});
 assert.equal(decisionView(state,permissionDetector).vision,"go");
 assert.equal(decisionView(state,permissionDetector).reply,"stop");
 const permission=motionPermission(state,{observe:true,drive:false},permissionPolicy,permissionDetector,status);
 assert.equal(permission.venom,false);assert.equal(permission.manual,false);
});

test("experimental geometry never enables a physical trial and changing thresholds changes receipt scope",()=>{
 const policy={mode:"observe",perception:{engine:"depth_plane",residual_limit:.06}};
 assert.equal(trialBlock(policy,{}, {},true,false,null),"loopDepthObservation");
 assert.equal(loopBlock({...policy,mode:"drive"},{drive:true},{configured:true},true,false,null),"loopDepthObservation");
 const changed={...policy,perception:{...policy.perception,residual_limit:.08}};
 assert.notEqual(calibrationScope(policy,{}),calibrationScope(changed,{}));
 assert.equal(loopSettingsValues(policy,changed).verified,false);
});

test("running observation with a delayed assessment never claims preview stopped",()=>{
 assert.equal(previewNotice(false,"venom",false,true),"previewDelayedNotice");
 assert.equal(previewNotice(true,"venom",false,true),"previewDelayedNotice");
 assert.equal(previewNotice(true,"venom",false,false),"previewPausedNotice");
 assert.equal(previewNotice(false,"venom",true,true),null);
});
test("operator sees manual ARM and observation as separate from image rejection",()=>{
 const status={control:{safety:{state:"ARMED",owner:"manual"}}} as unknown as RiderStatus;
 assert.equal(executionHeadline({mode:"observe"},{observe:true,drive:false},status,{venom:false}),"permissionVenomObserve");
 assert.equal(executionHeadline({mode:"drive"},{drive:true},status,{venom:false}),"permissionVenomManual");
 const view=decisionView({running:true,stale:false,mode:"observe",decision:{state:"unknown",reason:"vision_low_confidence",motion_reason:"observe_only"}},{});
 assert.equal(view.imageReason,"vision_low_confidence");
});


test("expired previous assessment is not described as waiting for the first result",()=>{
 assert.equal(decisionView({running:false,stale:true,decision:{state:"clear",vision_action:"go"}},{}).imageReason,"analysis_not_current");
 assert.equal(decisionView({running:false,stale:true},{}).imageReason,"waiting_for_decision");
});
