import assert from "node:assert/strict";
import { test } from "node:test";
import { applyOperatorSettings } from "../../web-next/lib/apply-operator-settings";
import { perceptionCatalog, selectionMatchesPrepared } from "../../web-next/lib/perception";
import type { JsonRecord } from "../../web-next/lib/model";

function fixture(goals:JsonRecord={observe:true,drive:false},armed=false){
 let epoch=0,generation='a',session='one',stopped=!armed;
 const writes:{path:string;body:JsonRecord}[]=[],acks:string[]=[];
 const dependencies={
  read:async(path:string)=>path==='/navigation/config'?{config:{revision:5,speed:.1},goals}:{connection:{session},settings:{revision:8},errors:{},control:{safety:{state:stopped?'DISARMED':'ARMED',generation,moving:!stopped,stale:false}}},
  mutate:async(path:string,body:JsonRecord)=>{writes.push({path,body});return {data:{config:{revision:6,...(body.values as JsonRecord)}}};},
  stop:async()=>{writes.push({path:'/control',body:{action:'stop'}});epoch++;generation='b';stopped=true;},
  session:()=>session,epoch:()=>epoch,generation:()=>generation,current:()=>true,
  onNavigation:()=>{acks.push('navigation');},onManual:()=>{acks.push('manual');},
 };
 return {dependencies,writes,acks,cancel:()=>{epoch++;},switchDevice:()=>{session='two';}};
}
const request={navigation:{revision:5,values:{speed:.12}}};
test('apply while observing pauses the goal, saves and restores only observation',async()=>{
 const f=fixture();const result=await applyOperatorSettings(request,f.dependencies);
 assert.deepEqual(f.writes.map(w=>w.path),['/goal/s15_venom_perception','/navigation/config','/goal/s15_venom_perception']);
 assert.deepEqual(f.writes.map(w=>w.body),[{enabled:false},{revision:5,values:{speed:.12}},{enabled:true}]);
 assert.equal(result.policy.revision,6);assert.deepEqual(f.acks,['navigation']);
});
test('active driving is stopped once; an applied configuration cannot restart driving',async()=>{
 const f=fixture({drive:true,observe:false},true);await applyOperatorSettings(request,f.dependencies);
 assert.deepEqual(f.writes.map(w=>w.path),['/control','/goal/s14_venom_navigation','/navigation/config','/goal/s15_venom_perception']);
 assert.ok(!f.writes.some(w=>w.body.action==='arm'||w.path==='/goal/s14_venom_navigation'&&w.body.enabled===true));
});
test('idle save does not start observation that the operator did not request',async()=>{
 const f=fixture({drive:false,observe:false});await applyOperatorSettings(request,f.dependencies);
 assert.deepEqual(f.writes.map(w=>w.path),['/navigation/config']);
});
test('start preparation can apply without an extra observation start',async()=>{
 const f=fixture();await applyOperatorSettings(request,f.dependencies,false);
 assert.deepEqual(f.writes.map(w=>w.path),['/goal/s15_venom_perception','/navigation/config']);
});
test('revision conflict is detected before STOP or any configuration write',async()=>{
 const f=fixture({drive:true},true);await assert.rejects(applyOperatorSettings({navigation:{revision:4,values:{}}},f.dependencies),/conflict/);
 assert.deepEqual(f.writes,[]);
});
test('manual acknowledgement survives a failed navigation save; navigation draft is not acknowledged',async()=>{
 const f=fixture();const mutate=f.dependencies.mutate;
 f.dependencies.mutate=async(path,body)=>{if(path==='/navigation/config')throw new Error('conflict');return mutate(path,body);};
 await assert.rejects(applyOperatorSettings({...request,manual:{revision:8,values:{},directions:{}}},f.dependencies),/conflict/);
 assert.deepEqual(f.acks,['manual']);assert.ok(!f.writes.some(w=>w.body.enabled===true));
});
test('a new STOP during settings write cancels subsequent steps and observation recovery',async()=>{
 const f=fixture();const mutate=f.dependencies.mutate;
 f.dependencies.mutate=async(path,body)=>{const result=await mutate(path,body);if(path==='/navigation/config')f.cancel();return result;};
 await assert.rejects(applyOperatorSettings(request,f.dependencies),/cancelled/);
 assert.ok(!f.writes.some(w=>w.body.enabled===true));
});
test('device change cannot write the previous device draft',async()=>{
 const f=fixture();const read=f.dependencies.read;
 f.dependencies.read=async(path)=>{const result=await read(path);f.switchDevice();return result;};
 await assert.rejects(applyOperatorSettings(request,f.dependencies),/cancelled/);assert.deepEqual(f.writes,[]);
});
test('failed observation recovery reports the saved policy without repeating the write',async()=>{
 const f=fixture();const mutate=f.dependencies.mutate;
 f.dependencies.mutate=async(path,body)=>{if(body.enabled===true)throw new Error('unavailable');return mutate(path,body);};
 const result=await applyOperatorSettings(request,f.dependencies);
 assert.equal(result.policy.revision,6);assert.ok(result.observationError);assert.deepEqual(f.acks,['navigation']);
});

test('a prepared settings revision does not masquerade as a different model selection',()=>{
 const catalog=(revision:number,id='a')=>perceptionCatalog({models:[],provider:'venom',pipeline_revision:1,selection:{model_id:id,artifact_sha256:'a'.repeat(64),revision}});
 assert.ok(selectionMatchesPrepared(catalog(55),catalog(56),56));
 assert.ok(!selectionMatchesPrepared(catalog(55),catalog(56,'b'),56));
 assert.ok(!selectionMatchesPrepared(catalog(55),catalog(57),56));
});

test('a different remotely observed generation prevents writes despite a delayed client poll',async()=>{
 const f=fixture();const read=f.dependencies.read;
 f.dependencies.read=async path=>{const row=await read(path);if(path.includes('status'))(row.control!.safety as JsonRecord).generation='other';return row;};
 await assert.rejects(applyOperatorSettings(request,f.dependencies),/stale_control/);
 assert.deepEqual(f.writes,[]);
});
