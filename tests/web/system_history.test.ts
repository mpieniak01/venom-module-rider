import assert from "node:assert/strict";
import {test} from "node:test";
import {retainSystem,systemAge,systemHistory} from "../../web-next/lib/system-history";
import type {RiderStatus,JsonRecord} from "../../web-next/lib/model";
const system=():JsonRecord=>({hist_ts:[100,101,90,91],hist_mono:[10,11,12,13],hist_generation:[1,1,2,2],hist_seq:[1,2,3,4],hist_cpu:[null,30,40,50],hist_mem:[20,21,22,23],hist_cpu_peak:[null,50,60,70],observation:{instance_id:"api",sequence:4,monotonic_s:13,age_s:1},sampling:{summary_interval_ms:1000}});
const status=(value:JsonRecord|null):RiderStatus=>({connection:{session:"s",device_id:"pi",configured:true,status:"online",mode:"device"},system:value,errors:{}} as RiderStatus);
test("clock correction preserves monotonic placement and generation breaks",()=>{
 const points=systemHistory(system(),0,9000);
 assert.deepEqual(points.map(p=>p.age),[4,3,2,1]);
 assert.deepEqual(points.map(p=>p.separated),[false,false,true,false]);
 assert.equal(points[0].cpu,null);assert.equal(points[0].mem,20);
});
test("failed system read retains source age and expires history",()=>{
 const first=retainSystem(null,status(system()),0);
 const failed=status(null);failed.errors.system="pi_unreachable";
 const next=retainSystem(first,failed,5000);
 assert.equal(next.system?.read_error,"pi_unreachable");
 assert.equal(systemAge(next.system!,5000),6);
 assert.equal(systemHistory(next.system!,61000).length,0);
});
test("cached HTTP response never refreshes an unchanged source sequence",()=>{
 const first=retainSystem(null,status(system()),0);
 const repeated=retainSystem(first,status(system()),5000);
 assert.equal(systemAge(repeated.system!,5000),6);
});
test("device or module-session switch never retains another source",()=>{
 const first=retainSystem(null,status(system()),0);const next=status(null);next.errors.system="error";
 next.connection.device_id="other";assert.equal(retainSystem(first,next,2000).system,null);
 next.connection.device_id="pi";next.connection.session="new";
 assert.equal(retainSystem(first,next,2000).system,null);
});
test("new API instance resets source age",()=>{
 const first=retainSystem(null,status(system()),0);const fresh=system();
 fresh.observation={instance_id:"new",sequence:1,age_s:0,monotonic_s:99};
 assert.equal(systemAge(retainSystem(first,status(fresh),20000).system!,20000),0);
});
test("legacy firmware gaps and sequence discontinuities split segments",()=>{
 assert.deepEqual(systemHistory({hist_ts:[10,11,20],hist_cpu:[1,2,3]},0,21).map(p=>p.separated),[false,false,true]);
 const row=system();row.hist_generation=[1,1,1,1];row.hist_seq=[1,2,5,6];
 assert.equal(systemHistory(row)[2].separated,true);
});

test("older source sequence cannot roll the displayed history backwards",()=>{
 const first=retainSystem(null,status(system()),0),older=system();
 older.observation={instance_id:"api",sequence:3,age_s:0,monotonic_s:12};
 const next=retainSystem(first,status(older),5000);
 assert.equal(next.system?.read_error,"out_of_order");assert.equal(systemAge(next.system!,5000),6);
});
test("restart and resume labels require distinct source identities",()=>{
 const a=system();a.observation={instance_id:"api",boot_id:"boot",generation:1,sequence:4,age_s:0,monotonic_s:13};
 const first=retainSystem(null,status(a),0);
 for(const [observation,event] of [
  [{instance_id:"new",boot_id:"other",generation:1,sequence:1,age_s:0},"systemHostRestart"],
  [{instance_id:"new",boot_id:"boot",generation:1,sequence:1,age_s:0},"systemApiRestart"],
  [{instance_id:"api",boot_id:"boot",generation:2,sequence:5,age_s:0},"systemSamplerResume"],
 ] as const){const next=system();next.observation=observation;assert.equal(retainSystem(first,status(next),1000).system?.history_event,event);}
});

test("missing source generation is not a confirmed resume event",()=>{
 const a=system();a.observation={instance_id:"api",sequence:4,age_s:0};
 const first=retainSystem(null,status(a),0),b=system();
 b.observation={instance_id:"api",generation:1,sequence:5,age_s:0};
 assert.notEqual(retainSystem(first,status(b),1000).system?.history_event,"systemSamplerResume");
});
