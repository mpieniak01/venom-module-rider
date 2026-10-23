import assert from "node:assert/strict";
import {test} from "node:test";
import {readSessions} from "../../web-next/lib/samples";

test("empty or populated session lists preserve their displayed identity",()=>{
  assert.deepEqual(readSessions({sessions:[]}),[]);
  const session={session_id:"study-a",state:"closed",started_at:1700000000};
  assert.equal(readSessions({sessions:[session]})[0],session);
});
test("missing, non-array or malformed records cannot reach the session chooser",()=>{
  for(const response of [null,{}, {sessions:null},{sessions:{}},{sessions:[null]},{sessions:[{session_id:4,state:"closed",started_at:0}]},{sessions:[{session_id:"a",state:"closed",started_at:"yesterday"}]}])assert.throws(()=>readSessions(response),/sample_sessions_invalid/);
});
