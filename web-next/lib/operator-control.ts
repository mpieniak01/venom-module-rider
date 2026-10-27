import { data, finite, record, safetyOf, str, type JsonRecord, type RiderStatus } from "./model";

/** Only an executed acknowledgement for this exact intervention may rebase a hold. */
export function adoptIntervention(previous: RiderStatus | null, response: JsonRecord, intent: JsonRecord, panelId: string): RiderStatus | null {
  if (!previous || response.confirmed !== true || response.pending === true) return null;
  const ack = record(response.ack), safety = record(response.safety), old = safetyOf(previous);
  const pause = intent.action === "navigation_pause";
  const explicit = old.operator_intervention_version === 1 && intent.operator_intervention_version === 1
    && (intent.action !== "height" || old.operator_height_intervention_version === 1)
    && intent.operator_consent === true && intent.owner === "manual" && intent.arming_mode === "operator"
    && !!str(intent.operator_plan) && ["ARMED", "DISARMED"].includes(str(old.state));
  const scopedPreparation = explicit && old.owner === "navigation" && !!str(intent.supersedes_operation_id)
    && record(old.operator_control).operation_id === intent.supersedes_operation_id;
  const observedOwnTransfer = old.state === "ARMED" && old.owner === "manual" && old.generation === ack.generation
    && record(old.panel_control).holder === panelId && record(old.operator_control).operation_id === intent.request_id;
  if ((!pause && intent.intervene !== true) || intent.panel_id !== panelId || (!explicit && old.owner !== "navigation" && !(old.owner === "manual" && (pause || observedOwnTransfer || str(intent.supersedes_operation_id))))
    || (!observedOwnTransfer && !scopedPreparation && old.generation !== intent.generation) || ack.request_id !== intent.request_id
    || ack.source !== "manual" || ack.action !== intent.action || ack.status !== "executed" || ack.accepted !== true
    || ack.command_generation !== intent.generation || !str(ack.generation) || ack.generation === intent.generation
    || safety.generation !== ack.generation || safety.state !== "ARMED" || safety.owner !== "manual"
    || record(safety.panel_control).holder !== panelId || !finite(response.server_time)
    || (!observedOwnTransfer && Number(response.server_time) < Number(data(previous.control).server_time ?? 0))) return null;
  if (observedOwnTransfer) return previous;
  return { ...previous, control: { ...data(previous.control), server_time: response.server_time, safety } };
}
