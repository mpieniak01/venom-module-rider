import { Activity } from "lucide-react";
import { data, finite, formatNumber, formatTime, record, rows, sensorRows, str, telemetryAge, timestampMs, type JsonRecord, type RiderStatus } from "../lib/model";
import type { Translator } from "../lib/i18n";
import { Details, Empty, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";

function Metric({ label, value, unit, quality, time, source, language, t }: { label: string; value: unknown; unit?: string; quality?: string; time?: unknown; source?: unknown; language: string; t: Translator }) {
  return <div className={styles.metric}><div className={styles.metricLabel}>{label}</div><div className={styles.metricValue}>{formatNumber(value, language)}<span className={styles.metricUnit}>{unit}</span></div><div className={styles.metricMeta}>{quality && <span><Pill tone={quality === "fresh" ? "good" : "warning"}>{t(quality)}</Pill></span>}{time !== undefined && <span>{t("observedAt")}: {formatTime(time, language)}</span>}{typeof source === "string" && source && <span>{t("source")}: {source}</span>}</div></div>;
}
export function SensorMetrics({ status, t, language }: { status: RiderStatus | null; t: Translator; language: string }) {
  const sensors = sensorRows(status), telemetry = data(status?.telemetry), xgo = record(record(telemetry.devices).xgo), observation = record(xgo.observation);
  const samples = ["battery", "roll", "pitch", "yaw"].map(name => {
    const sample = sensors.find(([key]) => key === name)?.[1] ?? {};
    const raw = sample.value, ratio = name === "battery" && sample.unit === "ratio";
    const value = ratio && finite(raw) ? raw * 100 : raw;
    const quality = !finite(value) ? "missing" : sample.offline === true ? "offline" : sample.stale === true || sample.ready === false ? "stale" : sample.ready === true ? "fresh" : "unknown";
    return { name, sample, value, quality, ratio };
  });
  const sharedQuality = samples.every(sample => sample.quality === samples[0].quality) ? samples[0].quality : "partial";
  const age = telemetryAge(status);
  return <><div className={styles.meta} style={{ marginBottom: 14, marginTop: 0 }}><span>{t("telemetryAge")}: {formatNumber(age, language)} {age !== null ? "s" : ""}</span><Pill tone={sharedQuality === "fresh" ? "good" : "warning"}>{t(sharedQuality)}</Pill></div><div className={styles.metrics}>{samples.map(({ name, sample, value, quality, ratio }) => <Metric key={name} label={t(name)} value={value} unit={ratio ? "%" : sample.unit === "sdk_raw" ? t("sdkRaw") : str(sample.unit)} quality={quality !== sharedQuality && quality !== "fresh" ? quality : undefined} language={language} t={t} />)}</div><Details value={{ source: observation.source ?? xgo.source, observed_at: observation.observed_at ?? xgo.observed_at, sensors: Object.fromEntries(sensors) }} t={t} /></>;
}
const series = [{ key: "hist_cpu", label: "cpu", color: "var(--primary, #00ff9d)" }, { key: "hist_cpu_peak", label: "cpuPeak", color: "var(--warning, #ffcc00)" }, { key: "hist_mem", label: "memory", color: "var(--secondary, #00b8ff)" }];
export function SystemChart({ system, t, language }: { system: JsonRecord; t: Translator; language: string }) {
  const times = Array.isArray(system.hist_ts) ? system.hist_ts.map(timestampMs) : [];
  const validTimes = times.filter((time): time is number => time !== null);
  if (validTimes.length < 2) return <Empty><Activity size={26} />{t("noHistory")}</Empty>;
  const first = Math.min(...validTimes), last = Math.max(...validTimes);
  if (first === last) return <Empty>{t("noHistory")}</Empty>;
  const allValues = series.flatMap(item => Array.isArray(system[item.key]) ? (system[item.key] as unknown[]).filter(finite) : []);
  const max = Math.max(100, ...allValues);
  const plot = (key: string) => {
    const values = Array.isArray(system[key]) ? system[key] as unknown[] : [];
    let path = "", pen = false;
    for (let i = 0; i < times.length; i++) { const time = times[i], value = values[i]; if (time === null || !finite(value)) { pen = false; continue; } path += `${pen ? "L" : "M"}${42 + (time - first) / (last - first) * 536},${170 - value / max * 150} `; pen = true; }
    return path;
  };
  return <><svg className={styles.chart} viewBox="0 0 600 210" role="img" aria-label={t("systemHistory")}><title>{t("systemHistory")}</title>{[0, max / 2, max].map(value => <g key={value}><line className={styles.chartGrid} x1="42" x2="578" y1={170 - value / max * 150} y2={170 - value / max * 150} /><text x="34" y={174 - value / max * 150} textAnchor="end" fill="currentColor" fontSize="10">{formatNumber(value, language, 0)}%</text></g>)}{series.map(item => <path key={item.key} d={plot(item.key)} fill="none" stroke={item.color} strokeWidth="2" />)}<text x="42" y="198" fill="currentColor" fontSize="11">{formatTime(first, language)}</text><text x="578" y="198" textAnchor="end" fill="currentColor" fontSize="11">{formatTime(last, language)}</text></svg><div className={styles.legend}>{series.map(item => <span key={item.key}><i className={styles.legendDot} style={{ background: item.color }} />{t(item.label)}</span>)}</div></>;
}
export function RequestHistory({ status, t, language }: { status: RiderStatus | null; t: Translator; language: string }) {
  const history = data(status?.history);
  const requests = rows(history.items);
  return <Panel title={t("history")} hint={t("historyReadOnly")}>{requests.length ? <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>{t("time")}</th><th>{t("request")}</th><th>{t("acknowledgement")}</th></tr></thead><tbody>{requests.map((item, index) => { const command = record(item.command ?? item.request ?? item); const ack = record(item.ack); return <tr key={str(command.request_id) || index}><td>{formatTime(command.ts ?? item.ts ?? item.observed_at, language)}</td><td>{t(str(command.action))} {command.direction ? `· ${t(str(command.direction))}` : ""}<div className={styles.small}>{str(command.request_id)}</div></td><td>{str(ack.status ?? item.status ?? item.result) || "—"}<div className={styles.small}>{str(ack.error ?? item.error)}</div></td></tr>; })}</tbody></table></div> : <Empty>{t("noRequests")}</Empty>}<Details value={history} t={t} /></Panel>;
}
export function Measurements({ status, t, language }: { status: RiderStatus | null; t: Translator; language: string }) {
  const system = data(status?.system);
  return <div className={styles.stack}><Panel title={t("measurements")} hint={t("measurementsHint")}><SensorMetrics status={status} t={t} language={language} /></Panel><div className={styles.grid}><Panel title={t("systemHistory")} hint={t("historyHint")}><SystemChart system={system} t={t} language={language} /></Panel><Panel title={t("device")}><div className={styles.metrics}><Metric label={t("cpu")} value={system.cpu_pct} unit="%" language={language} t={t} /><Metric label={t("memory")} value={system.mem_pct} unit="%" language={language} t={t} /><Metric label={t("temperature")} value={system.temp_c} unit="°C" language={language} t={t} /><Metric label={t("disk")} value={system.disk_pct} unit="%" language={language} t={t} /></div><Details value={{ health: status?.health, system, telemetry: status?.telemetry }} t={t} /></Panel></div><RequestHistory status={status} t={t} language={language} /></div>;
}