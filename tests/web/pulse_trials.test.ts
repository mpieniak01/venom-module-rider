import assert from "node:assert/strict";
import {test} from "node:test";
import {pulseProfiles,matchingPulse,pulseActive,pulseRun,type PulseRun} from "../../web-next/lib/pulse-trials";
import {alignTrial,readTimeline,type MotionStream} from "../../web-next/lib/motion-timeline";

test("only Pi-supported exact A/B/C recipes enable the trial selector",()=>{
 const profiles=[{id:"A",version:1,count:1,pulse_ms:500,gap_ms:0},{id:"C",version:1,count:5,pulse_ms:100,gap_ms:100}];
 assert.equal(pulseProfiles({version:1,supported:true,profiles}).length,2);
 assert.equal(pulseProfiles({version:1,supported:false,profiles}).length,0);
 assert.equal(pulseProfiles({version:1,supported:true,profiles:[{...profiles[0],pulse_ms:1000}]}).length,0);
});
test("edited pulse cannot retain an A/B/C label for a different duration",()=>{
 const profile={id:"A",version:1,count:1,pulse_ms:500,gap_ms:0} as const;
 assert.ok(matchingPulse(profile,{forward:{duration:.5}}));
 assert.ok(!matchingPulse(profile,{forward:{duration:.25}}));
 assert.ok(!matchingPulse(undefined,{forward:{duration:.5}}));
});
test("terminal runs cannot be mistaken for active control",()=>{
 assert.ok(pulseActive({phase:"gap"}));assert.ok(!pulseActive({phase:"completed"}));
 assert.ok(!pulseRun({version:1,run_id:"a",phase:"pulse",elapsed_ms:NaN}));
 assert.ok(pulseRun({version:1,run_id:"a",phase:"pulse",elapsed_ms:2}));
});
test("whole series alignment moves telemetry and markers together without altering Pi event times",()=>{
 const run={run_id:"one",t:3,events:[{kind:"pulse_started",pulse_index:1,elapsed_ms:0}]} as PulseRun;
 const stream={points:[],events:[],outputs:[{t:3.1}],trials:[run],range:[1,5]} as unknown as MotionStream;
 const aligned=alignTrial(stream,"series:one")!;
 assert.equal(aligned.trials![0].t,0);assert.ok(Math.abs(aligned.outputs[0].t-.1)<1e-9);
 assert.equal(aligned.trials![0].events[0].elapsed_ms,0);assert.equal(stream.trials![0].t,3);
});
test("oversized untrusted series metadata is rejected by the timeline reader",()=>{
 const row={schema_version:1,dataset_revision:"a",session_id:"b",streams:[{id:"s",points:[],events:[],outputs:[],range:[0,1],trials:[{t:0,events:Array(33).fill({})}]}]};
 assert.throws(()=>readTimeline(row),/invalid/);
});
