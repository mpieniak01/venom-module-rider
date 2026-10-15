import assert from "node:assert/strict";
import {test} from "node:test";
import {frameAge, readAnalysis} from "../../web-next/lib/vision-frame";

test("preview analysis carries its own identity and malformed metadata cannot replace it",()=>{
  const row={run_id:"trial",frame_id:"capture-4",sequence:4,config_revision:2,state:"obstacle",clear_count:0,vision_action:"stop"};
  assert.deepEqual(readAnalysis(JSON.stringify(row)),row);
  for(const value of [null,"not json","[]",JSON.stringify({...row,sequence:"4"}),JSON.stringify({...row,state:"other"}),"a".repeat(4097)]){
    assert.equal(readAnalysis(value),undefined);
  }
});
test("preview age advances on the browser monotonic clock independently of wall-clock offsets",()=>{
  const frame={ageMs:120,receivedAt:1000,ageSource:"pi-reported-plus-host-elapsed"};
  assert.equal(frameAge(frame,1040),160);
  assert.equal(frameAge(frame,999),120);
  assert.equal(frameAge({ageMs:NaN,receivedAt:1000},1040),null);
  assert.equal(frameAge(null,1040),null);
});
