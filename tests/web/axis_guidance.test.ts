import assert from "node:assert/strict";
import {test} from "node:test";
import {collisionDraft,collisionNavigationValid,guidanceReady,validCollision} from "../../web-next/lib/collision-geometry";
import {motionPermission,trialBlock} from "../../web-next/lib/loop-controls";
import {journeyView} from "../../web-next/lib/journey";
import {readGeometry} from "../../web-next/lib/overlay-geometry";
import type {JsonRecord,RiderStatus} from "../../web-next/lib/model";

const roi=[.35,.8,.65,.95];
function policy(){return {verified:true,calibration_id:"fixture",model_sha256:"a".repeat(64),corridor:roi,collision_geometry_supported:true,axis_guidance_supported:true,avoidance:{enabled:true,sweep_verified:true,protocol_version:3,geometry:{mode:"image",collision:collisionDraft(roi,true)}}};}
const detector={configured:true,sha256:"a".repeat(64),collision_geometry_v1:true,axis_guidance_v1:true};
const status={connection:{configured:true,status:"online"},errors:{},settings:{revision:1},control:{safety:{generation:"one",state:"ARMED",owner:"navigation",can_move:true,ready:true,capabilities:{move:{available:true}},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true,execution:{}}}} as unknown as RiderStatus;

test("saving an observation draft does not qualify an uncalibrated rotation",()=>{
 const p=policy(),g=p.avoidance.geometry.collision.guidance!;
 assert(validCollision(p.avoidance.geometry.collision,roi));
 assert(collisionNavigationValid(p,p));
 assert(!guidanceReady(p));
 assert.equal(trialBlock(p,{drive:true},detector,true,false,status),"guidance.rotationMissing");
 g.rotation_bounds=[.2,.88,.8,.95];
 assert(guidanceReady(p));
 assert.equal(trialBlock(p,{drive:true},detector,true,false,status),"");
 assert(!guidanceReady({...p,perception:{input_region:{bounds:[.35,.65,.65,.95]}}}));
 assert.equal(trialBlock(p,{drive:true},{...detector,axis_guidance_v1:false},true,false,status),"guidance.unsupported");
 assert(!collisionNavigationValid({...p,axis_guidance_supported:false},p));
 for(const patch of [{version:true},{motion_mode:"guess"},{center_deadband:NaN},{rotation_bounds:[.4,.9,.6,.95]}])assert(!validCollision({...p.avoidance.geometry.collision,guidance:{...g,...patch}},roi));
});

test("v3 rotation display requires a distinct rotation footprint and matching motion mode",()=>{
 const p=policy();p.avoidance.geometry.collision.guidance!.rotation_bounds=[.2,.88,.8,.95];
 const path={state:"clear",coverage_complete:true,geometry:"image_collision_corridor"};
 const decision:JsonRecord={protocol_version:3,state:"obstacle",vision_action:"stop",action:"turn_right",valid_for_ms:150,clear_count:3,geometry:"image_collision_corridor",collision_geometry:{version:1,sha256:"c".repeat(64)},guidance:{version:1,motion_mode:"rotate_then_drive"},paths:{center:{...path,state:"obstacle"},right:path,guard:path,rotation:path}};
 const check=(d:JsonRecord)=>motionPermission({running:true,mode:"drive",decision:d},{drive:true},p,detector,status).venom;
 assert.equal(check(decision),true);
 assert.equal(check({...decision,paths:{center:path,right:path,guard:path}}),false);
 assert.equal(check({...decision,action:"steer_right"}),false);
 assert.equal(check({...decision,guidance:{version:1,motion_mode:"auto"}}),false);
 assert.equal(journeyView({journey:{version:1,id:"x",active:true,state:"turn_right"}}).phase,"turn_right");
});

test("source overlay accepts bounded axis and independent rotation footprint",()=>{
 const shape={version:1,width:320,height:240,input_bounds:[0,0,320,240],paths:{rotation:[[64,211],[255,211],[255,227],[64,227],[64,211]]},axis:[[160,192],[160,227]]};
 assert(readGeometry(shape));
 assert.equal(readGeometry({...shape,axis:[[321,192],[160,227]]}),null);
});
