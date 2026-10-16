import { finite, record, safetyOf, str, type JsonRecord, type RiderStatus } from "./model";

/** Intent, acknowledgement and actuator report are separate observations. */
export function manualFeedback(status: RiderStatus | null, request: JsonRecord | null, fresh: boolean) {
  const safety = safetyOf(status);
  const current = request && request.generation === safety.generation;
  const requestKey = !current ? "manualNoRequest" : request.phase === "failed" ? "manualRejected" : request.phase === "pending" ? "manualPending" : "manualAccepted";
  const output = record(safety.output);
  const known = fresh && !status?.errors.control && finite(output.lx) && finite(output.az);
  const executionKey = !known ? "manualOutputUnknown" : output.lx === 0 && output.az === 0 ? "manualOutputZero" : "manualOutputNonzero";
  return { requestKey, executionKey, direction: current ? str(request.direction) : "", mode: current ? str(request.mode) : "", output: known ? `${output.lx} / ${output.az}` : "—" };
}
