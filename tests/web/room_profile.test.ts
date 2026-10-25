import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {behaviorCatalog,behaviorDraft,behaviorWrite,editBehaviorParameter,validBehaviorDraft} from "../../web-next/lib/behavior";
import type {JsonRecord} from "../../web-next/lib/model";
const original=JSON.parse(readFileSync(new URL("../fixtures/policy-catalog-v2.json",import.meta.url),"utf8"));
function policy():JsonRecord{
 const catalog=structuredClone(original),fluid=catalog.profiles.find((p:JsonRecord)=>p.id==="fluid");
 catalog.profiles.push({...fluid,id:"room_fluid",parameters:{...fluid.parameters,object_tracking_enabled:true,object_policy_enabled:true,thin_span_fraction:1}});
 catalog.parameter_schema.object_policy_enabled={kind:"bool",min:0,max:1,step:1,group:"objects"};
 return {revision:9,behavior:{...catalog.active},behavior_catalog:catalog};
}
test("room preset is explicit and old selection stays active until CAS apply",()=>{
 const p=policy(),catalog=behaviorCatalog(p);
 assert.equal(catalog.supported,true);assert.equal(catalog.active.profile_id,"fluid");
 const draft=behaviorDraft(p,"room_fluid","session")!;
 assert.equal(validBehaviorDraft(draft,p),true);
 assert.deepEqual(behaviorWrite(draft,p,"session"),{revision:9,values:{behavior:{version:1,profile_id:"room_fluid",profile_revision:2}}});
 const invalid=editBehaviorParameter(draft,p,"object_tracking_enabled",false);
 assert.equal(behaviorWrite(invalid,p,"session"),null);
 const custom=editBehaviorParameter(invalid,p,"object_policy_enabled",false);
 assert.equal(validBehaviorDraft(custom,p),true);
 assert.equal(behaviorWrite(custom,p,"different-session"),null);
});
test("old presets cannot acquire room-only controls even with a newer catalog",()=>{
 const p=policy(),draft=behaviorDraft(p,"fluid","session")!;
 assert.equal(behaviorWrite(editBehaviorParameter(draft,p,"object_policy_enabled",true),p,"session"),null);
});
