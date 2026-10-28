import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { test } from "node:test";
import { calibrationAcceptance, calibrationMatches, completedTrial, executionHeadline, freshPolicyFrame, previewNotice, calibrationScope, decisionView, freshAnalysisFrame, loopBlock, loopFailure, loopPhase, loopSettingsValues, navigationPulseLimit, navigationSettingsDriveBlocked, navigationReadCurrent, refreshStartedNavigation, motionPermission, stoppingZone, stoppingZoneValues, trialBlock, trialPreparation, trialPreparationTitle, trialPreparationErrorKey, trialDiagnostics, trialStopReasonKey, trialOutcomeTone, freshObservation } from "../../web-next/lib/loop-controls";
import { translate } from "../../web-next/lib/i18n";
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
 const status=(state:string,owner:string|null)=>({connection:{configured:true},errors:{},control:{safety:{state,owner,generation:"one",stale:false,capabilities:{move:{available:true}},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true}}} as unknown as RiderStatus);
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
 control:{safety:{generation:"one",state:"ARMED",owner,can_move:true,panel_motion_enabled:true,stale:false,ready:true,capabilities:{move:{available:true}},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true,execution:{}}}
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
 assert.equal(trialBlock(policy,{observe:true,drive:true}, {configured:true,drive_supported:true},true,false,null),"loopUnverified");
 assert.equal(loopBlock({...policy,mode:"drive"},{drive:true},{configured:true},true,false,null),"loopUnverified");
 const changed={...policy,perception:{...policy.perception,residual_limit:.08}};
 assert.notEqual(calibrationScope(policy,{}),calibrationScope(changed,{}));
 assert.equal(loopSettingsValues(policy,changed).verified,false);
});

