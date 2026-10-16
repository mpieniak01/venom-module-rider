import { record, str, type JsonRecord } from "./model";

export const AUTO_BASE="/api/v1/rider/samples/automatic";
export const eventNames=["new_obstacle","state_change","uncertainty","new_scene"] as const;
export type EventName=typeof eventNames[number];
export type EventProfile={
  events:EventName[]; stable_frames:number; stable_ms:number; cooldown_ms:number;
  scene_distance_bits:number; burst_frames:number; burst_interval_ms:number;
  max_samples:number; duration_s:number; max_gap_ms:number;
};
export const defaultEventProfile:EventProfile={
  events:["new_obstacle","uncertainty","new_scene"],stable_frames:3,stable_ms:250,
  cooldown_ms:3000,scene_distance_bits:10,burst_frames:1,burst_interval_ms:200,
  max_samples:50,duration_s:300,max_gap_ms:1500,
};
export type AutoStatus=JsonRecord & {state:"on"|"off";id?:string;session_id?:string;reason?:string|null;events?:JsonRecord[]};
export function autoStatus(value:unknown):AutoStatus {
  const row=record(value);
  if(!["on","off"].includes(str(row.state)) || row.state==="on"&&!/^[a-f0-9]{32}$/.test(str(row.id)))throw new Error("invalid_auto_response");
  return row as AutoStatus;
}
export function autoIntent(sessionId:string|null,sourceSession:string|null,profile:EventProfile,requestId:string) {
  return sessionId&&sourceSession&&profile.events.length
    ? {body:{session_id:sessionId,request_id:requestId,profile},headers:{"X-Rider-Session":sourceSession}}
    : null;
}

export function recoveredAutoEnable(status:AutoStatus,requestId:string|null):boolean {
  return !!requestId && status.request_id===requestId;
}
