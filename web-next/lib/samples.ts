import { frameAge } from "./vision-frame";
import { record, str, type JsonRecord } from "./model";
import type { Frame } from "./use-rider";

export const SAMPLES_BASE = "/api/v1/rider/samples";
export type Collection = { session_id:string; source_device_id:string; state:string; started_at:number; policy:JsonRecord; counts:Record<string,number>; evidence?:JsonRecord };
export type CaptureIntent = { session_id:string; capture_request_id:string; frame_key:string; allow_historical:boolean };
export type CaptureOutcome = { id:string; state:string; sample_id?:string|null; error?:string|null; saved_at?:number|null };
export type Sample = JsonRecord & { sample_id:string; frame_id?:string|null; sequence?:number|null; raw:JsonRecord; quality:JsonRecord };
export type SamplePage = {session:Collection;samples:Sample[];offset:number;limit:number;total:number};
const ticket = /^[a-f0-9]{32}$/;
export function historicalFrame(frame:Frame|null, now?:number):boolean {
  return !!frame && (frame.stale || Number(frame.recordedAt)>0 || (frameAge(frame,now)??0)>1000);
}
export function captureIntent(session:Collection|null, frame:Frame|null, requestId:string):CaptureIntent|null {
  if(session?.state!=="collecting" || !frame?.captureKey || !ticket.test(frame.captureKey) || frame.decoded!==true) return null;
  return {session_id:session.session_id,capture_request_id:requestId,frame_key:frame.captureKey,allow_historical:historicalFrame(frame)};
}
export function recoverCapture(value:unknown):CaptureIntent|null {
  const row=record(value);
  return ticket.test(str(row.session_id)) && ticket.test(str(row.frame_key)) && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(str(row.capture_request_id)) && typeof row.allow_historical==="boolean"
    ? row as CaptureIntent : null;
}
export function captureOutcome(value:unknown):CaptureOutcome {
  const row=record(value);
  if(!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(str(row.id)) || !["queued","running","saved","failed"].includes(str(row.state)) || row.state==="saved"&&!ticket.test(str(row.sample_id))) throw new Error("invalid_capture_response");
  return row as CaptureOutcome;
}