test("preview freshness is independent of whether a Pi navigation producer runs",()=>{
 for(const polling of [false,true]){
  assert.equal(previewNotice(false,"venom",polling),null);
  assert.equal(previewNotice(false,"raw",polling),null);
 }
 assert.equal(previewNotice(true,"venom",true),"previewDelayedNotice");
 assert.equal(previewNotice(true,"venom",false),"previewPausedNotice");
 assert.equal(previewNotice(true,"raw",true),"previewDelayedNotice");
});
test("a visible host snapshot never becomes calibration or movement permission",()=>{
 const frame={decoded:true,stale:false,knownFreshness:true,id:"standalone",fileAt:"",visionState:"clear",visionPolicy:"device_policy",ageMs:50,receivedAt:performance.now(),ageSource:"host-received",analysis:{run_id:"standalone",config_revision:33,state:"clear",vision_action:"stop"}};
 assert.equal(previewNotice(frame.stale,"venom",true),null);
 assert.equal(freshAnalysisFrame(frame,"venom"),false);
 assert.equal(freshPolicyFrame(frame,"venom",{run_id:"standalone"},{revision:33}),false);
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


test("accepted depth trial is available while unsupported runtime still blocks execution",()=>{
 const policy={...permissionPolicy,mode:"observe",perception:{engine:"depth_plane"}};
 const detector={...permissionDetector,configured:true,loaded:true,drive_supported:true};
 assert.equal(trialBlock(policy,{observe:true,drive:true},detector,true,false,permissionStatus("manual")),"");
 assert.equal(trialBlock(policy,{observe:true,drive:true},{...detector,drive_supported:false},true,false,permissionStatus("manual")),"loopDepthObservation");
});

test("latched driving STOP remains a fault after goals are rolled back",()=>{
 assert.equal(loopPhase(true,{mode:"drive",running:false,stale:true,latched:true},{drive:false,observe:false}),"fault");
 assert.equal(loopPhase(true,{mode:"observe",running:true,stale:false,latched:false},{drive:false,observe:true}),"running");
});


test("both panels retain the Pi trial outcome while current observation gives GO",()=>{
 const last_trial={mode:"drive",running:false,run_id:"failed-drive",observed_at:1792043923,stop_reason:"decision_timeout"};
 const chrome={mode:"observe",running:true,decision:{state:"clear",vision_action:"go"},last_trial};
 const embedded={...chrome,last_trial:{...last_trial}};
 assert.deepEqual(completedTrial(chrome),last_trial);
 assert.deepEqual(completedTrial(embedded),completedTrial(chrome));
 assert.equal(decisionView(chrome,{configured:true}).vision,"stop"); // History never grants GO.
 assert.equal(completedTrial({...chrome,mode:"drive",running:true}),null);
 assert.equal(completedTrial({...chrome,last_trial:{...last_trial,running:true}}),null);
 assert.equal(completedTrial({...chrome,last_trial:{mode:"observe",running:false}}),null);
});


test("near stopping extent preserves path width and nearest edge without increasing motion",()=>{
 const policy={revision:4,protocol_version:1,mode:"drive",verified:true,calibration_id:"old",model_sha256:"a".repeat(64),corridor:[.25,.65,.75,.95],speed:.12,max_motion_ms:180,decision_budget_ms:300};
 const near=stoppingZoneValues(policy,"near");
 assert.deepEqual(near?.corridor,[.25,.8,.75,.95]);
 assert.equal(near?.speed,.1);assert.equal(near?.max_motion_ms,150);
 assert.equal(near?.decision_budget_ms,300);assert.equal(near?.verified,false);
 assert.equal(near?.calibration_id,"");assert.equal(near?.model_sha256,"");
 assert.equal(stoppingZone(near),"near");
 const far=stoppingZoneValues({...policy,...near},"far");
 assert.deepEqual(far?.corridor,policy.corridor);
 assert.equal(far?.speed,.1);assert.equal(far?.max_motion_ms,150);
 assert.equal(stoppingZone(far),"far");
 assert.equal(stoppingZone({...policy,corridor:[.2,.4,.8,.9]}),"custom");
});

test("stopping extents reject unknown bounds and keep slower operator limits",()=>{
 for(const corridor of [null,[],[0,0,1,NaN],[.8,.6,.2,.95],[0,0,1,.1]]){
  assert.equal(stoppingZoneValues({corridor},"near"),null);
 }
 const values=stoppingZoneValues({corridor:[.35,.65,.65,.95],speed:.05,max_motion_ms:80},"near");
 assert.equal(values?.speed,.05);assert.equal(values?.max_motion_ms,80);
});


test("accepting a near zone requires the displayed JPEG to match its current run and revision",()=>{
 const frame={decoded:true,stale:false,knownFreshness:true,id:"capture",fileAt:"",visionState:"clear",visionPolicy:"device_policy",ageMs:30,receivedAt:performance.now(),ageSource:"pi-reported-plus-host-elapsed",analysis:{run_id:"new-zone",config_revision:8,state:"clear",vision_action:"go"}};
 const state={run_id:"new-zone"},policy={revision:8};
 assert.equal(freshPolicyFrame(frame,"venom",state,policy),true);
 assert.equal(freshPolicyFrame({...frame,analysis:{...frame.analysis,config_revision:7}},"venom",state,policy),false);
 assert.equal(freshPolicyFrame({...frame,analysis:{...frame.analysis,run_id:"old-zone"}},"venom",state,policy),false);
 assert.equal(freshPolicyFrame({...frame,analysis:{...frame.analysis,state:"obstacle"}},"venom",state,policy),false);
 assert.equal(freshPolicyFrame({...frame,analysis:undefined},"venom",state,policy),false);
 assert.equal(freshPolicyFrame(null,"venom",state,policy),false);
 assert.equal(stoppingZoneValues({corridor:[.35,.65,.65,.95]},"near"),null);
});


const preparation = {
 request_id:"prepare-one", phase:"camera_reservation", state:"preparing",
 elapsed_ms:1250, observed_at:1792043923, error:null, fresh:true,
};
const preparationError = (value:Record<string,unknown>)=>({data:{detail:{code:"navigation_trial_prepare_timeout",details:{preparation:value}}}});
test("preparation phases and Pi elapsed time are presentation only",()=>{
 for(const phase of ["admission","camera_reservation","stop_old_producer","configure","authority_handoff","arm_ack","start_navigation","first_status","ready"]){
  const value=trialPreparation({preparation:{...preparation,phase}});
  assert.equal(value?.phase,phase);assert.equal(value?.elapsedMs,1250);
  assert.equal(value?.source,"pi");assert.equal(value?.fresh,true);
 }
 const state={preparation:{...preparation,phase:"ready",state:"completed"}};
 assert.equal(trialPreparation(state)?.state,"completed");
 assert.equal(decisionView(state,permissionDetector).vision,"none");
 assert.equal(motionPermission(state,{drive:false},permissionPolicy,permissionDetector,permissionStatus()).venom,false);
});
test("fresh Pi preparation replaces previous per-tab errors in both browsers",()=>{
 const failed={...preparation,state:"failed",phase:"arm_ack",error:"navigation_trial_arm_unconfirmed",elapsed_ms:4300};
 const previous={...failed,request_id:"older-request",observed_at:preparation.observed_at-10};
 const chrome=trialPreparation({preparation:failed},preparationError(previous));
 const embedded=trialPreparation({preparation:{...failed}});
 assert.deepEqual(chrome,embedded);
 assert.equal(chrome?.error,"navigation_trial_arm_unconfirmed");
 assert.equal(chrome?.elapsedMs,4300);
 const completed={...preparation,request_id:"new-request",phase:"ready",state:"completed",observed_at:preparation.observed_at+10};
 assert.equal(trialPreparation({preparation:completed},preparationError(failed))?.state,"completed");
 assert.equal(trialPreparation({preparation:completed},preparationError(failed))?.source,"pi");
});
test("request details expose exact failure stage until a newer Pi poll arrives",()=>{
 const failed={...preparation,phase:"first_status",state:"failed",error:{code:"navigation_trial_stopped"},elapsed_ms:4700};
 const fallback=trialPreparation({},preparationError(failed));
 assert.equal(fallback?.phase,"first_status");assert.equal(fallback?.error,"navigation_trial_stopped");
 assert.equal(fallback?.source,"request");assert.equal(fallback?.elapsedMs,4700);
 const previous={...preparation,request_id:"previous",observed_at:preparation.observed_at-5};
 assert.equal(trialPreparation({preparation:previous},preparationError(failed))?.requestId,preparation.request_id);
 assert.equal(trialPreparation({preparation:{...failed}},preparationError(failed))?.source,"pi");
});
test("old Pi and invalid preparation snapshots keep the existing UI compatible",()=>{
 assert.equal(trialPreparation({running:false}),null);
 for(const patch of [{request_id:""},{state:"running"},{observed_at:null},{observed_at:NaN}]){
  assert.equal(trialPreparation({preparation:{...preparation,...patch}}),null);
 }
 for(const elapsed_ms of [-1,NaN,Infinity,undefined]){
  assert.equal(trialPreparation({preparation:{...preparation,elapsed_ms}})?.elapsedMs,null);
 }
 assert.equal(trialPreparation({preparation:{...preparation,fresh:false}})?.fresh,false);
 assert.equal(trialPreparation({preparation:{...preparation,phase:"future_stage"}})?.phase,"unknown");
 assert.equal(trialPreparation({}, {data:{detail:{code:"start_failed"}}}),null);
});
test("all preparation stages states and sources are translated in PL EN DE",()=>{
 const keys=["trialPreparation.state.preparing","trialPreparation.state.failed","trialPreparation.state.completed","trialPreparationElapsed","trialPreparationSource","trialPreparationResponse","trialPreparationStale",...["admission","camera_reservation","stop_old_producer","configure","authority_handoff","arm_ack","start_navigation","first_status","ready","unknown"].map(phase=>"trialPreparation.phase."+phase)];
 for(const language of ["pl","en","de"] as const){
  for(const key of keys)assert.notEqual(translate(language,key,{seconds:"1.2"}),key);
  assert.ok(translate(language,"trialPreparationElapsed",{seconds:"1.2"}).includes("1.2"));
  assert.ok(!translate(language,"trialStarting").includes("GO"));
 }
});


test("stale preparation is historical and unknown Pi errors never become translation keys",()=>{
 const current=trialPreparation({preparation});
 assert.ok(current);
 assert.equal(trialPreparationTitle(current),"trialPreparation.state.preparing");
 const historical=trialPreparation({preparation:{...preparation,fresh:false}});
 assert.ok(historical);
 assert.equal(trialPreparationTitle(historical),"trialPreparationHistorical");
 assert.equal(trialPreparationErrorKey({...current,error:"navigation_trial_prepare_timeout"}),"trial.error.navigation_trial_prepare_timeout");
 for(const error of ["unrecognized_code","__proto__","<script>invalid</script>","trial.error.navigation_trial_prepare_timeout"]){
  assert.equal(trialPreparationErrorKey({...current,error}),"requestFailed");
 }
 for(const language of ["pl","en","de"] as const){
  assert.notEqual(translate(language,"trialPreparationHistorical"),"trialPreparationHistorical");
 }
});


const failedPreparationPacket={
 request_id:"0fb2c2e3-864a-4c25-88d0-f56eb08b2bbc",
 operator_request_id:"trial-7d4514424976331315f72fe9e7d4aa03-start",
 phase:"start_navigation",state:"failed",elapsed_ms:5835.28,
 observed_at:1792061152.396262,error:"navigation_trial_prepare_timeout",fresh:true,
};
const stoppedProductionRun={
 mode:"drive",running:false,stale:true,latched:true,
 run_id:"0cb83d21740e44a188862a8856600821",
 operator_request_id:"trial-7d4514424976331315f72fe9e7d4aa03-start",
 observed_at:1792061149.3272917,stop_reason:"decision_timeout",
 decision:{action:"go",vision_action:"go",sequence:5,clear_count:4},
};
test("real stopped-run plus delayed prepare timeout becomes one correlated outcome",()=>{
 const state={...stoppedProductionRun,preparation:failedPreparationPacket,last_trial:stoppedProductionRun};
 const error={data:{detail:{code:"navigation_trial_prepare_timeout",details:{preparation:failedPreparationPacket}}}};
 const value=trialDiagnostics(state,error);
 assert.equal(value.outcome?.run_id,stoppedProductionRun.run_id);
 assert.equal(value.outcome?.stop_reason,"decision_timeout");
 assert.equal(value.preparation?.elapsedMs,5835.28);
 assert.equal(value.combined,true);
 assert.equal(value.requestErrorCovered,true);
 assert.equal(decisionView(state,permissionDetector).vision,"none");
 const otherBrowser=trialDiagnostics({...state});
 assert.equal(otherBrowser.combined,true);
 assert.deepEqual(otherBrowser.outcome,value.outcome);
});
test("Pi started=true terminal response remains an outcome before the next status poll",()=>{
 const details={started:true,status:stoppedProductionRun,preparation:failedPreparationPacket};
 const error={data:{detail:{code:"navigation_trial_stopped",details}}};
 const value=trialDiagnostics({},error);
 assert.equal(value.outcome?.stop_reason,"decision_timeout");
 assert.equal(value.combined,true);assert.equal(value.requestErrorCovered,true);
 assert.equal(value.preparation?.source,"request");
});
test("separate requests are never folded into a single stopped trial by time alone",()=>{
 const another={...failedPreparationPacket,request_id:"another-request",operator_request_id:"trial-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-start",observed_at:failedPreparationPacket.observed_at+10};
 const value=trialDiagnostics({last_trial:stoppedProductionRun,preparation:another});
 assert.equal(value.combined,false);
 assert.equal(value.outcome?.run_id,stoppedProductionRun.run_id);
 assert.equal(value.preparation?.requestId,"another-request");
 assert.equal(value.requestErrorCovered,false);
 const malformed=trialDiagnostics({last_trial:stoppedProductionRun,preparation:{...failedPreparationPacket,operator_request_id:""}});
 assert.equal(malformed.combined,false);
});
test("an unrelated HTTP 502 is never masked as a successful or confirmed trial",()=>{
 const error={data:{detail:{code:"http_502",details:{operation:{status:"unknown",confirmed:false}}}}};
 const value=trialDiagnostics({last_trial:stoppedProductionRun,preparation:failedPreparationPacket},error);
 assert.equal(value.combined,true);
 assert.equal(value.requestErrorCovered,false);
 assert.equal(value.outcome?.running,false);
});
test("a newer active run does not inherit a stopped result from an older request",()=>{
 const value=trialDiagnostics({mode:"drive",running:true,stale:false,run_id:"new-run",last_trial:stoppedProductionRun,preparation:{...failedPreparationPacket,state:"preparing"}});
 assert.equal(value.outcome,null);
 assert.equal(value.combined,false);
 assert.equal(value.requestErrorCovered,false);
});


test("unknown historical stop codes have a bounded module-owned display key",()=>{
 assert.equal(trialStopReasonKey("decision_timeout"),"vision.reason.decision_timeout");
 for(const value of [null,{},"<script>","unknown_future_reason"]){
  assert.equal(trialStopReasonKey(value),"trialStopped");
 }
});


const navigationSource = ts.createSourceFile(
 "navigation-loop.tsx",
 fs.readFileSync(new URL("../../web-next/components/navigation-loop.tsx", import.meta.url), "utf8"),
 ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);
function matchingNodes(root:ts.Node, match:(node:ts.Node)=>boolean):ts.Node[] {
 const found:ts.Node[]=[];
 const visit=(node:ts.Node)=>{if(match(node))found.push(node);ts.forEachChild(node,visit);};
 visit(root);return found;
}
function jsxAttribute(node:ts.Node, name:string):string|undefined {
 const opening=ts.isJsxElement(node)?node.openingElement:ts.isJsxSelfClosingElement(node)?node:null;
 const attribute=opening?.attributes.properties.find(value=>ts.isJsxAttribute(value)&&value.name.getText(navigationSource)===name);
 return attribute&&ts.isJsxAttribute(attribute)?attribute.initializer?.getText(navigationSource):undefined;
}
function jsxByTestId(id:string):ts.Node {
 const result=matchingNodes(navigationSource,node=>jsxAttribute(node,"data-testid")===JSON.stringify(id));
 assert.equal(result.length,1,id);return result[0];
}
test("forward driving is the main action; observation is a closed optional section",()=>{
 const start=jsxByTestId("loop-start"),stop=jsxByTestId("loop-stop"),preview=jsxByTestId("loop-preview");
 let toolbar:ts.Node|undefined=start.parent;
 while(toolbar&&jsxAttribute(toolbar,"className")!=="{styles.loopActions}")toolbar=toolbar.parent;
 assert.ok(toolbar);
 assert.ok(matchingNodes(toolbar,node=>node===stop).length);
 assert.equal(matchingNodes(toolbar,node=>node===preview).length,0);
 const optional=jsxByTestId("navigation-preparation");
 assert.ok(ts.isJsxElement(optional)&&optional.openingElement.tagName.getText(navigationSource)==="details");
 assert.equal(jsxAttribute(optional,"open"),undefined);
 assert.ok(matchingNodes(optional,node=>node===preview).length);
 assert.equal(jsxAttribute(start,"tone"),'"primary"');
 assert.ok(jsxByTestId("trial-start-help"));
});
test("the deliberate drive handler chooses warm resume or prepared trial without observation or ARM",()=>{
 const handler=matchingNodes(navigationSource,node=>ts.isVariableDeclaration(node)&&node.name.getText(navigationSource)==="runTrial")[0];
 assert.ok(handler);
 const calls=matchingNodes(handler,ts.isCallExpression).map(node=>(node as ts.CallExpression).expression.getText(navigationSource));
 assert.equal(calls.filter(call=>call==="client.startNavigationTrial").length,1);
 for(const prerequisite of ["ensureAnalysis","observePreview","client.mutate"])assert.equal(calls.includes(prerequisite),false,prerequisite);
 assert.ok(handler.getText(navigationSource).includes('action:"navigation_resume"'));
 assert.equal(/action:"arm"|freshPolicyFrame|imageDecision|vision_action|clear_count/.test(handler.getText(navigationSource)),false);
 const effects=matchingNodes(navigationSource,node=>ts.isCallExpression(node)&&node.expression.getText(navigationSource)==="useEffect");
 for(const effect of effects){
  const actions=matchingNodes(effect,ts.isCallExpression).map(node=>(node as ts.CallExpression).expression.getText(navigationSource));
  assert.equal(actions.includes("runTrial"),false);
  assert.equal(actions.includes("client.startNavigationTrial"),false);
 }
});
test("driving preparation needs its own runtime, never an already running observation or preview",()=>{
 const policy={mode:"observe",verified:true,calibration_id:"accepted",model_sha256:"a".repeat(64)};
 const detector={configured:true,loaded:false,sha256:""};
 const status={control:{safety:{state:"DISARMED",owner:null,generation:"current",stale:false,ready:true,capabilities:{move:{available:true}},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true}},errors:{}} as unknown as RiderStatus;
 assert.equal(trialBlock(policy,{observe:false,drive:true},detector,true,false,status),"");
 assert.equal(trialBlock(policy,{observe:true,drive:false},detector,true,false,status),"loopRuntimeMissing");
 assert.equal(trialBlock(policy,{drive:true},detector,true,true,status),"loopUnsaved");
 for(const language of ["pl","en","de"] as const){
  for(const key of ["trialStartHelp","observationOptional","observationOptionalHelp"])assert.notEqual(translate(language,key),key);
 }
 assert.equal(translate("pl","trialRun"),"Jedź prosto · 10 s");
});


test("confirmed ARM-window completion is stopped and neutral; faults remain errors",()=>{
 const completed={mode:"drive",running:false,latched:true,stale:false,stop_reason:"trial_completed"};
 assert.equal(loopPhase(true,completed,{drive:true,observe:false}),"stopped");
 assert.equal(loopPhase(true,completed,{drive:false,observe:false}),"stopped");
 assert.equal(loopPhase(false,completed,{drive:true}),"offline");
 assert.equal(trialOutcomeTone("trial_completed"),"neutral");
 assert.equal(trialStopReasonKey("trial_completed"),"vision.reason.trial_completed");
 for(const reason of ["motion_submission_failed","decision_timeout","navigation_motion_rejected","stale_generation","unknown"]){
  assert.equal(loopPhase(true,{...completed,stop_reason:reason},{drive:true}),"fault",reason);
  assert.equal(trialOutcomeTone(reason),"error",reason);
 }
 assert.notEqual(loopPhase(true,{...completed,running:true},{drive:true}),"stopped");
 const outcome=trialDiagnostics({mode:"observe",running:true,stale:false,last_trial:{...completed,run_id:"completed-run",observed_at:100}});
 assert.equal(outcome.outcome?.stop_reason,"trial_completed");
 const panel=jsxByTestId("trial-stop-reason");
 assert.match(jsxAttribute(panel,"className")??"",/trialOutcomeTone\(displayedStopReason\)==="neutral"\?styles\.hint:styles\.loopError/);
 for(const language of ["pl","en","de"] as const){
  const message=translate(language,"vision.reason.trial_completed");
  assert.notEqual(message,"vision.reason.trial_completed");
  assert.equal(message.includes("10"),false);
  assert.ok(message.includes("ARM"));
 }
});


test("preview transition is neutral and model or stale-frame errors remain visible outside it",()=>{
 assert.equal(previewNotice(true,"venom",false,true),"visionPreviewRestarting");
 assert.equal(previewNotice(false,"venom",true,true),"visionPreviewRestarting");
 assert.equal(previewNotice(true,"venom",false,false),"previewPausedNotice");
 assert.equal(previewNotice(true,"venom",true,false),"previewDelayedNotice");
 assert.equal(previewNotice(true,"raw",true,true),"previewDelayedNotice");
 assert.equal(previewNotice(false,"raw",true,true),null);
 for(const language of ["pl","en","de"] as const)assert.notEqual(translate(language,"visionPreviewRestarting"),"visionPreviewRestarting");
});
test("recovery errors clear only after a confirmed fresh observation",()=>{
 const state={mode:"observe",running:true,stale:false},goals={observe:true};
 assert.equal(freshObservation(state,goals),true);
 for(const row of [{...state,mode:"drive"},{...state,running:false},{...state,stale:true},{...state,stale:undefined},{}]){
  assert.equal(freshObservation(row,goals),false);
 }
 assert.equal(freshObservation(state,{observe:false}),false);
 assert.equal(freshObservation(state,{}),false);
});
test("recovery brackets preview polling and late processed GETs cannot publish old GO",()=>{
 const effects=matchingNodes(navigationSource,node=>ts.isCallExpression(node)&&node.expression.getText(navigationSource)==="useEffect");
 const recovery=effects.find(node=>node.getText(navigationSource).includes('client.mutate("/goal/s14_venom_navigation"'));
 assert.ok(recovery);
 const source=recovery.getText(navigationSource);
 assert.ok(source.indexOf("setCameraTransition(true)")<source.indexOf('client.mutate("/goal/s14_venom_navigation"'));
 assert.ok(source.indexOf('client.mutate("/goal/s15_venom_perception"')<source.indexOf("await readState()"));
 assert.ok(source.indexOf(".finally(")<source.indexOf("setCameraTransition(false)"));
 const client=fs.readFileSync(new URL("../../web-next/lib/use-rider.ts",import.meta.url),"utf8");
 const fetchStart=client.indexOf("const fetchFrame =");
 const dispatch=client.indexOf("const response = await fetch",fetchStart);
 assert.ok(client.indexOf("if (!currentPreview()) return;",fetchStart)<dispatch);
 assert.ok(client.indexOf("!currentPreview()",dispatch)>dispatch);
 const decodedStart=client.indexOf("const applyDecodedFrame=");
 const decode=client.indexOf("await decodePreview",decodedStart);
 const decodedGuard=client.indexOf("!currentPreview()",decode);
 assert.ok(decodedStart>=0&&decode>decodedStart&&decodedGuard>decode&&decodedGuard<fetchStart);
 assert.ok(client.indexOf("await applyDecodedFrame(response",dispatch)>dispatch);
 assert.ok(client.includes('cameraStream === "venom" && cameraTransition'));
 assert.ok(client.includes('if (!cameraStream || !["control", "vision", "samples"].includes(section) || processedTransition || nativeStreaming || pipelineBusy || pipelineStopped) return;'));
 assert.ok(client.includes("stale: true, knownFreshness: false"));
});


test("v2 turn is shown as a maneuver while the forward image remains STOP",()=>{
  for(const side of ["left","right"]){
    const state={...permissionLoop,decision:{...permissionLoop.decision,protocol_version:2,action:"turn_"+side,state:"obstacle",vision_action:"stop",sectors:{[side]:{state:"clear",coverage_complete:true}}}};
    const policy={...permissionPolicy,avoidance:{enabled:true,sweep_verified:true}};
    const result=motionPermission(state,{drive:true},policy,permissionDetector,permissionStatus());
    assert.equal(result.venom,true);
    assert.equal(result.reason,side==="left"?"permissionTurnLeft":"permissionTurnRight");
    assert.equal(decisionView(state,permissionDetector).reply,"turn_"+side);
    assert.equal(decisionView(state,permissionDetector).vision,"stop");
    assert.equal(executionHeadline(state,{drive:true},permissionStatus(),result),side==="left"?"permissionVenomTurnLeft":"permissionVenomTurnRight");
    assert.equal(motionPermission({...state,stale:true},{drive:true},policy,permissionDetector,permissionStatus()).venom,false);
    assert.equal(motionPermission(state,{drive:true},permissionPolicy,permissionDetector,permissionStatus()).venom,false);
  }
});


test("v3 steering needs clear near guard and connecting path in addition to side",()=>{
 const state={...permissionLoop,decision:{...permissionLoop.decision,protocol_version:3,action:"steer_right",state:"obstacle",vision_action:"stop",sectors:{right:{state:"clear",coverage_complete:true}},paths:{right:{state:"clear",coverage_complete:true},guard:{state:"clear",coverage_complete:true}}}};
 const policy={...permissionPolicy,avoidance:{enabled:true,sweep_verified:true,protocol_version:3}};
 const permission=motionPermission(state,{drive:true},policy,permissionDetector,permissionStatus());
 assert.equal(permission.venom,true);
 assert.equal(decisionView(state,permissionDetector).reply,"steer_right");
 assert.equal(executionHeadline(state,{drive:true},permissionStatus(),permission),"permissionVenomSteerRight");
 for(const field of ["right","guard"]){
   const blocked={...state,decision:{...state.decision,paths:{...state.decision.paths,[field]:{state:"obstacle",coverage_complete:true}}}};
   assert.equal(motionPermission(blocked,{drive:true},policy,permissionDetector,permissionStatus()).venom,false);
 }
});

test("ground mode requires configured projection and host support before a trial",()=>{
 const geometry={mode:"ground",fx:.5,fy:.5,radius_ratio:.35};
 const policy={...permissionPolicy,avoidance:{enabled:true,protocol_version:3,geometry}};
 const detector={...permissionDetector,configured:true,ground_geometry_supported:true};
 assert.equal(trialBlock(policy,{drive:true},detector,true,false,permissionStatus()),"");
 assert.equal(trialBlock(policy,{drive:true},{...detector,ground_geometry_supported:false},true,false,permissionStatus()),"geometryHostUpdate");
 for(const key of ["fx","fy","radius_ratio"]){
  for(const value of [null,NaN,0,undefined]){
   const invalid={...policy,avoidance:{...policy.avoidance,geometry:{...geometry,[key]:value}}};
   assert.equal(trialBlock(invalid,{drive:true},detector,true,false,permissionStatus()),"geometryCalibrationMissing");
  }
 }
 const changed={...policy,avoidance:{...policy.avoidance,geometry:{...geometry,radius_ratio:.4}}};
 assert.equal(loopSettingsValues(policy,changed).verified,false);
});
test("ground permission matches Pi evidence instead of displaying a bare GO as valid",()=>{
 const policy={...permissionPolicy,avoidance:{enabled:true,geometry:{mode:"ground"}}};
 const decision={...permissionLoop.decision,geometry:"ground_capsule",ground_support:.8,ground_reference:{normal:[0,0.6,.8],height:1}};
 const state={...permissionLoop,decision};
 assert.equal(motionPermission(state,{drive:true},policy,permissionDetector,permissionStatus()).venom,true);
 for(const patch of [{ground_support:.1},{ground_reference:{}},{ground_reference:{normal:[0,0,3],height:1}},{geometry:"image"}]){
  assert.equal(motionPermission({...state,decision:{...decision,...patch}},{drive:true},policy,permissionDetector,permissionStatus()).venom,false);
 }
});

test("editing a previously driving policy saves an unaccepted observation policy, not invalid drive authority",()=>{
 const policy={revision:53,mode:"drive",verified:true,calibration_id:"operator-test",model_sha256:"a".repeat(64),speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],avoidance:{enabled:false,protocol_version:3,sweep_verified:false,geometry:{mode:"ground",fx:null,fy:null,radius_ratio:null}}};
 for(const patch of [{speed:.09},{avoidance:{...policy.avoidance,enabled:true}},{avoidance:{...policy.avoidance,geometry:{mode:"image"}}}]){
  const values=loopSettingsValues(policy,{...policy,...patch});
  assert.equal(values.mode,"observe");assert.equal(values.verified,false);
  assert.equal(values.calibration_id,"");assert.equal(values.model_sha256,"");
  assert.equal(values.revision,undefined);
 }
 assert.equal(loopSettingsValues(policy,policy).mode,"drive");
 assert.equal(policy.mode,"drive");assert.equal(policy.verified,true);
});

test("native MJPEG age from the validated Pi status anchor permits the same visible calibration acceptance as snapshots",()=>{
 const state={run_id:"active-observation"},policy={revision:54};
 const frame={decoded:true,stale:false,knownFreshness:true,id:"camera-session-5515",fileAt:"",visionState:"clear",visionPolicy:"",ageMs:306.25,receivedAt:performance.now(),ageSource:"pi-status-anchor-plus-host-elapsed",analysis:{run_id:state.run_id,config_revision:54,state:"clear",vision_action:"go"}};
 assert.equal(freshPolicyFrame(frame,"venom",state,policy),true);
 assert.equal(freshPolicyFrame({...frame,ageSource:"pi-reported-plus-host-elapsed"},"venom",state,policy),true);
 for(const ageSource of ["host-received","unknown",undefined])assert.equal(freshPolicyFrame({...frame,ageSource},"venom",state,policy),false);
 for(const patch of [{stale:true},{knownFreshness:false},{decoded:false},{ageMs:3001},{analysis:{...frame.analysis,config_revision:53}},{analysis:{...frame.analysis,run_id:"previous-observation"}},{analysis:{...frame.analysis,vision_action:"stop"}}])assert.equal(freshPolicyFrame({...frame,...patch},"venom",state,policy),false);
 assert.equal(freshPolicyFrame(frame,"raw",state,policy),false);
});

test("explicit journey acceptance records zone and swept clearance atomically; legacy acceptance never grants sweep",()=>{
 const policy={mode:"observe",speed:.1,max_motion_ms:150,verified:false,avoidance:{enabled:true,protocol_version:3,sweep_verified:false}};
 const detector={sha256:"a".repeat(64)};
 const accepted=calibrationAcceptance(policy,detector,"operator-explicit",true);
 assert.equal(accepted.verified,true);assert.equal(accepted.calibration_id,"operator-explicit");
 assert.equal((accepted.avoidance as Record<string,unknown>).sweep_verified,true);
 assert.equal((calibrationAcceptance(policy,detector,"operator-legacy").avoidance as Record<string,unknown>).sweep_verified,false);
 assert.equal(policy.avoidance.sweep_verified,false);
 for(const avoidance of [{enabled:false,protocol_version:3},{enabled:true,protocol_version:2}])assert.throws(()=>calibrationAcceptance({...policy,avoidance},detector,"operator-explicit",true));
});

test("Pi presentation metadata is never submitted by save, zone change or acceptance",()=>{
 const policy={revision:134,protocol_version:1,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],perception:{engine:"depth_plane"},avoidance:{enabled:true,protocol_version:3,sweep_verified:false},behavior:{version:1,profile_id:"balanced",profile_revision:1},verified:false,calibration_id:"",model_sha256:"",
 behavior_catalog:{supported:true},model_tuning_supported:true,perception_tuning:{version:1},future_read_only_metadata:{diagnostic:true}};
 const allowed=["mode","speed","max_motion_ms","decision_budget_ms","corridor","perception","avoidance","behavior","verified","calibration_id","model_sha256"].sort();
 for(const value of [loopSettingsValues(policy,policy),stoppingZoneValues(policy,"far"),calibrationAcceptance(policy,{sha256:"a".repeat(64)},"operator-test",true)]){
  assert.ok(value);
  assert.deepEqual(Object.keys(value).sort(),allowed);
  assert.deepEqual(value.behavior,policy.behavior);
 }
 const accepted=calibrationAcceptance(policy,{sha256:"a".repeat(64)},"operator-test",true);
 assert.equal(accepted.verified,true);
 assert.equal((accepted.avoidance as {sweep_verified:boolean}).sweep_verified,true);
});

