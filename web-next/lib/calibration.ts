import { DIRECTIONS, finite, record, type JsonRecord, type NumericDescriptor } from "./model";
export type CalibrationDraft = { revision: number; values: JsonRecord; directions: Record<string, JsonRecord> };
export function calibrationDraft(settings: JsonRecord): CalibrationDraft {
  return { revision: Number(settings.revision), values: { ...record(settings.values) }, directions: Object.fromEntries(Object.entries(record(settings.directions)).map(([key, row]) => [key, { ...record(row) }])) };
}
function inRange(value: unknown, descriptor: JsonRecord): boolean { return finite(value) && (!finite(descriptor.minimum) || value >= descriptor.minimum) && (!finite(descriptor.maximum) || value <= descriptor.maximum) && (descriptor.exclusive_minimum !== true || !finite(descriptor.minimum) || value > descriptor.minimum); }
export function validCalibration(draft: CalibrationDraft, settings: JsonRecord): boolean {
  const schema = record(settings.parameter_schema), directionSchema = record(schema.direction_fields), valueSchema = record(schema.value_fields);
  if (!Number.isInteger(draft.revision)) return false;
  for (const name of DIRECTIONS) {
    const row = draft.directions[name]; if (!row) return false;
    for (const [field, descriptor] of Object.entries(directionSchema)) if (!inRange(row[field], record(descriptor))) return false;
    const minimum = Number(row.minimum), maximum = Number(row.maximum), speed = Number(row.speed);
    if (minimum > maximum || (speed !== 0 && (speed < minimum || speed > maximum)) || Number(row.step) <= 0) return false;
    const axis = name === "forward" || name === "backward" ? "linear" : "turn";
    const acceleration = Number(draft.values[`${axis}_rate`]), deceleration = Number(draft.values[`${axis}_deceleration`]);
    if (acceleration > 0 && deceleration > 0 && minimum > Number(row.duration) / (1 / acceleration + 1 / deceleration)) return false;
  }
  for (const [field, descriptor] of Object.entries(valueSchema)) if (!inRange(draft.values[field], record(descriptor))) return false;
  return Array.isArray(schema.ramp_modes) && schema.ramp_modes.some(mode => record(mode).value === draft.values.ramp_mode);
}
export function calibrationValues(draft: CalibrationDraft, settings: JsonRecord): JsonRecord {
  return Object.fromEntries([...Object.keys(record(record(settings.parameter_schema).value_fields)), "ramp_mode"].map(key => [key, draft.values[key]]));
}
export function directionDescriptor(settings: JsonRecord, field: string, row?: JsonRecord): NumericDescriptor {
  const descriptor = { ...record(record(record(settings.parameter_schema).direction_fields)[field]) } as NumericDescriptor;
  if (row && (field === "speed" || field === "minimum") && finite(row.maximum)) descriptor.maximum = row.maximum;
  if (row && field === "maximum" && finite(row.minimum)) descriptor.minimum = row.minimum;
  return descriptor;
}