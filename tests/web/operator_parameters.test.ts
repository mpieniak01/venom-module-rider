import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { behaviorCatalog, behaviorDraft, behaviorEditDraft, behaviorWrite, editBehaviorParameter, sameBehavior, validBehaviorDraft, behaviorParameterValues } from "../../web-next/lib/behavior";
import type { JsonRecord } from "../../web-next/lib/model";
const catalog = JSON.parse(readFileSync(new URL("../fixtures/policy-catalog-v2.json", import.meta.url), "utf8"));
function fixture() { return { revision: 9, behavior: { ...catalog.active }, behavior_catalog: structuredClone(catalog) }; }
test("old active revision remains readable with newer preset available", () => {
 const p=fixture();p.behavior.profile_revision=1;p.behavior_catalog.active.profile_revision=1;
 assert.equal(behaviorCatalog(p).supported,true);assert.equal(behaviorCatalog(p).active.profile_revision,1);
 assert.equal(behaviorEditDraft(p,"one")?.selection.profile_revision,2);
});
test("custom settings retain base identity, store only differences, restore preset", () => {
 const p=fixture(), base=behaviorEditDraft(p,"one")!;
 const changed=editBehaviorParameter(base,p,"obstacle_support_frames",3);
 assert.equal(validBehaviorDraft(changed,p),true);
 assert.deepEqual(behaviorWrite(changed,p,"one"),{revision:9,values:{behavior:{version:1,profile_id:"fluid",profile_revision:2,overrides:{obstacle_support_frames:3}}}});
 const restored=editBehaviorParameter(changed,p,"obstacle_support_frames",2);
 assert.equal(sameBehavior(restored.selection,base.selection),true);
 assert.equal(sameBehavior(behaviorDraft(p,"fluid","one")!.selection,base.selection),true);
});
test("invalid, incompatible and stale corrections cannot be written", () => {
 const p=fixture(),base=behaviorEditDraft(p,"one")!;
 for(const [key,value] of [["enter_frames",true],["enter_frames",1.5],["pixel_confidence",Number.NaN],["object_tracking_enabled",1],["unknown",1]] as const) {
  const changed=editBehaviorParameter(base,p,key,value);assert.equal(behaviorWrite(changed,p,"one"),null);
 }
 assert.equal(behaviorWrite(editBehaviorParameter(base,p,"obstacle_support_frames",5),p,"one"),null);
 assert.equal(behaviorWrite(base,{...p,revision:10},"one"),null);
 assert.equal(behaviorWrite(base,p,"other"),null);
});
test("custom parameters are restored from device rather than browser defaults", () => {
 const p=fixture();p.behavior_catalog.active.overrides={object_growth_min:1.3};
 const d=behaviorEditDraft(p,"one")!;assert.deepEqual(d.selection.overrides,{object_growth_min:1.3});
 assert.equal(catalog.profiles.at(-1).parameters.object_tracking_enabled,false);
});
test("older device exposes presets and no unsupported parameter editor", () => {
 const p=fixture() as JsonRecord;const raw={...(p.behavior_catalog as JsonRecord)};
 delete raw.parameter_editing_version;delete raw.parameter_schema;delete raw.active_parameters;
 p.behavior_catalog=raw;assert.equal(behaviorCatalog(p).editable,false);assert.equal(behaviorEditDraft(p,"one"),null);
});
test("editing saved model values keeps intentional corrections equal to a preset default", () => {
 const p=fixture() as JsonRecord, raw=p.behavior_catalog as JsonRecord;
 raw.active_base_parameters={...(catalog.profiles.at(-1).parameters),pixel_confidence:.82};
 raw.active={version:1,profile_id:"fluid",profile_revision:2,overrides:{pixel_confidence:.9}};
 const base=behaviorEditDraft(p,"one")!;
 assert.equal(behaviorParameterValues(base.selection,p).pixel_confidence,.9);
 const custom=editBehaviorParameter(base,p,"pixel_confidence",.7);
 assert.equal(custom.selection.overrides?.pixel_confidence,.7);
 assert.equal(behaviorParameterValues(custom.selection,p).pixel_confidence,.7);
 const restored=editBehaviorParameter(custom,p,"pixel_confidence",.82);
 assert.equal(restored.selection.overrides,undefined);
 assert.equal(behaviorParameterValues(restored.selection,p).pixel_confidence,.82);
});