test("warm drive service after disarm does not lock saving, but motion and unknown state do",()=>{
 const status={connection:{status:"online"},errors:{},control:{safety:{state:"DISARMED",moving:false,operator_control:{state:"idle"}}}} as unknown as RiderStatus;
 assert.equal(navigationSettingsDriveBlocked(status,true,{drive:true}),false);
 assert.equal(navigationSettingsDriveBlocked(status,false,{drive:true}),true);
 assert.equal(navigationSettingsDriveBlocked(null,true,{drive:true}),true);
 for(const change of [{state:"ARMED"},{moving:true},{stale:true},{operator_control:{state:"qualifying"}}]){
  const next={...status,control:{...status.control,safety:{...(status.control!.safety as object),...change}}} as RiderStatus;
  assert.equal(navigationSettingsDriveBlocked(next,true,{drive:true}),true);
 }
 assert.equal(navigationSettingsDriveBlocked(status,true,{drive:true,preparation:{active:true}}),true);
});

test("pulse field range follows Pi capability and does not persist presentation metadata",()=>{
 assert.equal(navigationPulseLimit(null,true),150);
 assert.equal(navigationPulseLimit({avoidance:{enabled:true}}),150);
 assert.equal(navigationPulseLimit({}),200);
 assert.equal(navigationPulseLimit({motion_limits:{version:1,max_motion_ms:250}},true),250);
 assert.equal(navigationPulseLimit({motion_limits:{version:1,max_motion_ms:999}},true),999);
 assert.equal(navigationPulseLimit({motion_limits:{version:2,max_motion_ms:250}},true),150);
 const policy={speed:.1,max_motion_ms:150,motion_limits:{version:1,max_motion_ms:250}};
 assert.equal("motion_limits" in loopSettingsValues(policy,{...policy,max_motion_ms:250}),false);
});

