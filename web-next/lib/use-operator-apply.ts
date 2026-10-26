import { apiFetch } from "@/lib/api-client";
import { calibrationValues, validCalibration } from "./calibration";
import { applyOperatorSettings } from "./apply-operator-settings";
import { loopSettingsValues, navigationPulseLimit, navigationSpeedLimit, navigationTurnPulseLimit } from "./loop-controls";
import { behaviorDraft, validBehaviorDraft } from "./behavior";
import { tuningDraft, validTuning } from "./model-tuning";
import { inputCovers } from "./input-region";
import { record, settingsOf, type JsonRecord } from "./model";
import type { useOperatorTuning } from "./operator-tuning";
import type { RiderClient } from "./use-rider";

export function validOperatorNavigation(base:JsonRecord,draft:JsonRecord):boolean {
 const number=(value:unknown,min:number,max:number,integer=false)=>typeof value==='number'&&Number.isFinite(value)&&value>=min&&value<=max&&(!integer||Number.isInteger(value));
 if(!number(draft.speed,.01,navigationSpeedLimit(base))||!number(draft.max_motion_ms,50,navigationPulseLimit(base),true)||!number(draft.decision_budget_ms,100,500,true))return false;
 const roi=draft.corridor;
 if(!Array.isArray(roi)||roi.length!==4||!roi.every(value=>number(value,0,1))||!(roi[0]<roi[2]&&roi[1]<roi[3]))return false;
 const avoidance=record(draft.avoidance);
 if('avoidance' in draft&&(!number(avoidance.turn_speed,.01,navigationSpeedLimit(base,false,true))||!number(avoidance.turn_ms,50,navigationTurnPulseLimit(base),true)))return false;
 if(JSON.stringify(base.perception)!==JSON.stringify(draft.perception)){
  const tuning=tuningDraft({...base,perception:draft.perception},'validation');
  if(!tuning||!validTuning(tuning))return false;
  const input=record(draft.perception).input_region;
  if(input!==undefined&&!inputCovers(input,roi))return false;
 }
 if(JSON.stringify(base.behavior)!==JSON.stringify(draft.behavior)){
  const selection=record(draft.behavior),candidate=behaviorDraft(base,String(selection.profile_id),'validation');
  if(!candidate||!validBehaviorDraft({...candidate,selection:selection as typeof candidate.selection},base))return false;
 }
 return true;
}
export function useOperatorApply(workspace:ReturnType<typeof useOperatorTuning>,client:RiderClient) {
 const {state,setState,claimed,alive}=workspace,settings=settingsOf(client.status);
 const navigationDirty=!!state.base&&!!state.draft&&JSON.stringify(state.base)!==JSON.stringify(state.draft);
 const manualDirty=!!state.manual&&(JSON.stringify(state.manual.directions)!==JSON.stringify(settings.directions)||JSON.stringify(state.manual.values)!==JSON.stringify(settings.values));
 const dirty=navigationDirty||manualDirty;
 const valid=(!navigationDirty||validOperatorNavigation(state.base!,state.draft!))&&(!manualDirty||validCalibration(state.manual!,settings));
 const apply=async(resumeObservation=true)=>{
  if(claimed.current||!dirty||!valid)throw new Error('operator_settings_invalid');
  claimed.current=true;setState(previous=>({...previous,busy:true}));
  try{
   const result=await applyOperatorSettings({
    ...(navigationDirty?{navigation:{revision:Number(state.base!.revision),values:loopSettingsValues(state.base!,state.draft!)}}:{}),
    ...(manualDirty?{manual:{revision:state.manual!.revision,values:calibrationValues(state.manual!,settings),directions:state.manual!.directions}}:{}),
   },{
    read:path=>apiFetch<JsonRecord>('/api/v1/rider'+path),
    mutate:(path,body)=>client.mutate(path,body,'POST',true),
    stop:()=>client.command({action:'stop'},true,true),
    session:client.currentSession,epoch:client.currentMotionEpoch,generation:client.currentGeneration,
    current:()=>alive.current&&!document.hidden,
    onManual:()=>setState(previous=>({...previous,manual:null})),
    onNavigation:policy=>{const full={...state.base,...policy};setState(previous=>({...previous,base:full,draft:full}));client.setPerceptionPolicy?.(full);client.invalidatePerceptionPreview?.();},
   },resumeObservation);
   await client.poll();
   if(result.observationError)throw Object.assign(new Error('loopStartedReadFailed'),{data:{error:'loopStartedReadFailed'}});
   return result.policy;
  }finally{claimed.current=false;if(alive.current)setState(previous=>({...previous,busy:false}));}
 };
 return {dirty,navigationDirty,manualDirty,valid,busy:state.busy,apply};
}
