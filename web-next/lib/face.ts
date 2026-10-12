import { finite, record, type Gesture, type JsonRecord } from "./model";
export function gestureFrom(value: unknown): Gesture {
  const row = record(value);
  return { ...(typeof row.id === "string" ? { id: row.id } : {}), builtin: row.builtin === true, name: typeof row.name === "string" ? row.name : "", expr: typeof row.expr === "string" ? row.expr : "neutral", duration: Number(row.duration ?? 1), contract_version: Number(row.contract_version ?? 1), initial: { ...record(row.initial) } as Record<string, number>, tracks: Object.fromEntries(Object.entries(record(row.tracks)).map(([key, segments]) => [key, Array.isArray(segments) ? segments.map(segment => ({ ...record(segment) })) : []])) as Gesture["tracks"] };
}
export function gestureBody(gesture: Gesture, copy = false): JsonRecord {
  const { id, ...bodyWithBuiltin } = gesture;
  const body: JsonRecord = { ...bodyWithBuiltin }; delete body.builtin;
  return { ...body, ...(!copy && !gesture.builtin && id ? { id } : {}) };
}
export function validGesture(gesture: Gesture, library: JsonRecord): boolean {
  const descriptors = record(library.descriptors), duration = record(descriptors.duration), fields = record(library.fields ?? descriptors.fields);
  const expressions = Array.isArray(descriptors.expressions) ? descriptors.expressions : [];
  if (!gesture.name.trim() || gesture.name.trim().length > 60 || !finite(gesture.duration) || gesture.duration * 1000 < Number(duration.min) || gesture.duration * 1000 > Number(duration.max) || !expressions.includes(gesture.expr)) return false;
  const validValue = (field: string, value: unknown) => { const bounds = fields[field]; return Array.isArray(bounds) && finite(value) && value >= Number(bounds[0]) && value <= Number(bounds[1]); };
  for (const [field, value] of Object.entries(gesture.initial)) if (!validValue(field, value)) return false;
  for (const [field, segments] of Object.entries(gesture.tracks)) {
    if (!(field in fields) || segments.length > 32) return false;
    let end = 0;
    for (const segment of segments) {
      if (![segment.t0, segment.t1].every(finite) || segment.t0 < end || segment.t1 <= segment.t0 || segment.t1 > gesture.duration || !validValue(field, segment.v0) || !validValue(field, segment.v1) || !["lin", "in", "out", "in_out"].includes(segment.ease)) return false;
      end = segment.t1;
    }
  }
  return true;
}
export function validFaceSettings(settings: JsonRecord, descriptors: JsonRecord, library: JsonRecord): boolean {
  const choices = (key: string) => Array.isArray(descriptors[key]) ? (descriptors[key] as unknown[]).map(item => record(item).id) : [];
  const intensity = record(descriptors.intensity), tempo = record(descriptors.tempo);
  const normalized = (value: unknown, range: JsonRecord) => finite(value) && finite(range.min) && finite(range.max) && value * 100 >= range.min && value * 100 <= range.max;
  const gestures = Array.isArray(library.gestures) ? library.gestures.map(item => record(item).id) : [];
  return choices("styles").includes(settings.style) && choices("skins").includes(settings.skin)
    && normalized(settings.intensity, intensity) && normalized(settings.tempo, tempo)
    && Array.isArray(descriptors.transitions) && descriptors.transitions.includes(settings.transition)
    && typeof settings.idle === "boolean" && ["battery", "cable"].includes(String(settings.power_mode))
    && Array.isArray(settings.active_gestures) && settings.active_gestures.every(id => gestures.includes(id));
}
