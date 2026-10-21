import assert from "node:assert/strict";
import {test} from "node:test";
import {manualStopAction} from "../../web-next/lib/navigation";
import type {RiderStatus} from "../../web-next/lib/model";
function state(owner:string,arming_mode:string,navState:string):RiderStatus {
  return {control:{safety:{owner,arming_mode,operator_control:{version:1,state:navState}}}} as unknown as RiderStatus;
}
test("manual authority releases even when old navigation metadata says paused",()=>{
  assert.equal(manualStopAction(state("manual","operator","paused")),"release");
  assert.equal(manualStopAction(state("manual","timed","active")),"release");
});
test("active navigation pauses and journey terminates; idle metadata grants no pause",()=>{
  assert.equal(manualStopAction(state("navigation","timed","active")),"navigation_pause");
  assert.equal(manualStopAction(state("navigation","journey","active")),"stop");
  assert.equal(manualStopAction(state("manual","operator","idle")),"release");
  assert.equal(manualStopAction(null),"release");
});
