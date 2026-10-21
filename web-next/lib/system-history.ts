import {data, finite, record, type JsonRecord, type RiderStatus} from "./model";

export function systemAge(system:JsonRecord,now=performance.now()):number|null {
  const age=system.client_age_s;
  const received=system.client_received_at_ms;
  if(finite(age)&&finite(received))return age+Math.max(0,(now-received)/1000);
  return finite(record(system.observation).age_s)?Number(record(system.observation).age_s):null;
}
export function retainSystem(previous:RiderStatus|null,next:RiderStatus,now=performance.now()):RiderStatus {
  const same=previous?.connection.session===next.connection.session&&previous?.connection.device_id===next.connection.device_id;
  const old=same?data(previous?.system):{};
  if(!next.system){
    if(next.errors.system&&Object.keys(old).length)return {...next,system:{...old,read_error:next.errors.system}};
    return next;
  }
  const system=data(next.system),observation=record(system.observation),before=record(old.observation);
  const unchanged=observation.instance_id?observation.instance_id===before.instance_id&&observation.sequence===before.sequence:system.ts===old.ts;
  const identity=observation.instance_id&&observation.instance_id===before.instance_id;
  if(identity&&finite(observation.sequence)&&finite(before.sequence)&&observation.sequence<before.sequence)
    return {...next,system:{...old,read_error:"out_of_order"}};
  const event=before.boot_id&&observation.boot_id&&before.boot_id!==observation.boot_id?"systemHostRestart"
    :before.instance_id&&observation.instance_id&&before.instance_id!==observation.instance_id?"systemApiRestart"
    :identity&&before.generation!==observation.generation?"systemSamplerResume":old.history_event;
  const age=finite(observation.age_s)?observation.age_s:finite(system.server_time)&&finite(system.ts)?Math.max(0,system.server_time-system.ts):null;
  const previousAge=unchanged?systemAge(old,now):null;
  return {...next,system:{...system,read_error:next.errors.system??(next.connection.status==="offline"?"offline":null),
    history_event:event,client_received_at_ms:now,client_age_s:age===null?null:Math.max(age,previousAge??0)}};
}
export type HistoryPoint={age:number;separated:boolean;cpu:unknown;mem:unknown;peak:unknown};
export function systemHistory(system:JsonRecord,now=performance.now(),wall=Date.now()/1000):HistoryPoint[]{
  const times=Array.isArray(system.hist_ts)?system.hist_ts:[];
  const mono=system.hist_mono,generations=system.hist_generation,seq=system.hist_seq,observation=record(system.observation);
  const sourceAge=systemAge(system,now);
  const monotonic=Array.isArray(mono)&&mono.length===times.length&&finite(observation.monotonic_s)&&sourceAge!==null;
  const cadence=finite(record(system.sampling).summary_interval_ms)?Number(record(system.sampling).summary_interval_ms)/1000:1;
  const channels=[system.hist_cpu,system.hist_mem,system.hist_cpu_peak].map(row=>Array.isArray(row)?row:[]);
  const result:HistoryPoint[]=[];
  for(let i=0;i<times.length;i++){
    const stamp=monotonic?mono[i]:times[i],previous=monotonic?mono[i-1]:times[i-1];
    if(!finite(stamp))continue;
    const age=monotonic?sourceAge!+Number(observation.monotonic_s)-stamp:wall-stamp;
    if(!finite(age)||age<-.1||age>60)continue;
    const separated=i>0&&(!finite(previous)||stamp<=previous||stamp-previous>2*cadence
      ||Array.isArray(generations)&&generations[i]!==generations[i-1]
      ||Array.isArray(seq)&&seq[i]!==seq[i-1]+1);
    result.push({age:Math.max(0,age),separated,cpu:channels[0][i],mem:channels[1][i],peak:channels[2][i]});
  }
  return result;
}
