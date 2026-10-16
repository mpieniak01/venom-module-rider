import assert from "node:assert/strict";
import { test } from "node:test";
import { captureIntent, captureOutcome, historicalFrame, recoverCapture, type Collection } from "../../web-next/lib/samples";
import type { Frame } from "../../web-next/lib/use-rider";
const key="a".repeat(32);
const session={session_id:"b".repeat(32),state:"collecting"} as Collection;
const frame={captureKey:key,decoded:true,stale:false,ageMs:20,receivedAt:performance.now(),recordedAt:""} as Frame;
test("a capture pins the visible RAW receipt, never a next-frame URL",()=>{
  const intent=captureIntent(session,frame,"operator-request");
  assert.equal(intent?.frame_key,key);
  assert.equal(intent?.session_id,session.session_id);
  assert.equal(intent?.allow_historical,false);
  for(const invalid of [null,{...frame,decoded:false},{...frame,captureKey:undefined},{...frame,captureKey:"bad"}])assert.equal(captureIntent(session,invalid,"request"),null);
  assert.equal(captureIntent({...session,state:"closed"},frame,"request"),null);
});
test("historical capture is explicit for replay, stopped or aged images",()=>{
  assert.equal(historicalFrame(null),false);
  assert.equal(historicalFrame({...frame,recordedAt:"100"}),true);
  assert.equal(historicalFrame({...frame,stale:true}),true);
  assert.equal(historicalFrame({...frame,receivedAt:10},1100),true);
  assert.equal(captureIntent(session,{...frame,recordedAt:"100"},"request")?.allow_historical,true);
});
test("reload recovery retains the original capture ID and source ticket",()=>{
  const intent=captureIntent(session,frame,"old-http-result");
  assert.deepEqual(recoverCapture(JSON.parse(JSON.stringify(intent))),intent);
  for(const invalid of [null,{}, {...intent,frame_key:"../raw"},{...intent,allow_historical:"false"},{...intent,capture_request_id:"../bad"}])assert.equal(recoverCapture(invalid),null);
});
test("a queue acknowledgement is never presented as a durable save",()=>{
  assert.equal(captureOutcome({id:"request",state:"queued"}).state,"queued");
  assert.equal(captureOutcome({id:"request",state:"failed",error:"disk_full"}).state,"failed");
  assert.equal(captureOutcome({id:"request",state:"saved",sample_id:key}).sample_id,key);
  for(const invalid of [{state:"saved",sample_id:key},{id:"request",state:"saved"},{id:"request",state:"success"}])assert.throws(()=>captureOutcome(invalid),/invalid_capture_response/);
});
