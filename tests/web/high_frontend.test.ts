import assert from "node:assert/strict";
import {test} from "node:test";
import {riderAnalysisSettings} from "../../web-next/lib/analysis-profile";
import {applyOperatorSettings} from "../../web-next/lib/apply-operator-settings";

/** Serialized enum values must never be accepted through array string coercion. */
test("offline settings reject a semantic surface array while retaining a valid surface",()=>{
 const policy={revision:1,corridor:[.35,.8,.65,.95],perception:{semantic_models:{"segformer-b0-ade20k":{version:1,surface:"floor_only"}}}};
 assert.equal((riderAnalysisSettings(policy).perception.semantic_models as Record<string,{surface:string}>)["segformer-b0-ade20k"].surface,"floor_only");
 for(const surface of [["floor_only"],{},["floor_rug"]]){
  assert.throws(()=>riderAnalysisSettings({...policy,perception:{semantic_models:{"segformer-b0-ade20k":{version:1,surface}}}}),/analysis_rider_settings_invalid/);
 }
});

/** Invalid initial authority must fail before stopping any background goal. */
test("settings reject a safety state array before any goal mutation",async()=>{
 const mutations:string[]=[];
 const status={control:{safety:{generation:"generation",state:["DISARMED"],moving:false,stale:false}},errors:{}};
 await assert.rejects(applyOperatorSettings({}, {
  read:async path=>path==="/navigation/config"?{config:{revision:1},goals:{observe:true}}:status,
  mutate:async path=>{mutations.push(path);return {};},stop:async()=>{},
  session:()=>"session",epoch:()=>1,generation:()=>"generation",current:()=>true,
  onNavigation:()=>{},onManual:()=>{},
 }),/stale_control/);
 assert.deepEqual(mutations,[]);
});
