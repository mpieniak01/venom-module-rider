import assert from "node:assert/strict";
import { test } from "node:test";
import { navigationPulseLimit, navigationSpeedLimit, navigationTurnPulseLimit } from "../../web-next/lib/loop-controls";
import { descriptorRange } from "../../web-next/lib/field-range";
import { calibrationDraft, directionDescriptor } from "../../web-next/lib/calibration";

test("new Pi advertises matching ceilings; legacy Pi retains its limits", () => {
 const policy={motion_limits:{version:1,max_speed:1,max_motion_ms:500,max_turn_speed:1,max_turn_ms:500}};
 assert.equal(navigationSpeedLimit(policy,true),1);
 assert.equal(navigationSpeedLimit(policy,false,true),1);
 assert.equal(navigationPulseLimit(policy,true),500);
 assert.equal(navigationTurnPulseLimit(policy),500);
 assert.equal(navigationSpeedLimit({},true),.1);
 assert.equal(navigationPulseLimit({},true),150);
 assert.equal(navigationTurnPulseLimit({}),150);
 assert.equal(navigationSpeedLimit({motion_limits:{version:1,max_speed:Infinity}},true),.1);
});
test("range labels match scaled units and retain lower exclusive bounds", () => {
 assert.equal(descriptorRange({minimum:.01,maximum:.5,display_factor:1000,display_unit:"ms"}),"10–500 ms");
 assert.equal(descriptorRange({minimum:0,maximum:1,display_factor:100,display_unit:"%"}),"0–100 %");
 assert.equal(descriptorRange({minimum:0,maximum:1,exclusive_minimum:true}),">0–1");
});
test("raising ceilings never overwrites the saved draft values", () => {
 const settings={revision:36,values:{linear_rate:.5},directions:{forward:{speed:.6,duration:.25,maximum:.6}},limits:{linear_speed:1,pulse_duration:.5}};
 const draft=calibrationDraft(settings);
 assert.deepEqual(draft.values,settings.values);
 assert.deepEqual(draft.directions,settings.directions);
});

test("manual speed range explains the current direction cap, not only hardware ceiling",()=>{
 const settings={parameter_schema:{direction_fields:{speed:{minimum:0,maximum:1,display_factor:100,display_unit:"%"}}}};
 assert.equal(descriptorRange(directionDescriptor(settings,"speed",{maximum:.6})),"0–60 %");
});
