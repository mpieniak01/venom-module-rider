import { record, safetyOf, settingsOf, type JsonRecord, type RiderStatus } from "./model";

type Request = { navigation?:{revision:number;values:JsonRecord}; manual?:{revision:number;values:JsonRecord;directions:Record<string,JsonRecord>} };
type Dependencies = {
 read:(path:string)=>Promise<JsonRecord>; mutate:(path:string,body:JsonRecord)=>Promise<unknown>;
 stop:()=>Promise<unknown>; session:()=>string; epoch:()=>number; generation:()=>string;
 current:()=>boolean; sleep?:(ms:number)=>Promise<void>;
 onNavigation:(policy:JsonRecord)=>void; onManual:()=>void;
};
const fail=(code:string):never=>{throw Object.assign(new Error(code),{data:{error:code}});};
/** One explicit settings action. Never ARM, replay movement or restore a drive goal. */
export async function applyOperatorSettings(request:Request,d:Dependencies,resumeObservation=true) {
 const origin=d.session();let epoch=d.epoch(),generation=d.generation();
 const check=()=>{if(!d.current()||d.session()!==origin||d.epoch()!==epoch||d.generation()!==generation)fail("navigation_trial_cancelled");};
 check();
 const loaded=await d.read("/navigation/config");check();
 let observed=await d.read("/status?section=calibration");check();
 const policy=record(loaded.config),goals=record(loaded.goals);
 if(request.navigation&&policy.revision!==request.navigation.revision)fail("navigation_configuration_conflict");
 if(request.manual&&settingsOf(observed as RiderStatus).revision!==request.manual.revision)fail("manual_settings_conflict");
 let safety=safetyOf(observed as RiderStatus);
 if(safety.generation!==generation||typeof safety.moving!=="boolean"||safety.stale!==false||!['ARMED','DISARMED'].includes(String(safety.state))||record(observed.errors).control)fail("stale_control");
 const stopping=safety.state==='ARMED'||safety.moving===true||goals.drive===true;
 if(stopping){
  const nextEpoch=epoch+1;await d.stop();
  if(d.epoch()!==nextEpoch||d.session()!==origin||!d.current())fail("navigation_trial_cancelled");
  epoch=nextEpoch;generation=d.generation();check();
 }
 for(const [key,name] of [['drive','s14_venom_navigation'],['observe','s15_venom_perception']] as const){
  if(goals[key]===true){check();await d.mutate('/goal/'+name,{enabled:false});check();}
 }
 if(stopping){
  for(let attempt=0;attempt<20;attempt++){
   observed=await d.read('/status?section=control');check();safety=safetyOf(observed as RiderStatus);
   if(safety.generation!==generation)fail("navigation_trial_cancelled");
   if(safety.state==='DISARMED'&&safety.moving===false&&safety.stale===false)break;
   if(attempt===19)fail('manual_settings_requires_idle');
   await (d.sleep??(ms=>new Promise(resolve=>setTimeout(resolve,ms))))(50);check();
  }
 }
 const verifyIdle=async()=>{
  const fresh=await d.read('/status?section=control');check();const now=safetyOf(fresh as RiderStatus);
  if(now.generation!==generation||now.state!=='DISARMED'||now.moving!==false||now.stale!==false||record(fresh.errors).control)fail('navigation_trial_cancelled');
 };
 await verifyIdle();
 if(request.manual){check();await d.mutate('/settings',request.manual);check();d.onManual();}
 let saved=policy;
 if(request.navigation){
  await verifyIdle();check();const response=record(await d.mutate('/navigation/config',request.navigation));check();
  saved=record(record(response.data??response).config);
  if(!Number.isSafeInteger(saved.revision))fail('navigation_configuration_unconfirmed');
  d.onNavigation(saved);
 }
 let observationError:unknown=null;
 if(resumeObservation&&(goals.observe===true||goals.drive===true)){
  await verifyIdle();check();try{await d.mutate('/goal/s15_venom_perception',{enabled:true});check();}catch(error){check();observationError=error;}
 }
 return {policy:saved,observationError,token:{session:origin,epoch,generation},wasObserving:goals.observe===true||goals.drive===true};
}
