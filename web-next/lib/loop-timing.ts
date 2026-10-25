import { finite, record, type JsonRecord, type Section } from "./model";

// Preserve the original freshness budget when the display cadence is changed.
export function freshnessCadence(section: Section): number {
  return ({control:500,vision:500,calibration:1000,overview:2000,resources:5000,face:5000,compute:5000,samples:2000})[section];
}
export function previewInterval(status: unknown): number {
  const value = record(record(status).effective).preview_ms;
  return finite(value) && value >= 500 && value <= 2000 ? value : 500;
}
export function timingValues(value: unknown): Record<string, number> {
  return Object.fromEntries(Object.entries(record(value)).filter((entry): entry is [string, number] => finite(entry[1])));
}
export function validTiming(values: Record<string, number>, fields: JsonRecord): boolean {
  const keys = Object.keys(fields);
  return keys.length > 0 && keys.length === Object.keys(values).length && keys.every(key => {
    const field = record(fields[key]), value = values[key];
    return Number.isInteger(value) && finite(field.minimum) && finite(field.maximum) && value >= field.minimum && value <= field.maximum && finite(field.step) && (value-field.minimum)%field.step===0;
  });
}
