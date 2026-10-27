import {navigationAvailable} from "./navigation";
import {record,safetyOf,settingsOf,str,type RiderStatus} from "./model";

export function heightInterventionSupported(status:RiderStatus|null):boolean {
 return safetyOf(status).operator_height_intervention_version===1;
}
/** One explicit Apply can take height authority; observation and edits never do. */
export function heightInterventionAvailable(status:RiderStatus|null,panelId:string):boolean {
 const safety=safetyOf(status);
 return heightInterventionSupported(status)&&!!panelId&&!!status?.connection.configured
  &&["online","degraded"].includes(status.connection.status)&&!status.errors.control
  &&safety.stale!==true&&safety.ready===true&&["DISARMED","ARMED"].includes(str(safety.state))
  &&record(record(safety.capabilities).height).available===true
  &&record(safety.panel_control).version===1&&!!str(safety.generation)
  &&Number.isInteger(settingsOf(status).revision);
}
export function heightAvailable(status:RiderStatus|null,panelId:string):boolean {
 return heightInterventionSupported(status)?heightInterventionAvailable(status,panelId)
  :navigationAvailable(status,panelId)&&safetyOf(status).owner==="manual";
}
