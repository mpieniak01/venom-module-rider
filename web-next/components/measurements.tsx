import {useEffect,useState} from "react";
import {systemAge,systemHistory} from "../lib/system-history";
import {axisNumber} from "../lib/motion-timeline";
import { Activity } from "lucide-react";
import { data, finite, formatNumber, formatTime, record, rows, sensorRows, str, telemetryAge, type JsonRecord, type RiderStatus } from "../lib/model";
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
  return <><div className={styles.meta} style={{ marginBottom: 14, marginTop: 0 }}><span>{t("telemetryAge")}: {formatNumber(age, language)} {age !== null ? "s" : ""}</span><Pill tone={sharedQuality === "fresh" ? "good" : "warning"}>{t(sharedQuality)}</Pill></div><div className={styles.metrics}>{samples.map(({ name, sample, value, quality, ratio }) => <Metric key={name} label={axisNumber(name)?t("motion.axis",{axis:axisNumber(name)}):t(name)} value={value} unit={ratio ? "%" : sample.unit === "sdk_raw" ? t("sdkRaw") : str(sample.unit)} quality={quality !== sharedQuality && quality !== "fresh" ? quality : undefined} language={language} t={t} />)}</div><Details value={{ source: observation.source ?? xgo.source, observed_at: observation.observed_at ?? xgo.observed_at, sensors: Object.fromEntries(sensors) }} t={t} /></>;
}
const series = [{ key: "cpu", label: "cpu", color: "var(--primary, #00ff9d)" }, { key: "peak", label: "cpuPeak", color: "var(--warning, #ffcc00)" }, { key: "mem", label: "memory", color: "var(--secondary, #00b8ff)" }] as const;
export function SystemChart({ system, t, language, now=0 }: { system: JsonRecord; t: Translator; language: string; now?:number }) {
  const received=finite(system.client_received_at_ms)?system.client_received_at_ms:now;
  const wall=finite(system.server_time)?system.server_time+Math.max(0,(now-received)/1000):0;
  const points=systemHistory(system,now,wall);
  if(points.length<2)return <Empty><Activity size={26} />{t("noHistory")}</Empty>;
  const plot=(key:"cpu"|"mem"|"peak")=>{
    let path="",pen=false;
    for(const point of points){
      const value=point[key];
      if(!finite(value)){pen=false;continue;}
      if(point.separated)pen=false;
      path+=`${pen?"L":"M"}${42+(1-point.age/60)*536},${170-Math.max(0,Math.min(100,value))*1.5} `;pen=true;
    }
    return path;
  };
  return <><svg className={styles.chart} viewBox="0 0 600 210" role="img" aria-label={t("systemHistory")}><title>{t("systemHistory")}</title>{[0,50,100].map(value=><g key={value}><line className={styles.chartGrid} x1="42" x2="578" y1={170-value*1.5} y2={170-value*1.5}/><text x="34" y={174-value*1.5} textAnchor="end" fill="currentColor" fontSize="10">{formatNumber(value,language,0)}%</text></g>)}{series.map(item=><path key={item.key} d={plot(item.key)} fill="none" stroke={item.color} strokeWidth="2"/>)}<text x="42" y="198" fill="currentColor" fontSize="11">−60 s</text><text x="578" y="198" textAnchor="end" fill="currentColor" fontSize="11">0 s</text></svg><div className={styles.legend}>{series.map(item=><span key={item.key}><i className={styles.legendDot} style={{background:item.color}}/>{t(item.label)}</span>)}</div></>;
}
export function RequestHistory({ status, t, language }: { status: RiderStatus | null; t: Translator; language: string }) {
  const history = data(status?.history);
  const requests = rows(history.items);
  return <Panel title={t("history")} hint={t("historyReadOnly")}>{requests.length ? <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>{t("time")}</th><th>{t("request")}</th><th>{t("acknowledgement")}</th></tr></thead><tbody>{requests.map((item, index) => { const command = record(item.command ?? item.request ?? item); const ack = record(item.ack); return <tr key={str(command.request_id) || index}><td>{formatTime(command.ts ?? item.ts ?? item.observed_at, language)}</td><td>{t(str(command.action))} {command.direction ? `· ${t(str(command.direction))}` : ""}<div className={styles.small}>{str(command.request_id)}</div></td><td>{str(ack.status ?? item.status ?? item.result) || "—"}<div className={styles.small}>{str(ack.error ?? item.error)}</div></td></tr>; })}</tbody></table></div> : <Empty>{t("noRequests")}</Empty>}<Details value={history} t={t} /></Panel>;
}
export function Measurements({ status, t, language }: { status: RiderStatus | null; t: Translator; language: string }) {
  const [now,setNow]=useState(()=>performance.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(performance.now()),1000);return()=>clearInterval(timer);},[]);
  const system = data(status?.system),age=systemAge(system,now),observation=record(system.observation);
  const known=age===null||age<=10;
  const fresh=known&&system.stale!==true&&!system.read_error&&observation.state!=="error";
  const metric=(key:string)=>fresh?system[key]:null;
  return <div className={styles.stack}><Panel title={t("measurements")} hint={t("measurementsHint")}><SensorMetrics status={status} t={t} language={language} /></Panel><div className={styles.grid}><Panel title={t("systemHistory")} hint={t("historyHint")}><p className={styles.hint}>{t(system.read_error?"systemReadFailed":observation.state==="error"?"systemSamplerFailed":!fresh?"stale":observation.state==="warming"?"systemWarming":"fresh")}{age!==null&&<> · {formatNumber(age,language,1)} s</>}{str(system.history_event)&&<> · {t(str(system.history_event))}</>}</p><SystemChart system={system} t={t} language={language} now={now} /></Panel><Panel title={t("device")}><div className={styles.metrics}><Metric label={t("cpu")} value={metric("cpu_pct")} unit="%" language={language} t={t} /><Metric label={t("memory")} value={metric("mem_pct")} unit="%" language={language} t={t} /><Metric label={t("temperature")} value={metric("temp_c")} unit="°C" language={language} t={t} /><Metric label={t("disk")} value={metric("disk_pct")} unit="%" language={language} t={t} /></div><Details value={{ health: status?.health, system, telemetry: status?.telemetry }} t={t} /></Panel></div><RequestHistory status={status} t={t} language={language} /></div>;
}