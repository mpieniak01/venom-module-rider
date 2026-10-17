import assert from "node:assert/strict";
import { test } from "node:test";
import { adoptIntervention } from "../../web-next/lib/operator-control";
import type { JsonRecord, RiderStatus } from "../../web-next/lib/model";
function scenario() {
  const previous = {connection:{session:"session",device_id:"fixture",configured:true,status:"online"},control:{server_time:10,safety:{state:"ARMED",owner:"navigation",generation:"old"}}} as unknown as RiderStatus;
  const intent = {action:"move",intervene:true,request_id:"request",panel_id:"panel",generation:"old",seq:4};
  const response:JsonRecord = {confirmed:true,server_time:11,ack:{request_id:"request",action:"move",source:"manual",accepted:true,status:"executed",command_generation:"old",generation:"new"},safety:{state:"ARMED",owner:"manual",generation:"new",panel_control:{version:1,holder:"panel"}}};
  return {previous,intent,response};
}
test("executed own intervention rebases generation so a held direction can renew", () => {
  const {previous,intent,response}=scenario();
  const adopted=adoptIntervention(previous,response,intent,"panel");
  assert.equal((adopted?.control?.safety as JsonRecord).generation,"new");
  assert.equal((previous.control?.safety as JsonRecord).generation,"old");
});
test("pending, rejected, foreign and delayed acknowledgements cannot restore authority", () => {
  for (const patch of [{confirmed:false},{pending:true},{server_time:9},{ack:{request_id:"foreign"}},{safety:{state:"DISARMED",generation:"new"}},{safety:{state:"ARMED",owner:"manual",generation:"new",panel_control:{holder:"foreign"}}}]) {
    const {previous,intent,response}=scenario();
    assert.equal(adoptIntervention(previous,{...response,...patch},intent,"panel"),null);
  }
  const {previous,intent,response}=scenario();
  (previous.control!.safety as JsonRecord).generation="stop-generation";
  assert.equal(adoptIntervention(previous,response,intent,"panel"),null);
});
