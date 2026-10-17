import { data, finite, record, safetyOf, str, type JsonRecord, type RiderStatus } from "./model";

/** Only an executed acknowledgement for this exact intervention may rebase a hold. */
export function adoptIntervention(previous: RiderStatus | null, response: JsonRecord, intent: JsonRecord, panelId: string): RiderStatus | null {
  if (!previous || response.confirmed !== true || response.pending === true) return null;
  const ack = record(response.ack), safety = record(response.safety), old = safetyOf(previous);
  if (intent.intervene !== true || intent.panel_id !== panelId || old.owner !== "navigation"
    || old.generation !== intent.generation || ack.request_id !== intent.request_id
    || ack.source !== "manual" || ack.action !== "move" || ack.status !== "executed" || ack.accepted !== true
    || ack.command_generation !== intent.generation || !str(ack.generation) || ack.generation === intent.generation
    || safety.generation !== ack.generation || safety.state !== "ARMED" || safety.owner !== "manual"
    || record(safety.panel_control).holder !== panelId || !finite(response.server_time)
    || Number(response.server_time) < Number(data(previous.control).server_time ?? 0)) return null;
  return { ...previous, control: { ...data(previous.control), server_time: response.server_time, safety } };
}
