import {useState} from "react";
import {useSamples} from "./use-samples";

/** Page-owned recording survives module tab changes, but not device/session changes. */
export function useStudyRecording(deviceId:string|null,sourceSession:string|null){
  const [surface,setSurface]=useState("");
  const capture=useSamples(deviceId,true,"motion-study-v1:"+surface.trim(),sourceSession);
  return {capture,surface,setSurface};
}
export type StudyRecording=ReturnType<typeof useStudyRecording>;
