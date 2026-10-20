import { useState } from "react";
import { record, finite, formatNumber, type JsonRecord } from "../lib/model";
import { timingValues, validTiming } from "../lib/loop-timing";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Field, Input, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";

export function LoopTimingPanel({client,t,language}:{client:RiderClient;t:Translator;language:string}) {
  const current = record(client.status?.loop_timing);
  const fields = record(current.fields), effective = timingValues(current.effective);
  const revision = Number(current.revision);
  const [draft,setDraft] = useState<{revision:number;values:Record<string,number>}|null>(null);
  const [saving,setSaving] = useState(false);
  const values = draft?.values ?? effective;
  const conflict = !!draft && draft.revision !== revision;
  const valid = validTiming(values,fields);
  const save = async () => {
    if(!draft || !valid || conflict || saving) return;
    setSaving(true);
    try {
      const result = await client.mutate("/loop-timing",{revision:draft.revision,values:draft.values},"PUT");
      if(result) setDraft(null);
    } finally { setSaving(false); }
  };
  const reads = record(current.reads);
  return <Panel title={t("timing.title")} hint={t("timing.hint")} extra={<Pill>{t("revision")} {finite(revision)?revision:"—"}</Pill>}>
    <div data-testid="loop-timing-panel">
      {current.warning ? <p role="alert">{t("timing.restoreFailed")}</p> : null}
      <p className={styles.hint}>{t(current.persistent ? "timing.persistent" : "timing.temporary")}</p>
      <div className={styles.grid3}>{Object.entries(fields).map(([key,value])=>{
        const field=record(value);
        return <Field key={key} label={t("timing."+key)+" · ms"}>
          <Input style={{width:"12ch",maxWidth:"100%"}} data-testid={"timing-"+key} type="number" min={Number(field.minimum)} max={Number(field.maximum)} step={Number(field.step)} value={finite(values[key])?values[key]:""} disabled={saving} onChange={event=>setDraft({revision:draft?.revision??revision,values:{...values,[key]:event.target.value===""?Number.NaN:Number(event.target.value)}})}/>
          <span>{t("timing.effective")}: {effective[key]} ms · {formatNumber(1000/effective[key],language,2)} Hz</span>
        </Field>;
      })}</div>
      {conflict&&<p role="alert">{t("conflict")}</p>}
      <div className={styles.flex} style={{marginTop:16}}>
        <Button disabled={!draft||saving} onClick={()=>setDraft(null)}>{t("resetDraft")}</Button>
        <Button tone="primary" data-testid="timing-save" disabled={!draft||!valid||conflict||saving||!client.canMutate} onClick={()=>void save()}>{t("save")}</Button>
      </div>
      <p className={styles.hint}>{t("timing.limits")}</p>
      <details className={styles.details}><summary>{t("timing.diagnostics")}</summary>
        <p className={styles.hint}>{t("timing.metricsHint")}</p>
        <div className={styles.tableWrap}><table className={styles.table}><thead><tr>
          {["resource","requests","cache","shared","errors","duration","gaps"].map(key=><th key={key}>{t("timing."+key)}</th>)}
        </tr></thead><tbody>{Object.entries(reads).map(([name,value])=>{
          const row=record(value),duration=record(row.duration_ms),gaps=record(row.start_gaps_ms);
          const summary=(stats:JsonRecord)=>["p50","p95","max"].map(key=>formatNumber(stats[key],language,1)).join(" / ");
          return <tr key={name}><td>{name}</td><td>{String(row.requests??0)}</td><td>{String(row.cache_hits??0)}</td><td>{String(row.coalesced??0)}</td><td>{String(row.errors??0)}</td><td>{summary(duration)}</td><td>{summary(gaps)}</td></tr>;
        })}</tbody></table></div>
      </details>
    </div>
  </Panel>;
}
