import assert from "node:assert/strict";
import {test} from "node:test";
import {heightAvailable} from "../../web-next/lib/posture";
import {adoptIntervention} from "../../web-next/lib/operator-control";
import type {JsonRecord,RiderStatus} from "../../web-next/lib/model";
function fixture():RiderStatus {
 return {connection:{configured:true,status:"online"},errors:{},settings:{revision:45},
 control:{safety:{state:"DISARMED",owner:null,ready:true,generation:"g0",operator_intervention_version:1,
 operator_height_intervention_version:1,panel_control:{version:1},capabilities:{height:{available:true},move:{available:false}}}}} as unknown as RiderStatus;
}
test("height Apply is available disarmed independently from drive capability",()=>{
 assert.equal(heightAvailable(fixture(),"panel"),true);
});
test("height does not bypass E-Stop, missing hardware, stale control or older Pi",()=>{
 for(const patch of [{state:"ESTOP"},{ready:false},{stale:true},{operator_height_intervention_version:undefined},{capabilities:{height:{available:false}}}]){
 const status=fixture();Object.assign(status.control!.safety as JsonRecord,patch);assert.equal(heightAvailable(status,"panel"),false);
 }
 const status=fixture();status.errors.control="offline";assert.equal(heightAvailable(status,"panel"),false);
});
test("only an exact supported height acknowledgement adopts new authority",()=>{
 const status=fixture(),intent={action:"height",height:95,intervene:true,operator_intervention_version:1,operator_consent:true,operator_plan:"height 95 SDK",owner:"manual",arming_mode:"operator",panel_id:"panel",generation:"g0",request_id:"h1"};
 const response={confirmed:true,server_time:10,ack:{action:"height",request_id:"h1",source:"manual",status:"executed",accepted:true,command_generation:"g0",generation:"g1"},
 safety:{state:"ARMED",owner:"manual",generation:"g1",panel_control:{holder:"panel"}}};
 assert.ok(adoptIntervention(status,response,intent,"panel"));
 (status.control!.safety as JsonRecord).operator_height_intervention_version=undefined;
 assert.equal(adoptIntervention(status,response,intent,"panel"),null);
});
