import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { test } from "node:test";
import { calibrationAcceptance, calibrationMatches, completedTrial, executionHeadline, freshPolicyFrame, previewNotice, calibrationScope, decisionView, freshAnalysisFrame, loopBlock, loopFailure, loopPhase, loopSettingsValues, motionPermission, stoppingZone, stoppingZoneValues, trialBlock, trialPreparation, trialPreparationTitle, trialPreparationErrorKey, trialDiagnostics, trialStopReasonKey, trialOutcomeTone } from "../../web-next/lib/loop-controls";
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
 const optional=jsxByTestId("loop-observation");
 assert.ok(ts.isJsxElement(optional)&&optional.openingElement.tagName.getText(navigationSource)==="details");
 assert.equal(jsxAttribute(optional,"open"),undefined);
 assert.ok(matchingNodes(optional,node=>node===preview).length);
 assert.equal(jsxAttribute(start,"tone"),'"primary"');
 assert.ok(jsxByTestId("trial-start-help"));
});
test("the deliberate drive handler calls one prepared trial and never races observation or ARM",()=>{
 const handler=matchingNodes(navigationSource,node=>ts.isVariableDeclaration(node)&&node.name.getText(navigationSource)==="runTrial")[0];
 assert.ok(handler);
 const calls=matchingNodes(handler,ts.isCallExpression).map(node=>(node as ts.CallExpression).expression.getText(navigationSource));
 assert.equal(calls.filter(call=>call==="client.startNavigationTrial").length,1);
 for(const prerequisite of ["ensureAnalysis","observePreview","client.command","client.mutate"])assert.equal(calls.includes(prerequisite),false,prerequisite);
 assert.equal(/freshPolicyFrame|imageDecision|vision_action|clear_count/.test(handler.getText(navigationSource)),false);
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
 assert.equal(jsxAttribute(panel,"className"),'{trialOutcomeTone(displayedStopReason)==="neutral"?styles.hint:styles.loopError}');
 for(const language of ["pl","en","de"] as const){
  const message=translate(language,"vision.reason.trial_completed");
  assert.notEqual(message,"vision.reason.trial_completed");
  assert.equal(message.includes("10"),false);
  assert.ok(message.includes("ARM"));
 }
});
