import assert from "node:assert/strict";
import { test } from "node:test";
import { continuousAvailable, navigationAvailable } from "../../web-next/lib/navigation";
import { manualFeedback } from "../../web-next/lib/manual-feedback";
import type { RiderStatus } from "../../web-next/lib/model";

function state(holder: string | null = "panel-a"): RiderStatus {
  return { connection:{configured:true,status:"online"}, settings:{revision:1}, errors:{},
    control:{safety:{state:"ARMED",owner:"manual",panel_motion_enabled:true,
      capabilities:{move:{available:true}},generation:"one",
      panel_control:{version:1,holder},output:{lx:0,az:0}}}
  } as unknown as RiderStatus;
}
test("continuous drive requires supported Pi, current connection and its own holder", ()=>{
  const current=state();
  assert.equal(continuousAvailable(current,"panel-a"),true);
  assert.equal(continuousAvailable(current,"panel-b"),false);
  assert.equal(navigationAvailable(current,"panel-b"),false);
  assert.equal(continuousAvailable(state(null),"panel-a"),false);
  current.connection.status="offline";
  assert.equal(continuousAvailable(current,"panel-a"),false);
  delete (current.control!.safety as Record<string,unknown>).panel_control;
  assert.equal(navigationAvailable(current,"panel-a"),true);
  assert.equal(continuousAvailable(current,"panel-a"),false);
});
test("intent acknowledgement does not claim that motors or robot moved", ()=>{
  const current=state();
  const request={phase:"accepted",direction:"forward",mode:"hold",generation:"one"};
  assert.equal(manualFeedback(current,request,true).requestKey,"manualAccepted");
  assert.equal(manualFeedback(current,request,true).executionKey,"manualOutputZero");
  (current.control!.safety as Record<string,unknown>).output={lx:.1,az:0};
  assert.equal(manualFeedback(current,request,true).executionKey,"manualOutputNonzero");
  assert.equal(manualFeedback(current,request,false).executionKey,"manualOutputUnknown");
  current.errors.control="pi_unreachable";
  assert.equal(manualFeedback(current,request,true).executionKey,"manualOutputUnknown");
});
test("pending, rejection, generation changes and missing observations remain distinct",()=>{
  const current=state();
  assert.equal(manualFeedback(current,null,true).requestKey,"manualNoRequest");
  assert.equal(manualFeedback(current,{generation:"old",phase:"accepted"},true).requestKey,"manualNoRequest");
  assert.equal(manualFeedback(current,{generation:"one",phase:"pending"},true).requestKey,"manualPending");
  assert.equal(manualFeedback(current,{generation:"one",phase:"failed"},true).requestKey,"manualRejected");
  assert.equal(manualFeedback(null,null,false).executionKey,"manualOutputUnknown");
});

test("a missing control read cannot sustain or start continuous motion",()=>{
  const current=state();
  current.connection.status="degraded";
  current.errors.control="pi_unreachable";
  assert.equal(continuousAvailable(current,"panel-a"),false);
  delete current.errors.control;
  (current.control!.safety as Record<string,unknown>).stale=true;
  assert.equal(continuousAvailable(current,"panel-a"),false);
});