test("late navigation reads cannot roll back newer state or policy revisions",()=>{
 assert.equal(navigationReadCurrent(1,2,10,10),false);
 assert.equal(navigationReadCurrent(3,2,9,10),false);
 assert.equal(navigationReadCurrent(3,2,10,10),true);
 assert.equal(navigationReadCurrent(4,2,11,10),true);
});
test("successful Start remains successful when only the following state GET fails",async()=>{
 const error=new Error("read_failed");
 assert.equal(await refreshStartedNavigation(async()=>{throw error;},()=>true),error);
 assert.equal(await refreshStartedNavigation(async()=>{},()=>true),null);
});
test("STOP or session changes during a successful or failed post-Start GET still cancel",async()=>{
 for(const failed of [true,false]){
  let current=true;
  await assert.rejects(refreshStartedNavigation(async()=>{current=false;if(failed)throw new Error("read_failed");},()=>current),/navigation_trial_cancelled/);
 }
});


test("diagnostic reasons accept protocol strings and never stringify malformed metadata",()=>{
 const malformed=[{},[],["vision_clear"],17,false];
 for(const value of malformed){
  const decision={state:"unknown",reason:value,motion_reason:value};
  const current=decisionView({running:true,stale:false,decision},{});
  assert.equal(current.imageReason,"waiting_for_decision");
  assert.equal(current.reason,"waiting_for_decision");
  assert.equal(decisionView({running:false,stop_reason:value,decision},{}).reason,"waiting_for_decision");
 }
 const exact=decisionView({running:true,decision:{state:"unknown",reason:"vision_low_confidence",motion_reason:"observe_only"}},{});
 assert.equal(exact.imageReason,"vision_low_confidence");
 assert.equal(exact.reason,"observe_only");
 const empty=decisionView({running:true,decision:{state:"unknown",reason:"",motion_reason:""}},{});
 assert.equal(empty.imageReason,"");
 assert.equal(empty.reason,"");
});


for(const action of ["go","turn_left","steer_left"]){
 test(`only the string enum ${action} can supply a navigation reply or permission`,()=>{
  const decision=action==="go"?{...permissionLoop.decision,action}:{
   ...permissionLoop.decision,action,protocol_version:action==="steer_left"?3:2,state:"obstacle",vision_action:"stop",
   sectors:{left:{state:"clear",coverage_complete:true}},
   paths:{left:{state:"clear",coverage_complete:true},guard:{state:"clear",coverage_complete:true}},
  };
  const state={...permissionLoop,decision};
  const policy={...permissionPolicy,avoidance:{enabled:true,sweep_verified:true,protocol_version:3}};
  assert.equal(decisionView(state,permissionDetector).reply,action);
  assert.equal(motionPermission(state,{drive:true},policy,permissionDetector,permissionStatus()).venom,true);
  for(const malformed of [[action],{action},{toString:()=>action},null,17,true]){
   const invalid={...state,decision:{...decision,action:malformed}};
   assert.equal(decisionView(invalid,permissionDetector).reply,"none");
   assert.equal(motionPermission(invalid,{drive:true},policy,permissionDetector,permissionStatus()).venom,false);
  }
 });
}
