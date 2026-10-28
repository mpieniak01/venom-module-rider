import { data, finite, record, sensorRows, str, timestampMs, type JsonRecord, type RiderStatus } from "./model";

export type OperatorSample = { value: number | null; unit: string; quality: "fresh" | "stale" | "missing" | "unknown" };
/** Determine freshness independently of unit normalization and battery validation. */
function sampleQuality(sample:JsonRecord,connected:boolean,now:number|null):OperatorSample["quality"] {
    const observed = timestampMs(sample.observed_at);
    const age = observed !== null && now !== null ? Math.max(0, (now - observed) / 1000) : sample.age_s;
    const stale = !connected || sample.offline === true || sample.stale === true || sample.ready === false
      || finite(age) && finite(sample.max_age_s) && age > sample.max_age_s;
    const quality = stale ? "stale" : sample.ready === true ? "fresh" : "unknown";
    return quality;
}
/** Preserve Pi units and distinguish a missing/stale sample from a measured zero. */
export function operatorTelemetry(status: RiderStatus | null) {
  const samples = Object.fromEntries(sensorRows(status));
  const clocks = [data(status?.control).server_time, data(status?.telemetry).server_time]
    .map(timestampMs).filter((time): time is number => time !== null);
  const now = clocks.length ? Math.max(...clocks) : null;
  const connected = !!status && ["online", "degraded"].includes(status.connection.status) && !status.errors.telemetry;
  /** Read one sample without converting missing or stale telemetry to zero. */
  const read = (name: string): OperatorSample => {
    const sample = record(samples[name]), raw = sample.value, unit = str(sample.unit);
    const ratio = name === "battery" && unit === "ratio";
    const percent = name === "battery" && ["%", "percent", "pct"].includes(unit);
    const value = finite(raw) ? ratio ? raw * 100 : raw : null;
    if (value === null || name === "battery" && (!ratio && !percent || value < 0 || value > 100))
      return { value: null, unit: name === "battery" ? "%" : unit, quality: "missing" };
    const quality=sampleQuality(sample,connected,now);
    return { value: quality === "fresh" ? value : null, unit: ratio || percent ? "%" : unit, quality };
  };
  const battery = read("battery"), axes = ["roll", "pitch", "yaw"].map(read);
  const all = [battery, ...axes];
  const quality = all.every(sample => sample.quality === "fresh") ? "fresh"
    : all.some(sample => sample.quality === "fresh") ? "partial"
    : all.some(sample => sample.quality === "stale") ? "stale" : "missing";
  const units = [...new Set(axes.map(axis => axis.unit).filter(Boolean))];
  return { battery, axes, quality, unit: units.length === 1 ? units[0] : "" };
}
