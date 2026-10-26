import {formatNumber,record} from "../lib/model";
import type {PulseRun} from "../lib/pulse-trials";
import type {Translator} from "../lib/i18n";
import styles from "../rider.module.css";

export function PulseSeriesComparison({a,b,t,language}:{a:PulseRun|null;b:PulseRun|null;t:Translator;language:string}){
 if(!a&&!b)return null;
 return <section className={styles.pulseComparison} data-testid="pulse-series-comparison">
  {a&&b&&<p>{t("pulse.common")}: <strong data-testid="pulse-group-match">{t(a.group_id===b.group_id?"pulse.same":"pulse.different")}</strong></p>}
  {([["A",a],["B",b]] as const).map(([label,run])=>run&&<div key={label} data-testid={"pulse-series-"+label}>
   <strong>{label} · {t("pulse.series")} {run.profile_id} · {t("pulse.phase."+run.phase)}</strong>
   <p>{run.completed_pulses}/{run.count} × {run.pulse_ms} ms · {t("pulse.actual")}: {formatNumber(run.elapsed_ms,language,1)} ms · {run.reason}</p>
   <p className={styles.small}>{t("speed")}: {String(record(run.common.parameters).speed??"—")} · {t("linear_rate")}: {String(record(run.common.ramps).linear_rate??"—")} · {t("linear_deceleration")}: {String(record(run.common.ramps).linear_deceleration??"—")}</p>
   <svg viewBox="0 0 600 46" role="img" aria-label={t("pulse.series")+" "+run.profile_id}>
    <line x1="0" x2="600" y1="32" y2="32" stroke="currentColor"/>
    {run.events.filter(e=>e.kind==="pulse_started").map(start=>{
     const end=run.events.find(e=>e.kind==="pulse_zero"&&e.pulse_index===start.pulse_index),scale=600/Math.max(1,run.elapsed_ms),width=Math.max(0,((end?.elapsed_ms??run.elapsed_ms)-start.elapsed_ms)*scale);
     return <g key={start.pulse_index}><rect x={start.elapsed_ms*scale} y="6" width={width} height="25" fill={end?"#10b981":"#eab308"}/><text x={start.elapsed_ms*scale+3} y="23" fill="#07111b" fontSize="13">{start.pulse_index}</text></g>;
    })}
    <text x="0" y="45" fill="currentColor" fontSize="10">0 ms</text><text x="600" y="45" textAnchor="end" fill="currentColor" fontSize="10">{run.elapsed_ms} ms</text>
   </svg>
  </div>)}
  <p className={styles.small}>{t("pulse.clock")}</p>
 </section>;
}
