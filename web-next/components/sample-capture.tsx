import { AutomaticCapture } from "./automatic-capture";
import { Camera, CircleStop } from "lucide-react";
import { captureIntent, historicalFrame } from "../lib/samples";
import { useSamples } from "../lib/use-samples";
import type { Frame } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Pill } from "./primitives";
import styles from "../rider.module.css";

export function SampleCapture({sourceSession,deviceId,frame,visible,t}:{sourceSession:string|null;deviceId:string|null;frame:Frame|null;visible:boolean;t:Translator}) {
  const data=useSamples(deviceId,visible);
  const active=data.session?.state==="collecting";
  const pending=["submitting","queued","running","unknown"].includes(data.outcome?.state??"");
  const canSave=!!captureIntent(data.session,frame,"preview");
  const historical=historicalFrame(frame);
  return <div className={styles.sampleCapture} data-testid="sample-capture">
    <div className={styles.flex}><strong>{t("samples.capture")}</strong>
      {data.session&&<Pill tone={active?"good":undefined}>{t("samples.session."+data.session.state)} · {data.session.counts.saved??0} {t("samples.savedCount")}</Pill>}
    </div>
    <p className={styles.hint}>{t("samples.captureHint")}</p>
    <div className={styles.flex}>
      {!active?<><Button data-testid="samples-start" disabled={!deviceId||!visible||data.busy} onClick={()=>void data.start("manual")}>{t("samples.start")}</Button>
      <Button data-testid="samples-sequence" disabled={!deviceId||!visible||data.busy} onClick={()=>void data.start("sequence")}>{t("samples.sequence")}</Button></>:<>
      <Button data-testid="samples-save" tone="primary" disabled={!canSave||pending||data.busy} onClick={()=>void data.save(frame)}><Camera size={15}/>{t(historical?"samples.saveHistorical":"samples.save")}</Button>
      <Button data-testid="samples-end" disabled={data.busy} onClick={()=>void data.end()}><CircleStop size={15}/>{t("samples.end")}</Button></>}
    </div>
    {!visible&&<p className={styles.hint}>{t("samples.previewRequired")}</p>}
    {active&&!canSave&&<p className={styles.hint}>{t("samples.frameUnavailable")}</p>}
    {data.outcome&&<div className={styles.meta} aria-live="polite" data-testid="samples-result">
      <strong>{t("samples.request."+data.outcome.state)}</strong><code>{data.outcome.id}</code>
      {data.outcome.state==="unknown"&&<><Button onClick={()=>void data.check()}>{t("samples.check")}</Button><Button onClick={data.dismiss}>{t("samples.dismiss")}</Button></>}
    </div>}
    <AutomaticCapture collection={active?data.session:null} sourceSession={sourceSession} t={t}/>
    {data.error&&<p role="alert" className={styles.hint}>{t("samples.failure")} · <code>{data.error}</code></p>}
  </div>;
}
