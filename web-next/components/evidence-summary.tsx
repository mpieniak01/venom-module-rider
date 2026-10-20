import { record, str, type JsonRecord } from "../lib/model";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function EvidenceSummary({value,t}:{value:unknown;t:Translator}) {
  const row:JsonRecord=record(value);
  if(!Object.keys(row).length)return null;
  const limits=record(row.limits);
  return <details className={styles.details} data-testid="evidence-summary">
    <summary>{t("evidence.title")} · {Number(row.saved??0)}</summary>
    <p className={styles.hint}>{t("evidence.counts",{saved:Number(row.saved??0),dropped:Number(row.dropped??0),failed:Number(row.failed??0),bytes:Number(row.bytes??0)})}</p>
    <p className={styles.hint}>{t("evidence.limits",{records:Number(limits.session_records??0),bytes:Number(limits.session_bytes??0)})}</p>
    <p className={styles.hint}>{t(row.retired===true?"evidence.retired":Number(row.saved??0)>0?"evidence.association":"evidence.waiting")}</p>
    {row.retired===true&&str(row.retired_reason)&&<code>{str(row.retired_reason)}</code>}
  </details>;
}
