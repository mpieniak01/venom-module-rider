import {Circle,Square,Pause,Camera} from "lucide-react";
import {record} from "../lib/model";
import type {Translator} from "../lib/i18n";
import type {RiderClient} from "../lib/use-rider";
import type {StudyRecording} from "../lib/use-study-recording";
import {Button} from "./primitives";
import {AutomaticCapture} from "./automatic-capture";
import styles from "../rider.module.css";

export function StudyRecordingBar({recording,client,t}:{recording:StudyRecording;client:RiderClient;t:Translator}){
  const {capture}=recording;
  const rawImageError=record(record(client.status?.loop_timing).study_sampling).image_error;
  const imageError=typeof rawImageError==="string"?rawImageError:null;
  const active=capture.session?.state==="collecting";
  const photoPending=["submitting","queued","running","unknown"].includes(capture.outcome?.state??"");
  const retired=active&&record(capture.session?.evidence).retired===true;
  return <div className={styles.recordingBar} data-testid="study-recording-bar" role="group" aria-label={t("study.global")}>
    <strong className={styles.recordingState} role="status" data-testid="study-recording-state">{capture.error?<span role="alert" title={capture.error}>{t("samples.failure")} · {capture.session?.session_id.slice(0,8)} · {capture.error}</span>:<>{t(retired?"study.retiredShort":active?"study.activeShort":"study.idleShort")}{capture.session&&<span> · {capture.session.session_id.slice(0,8)}</span>}</>}</strong>
    <div className={styles.recordingActions}>
      <Button data-testid="study-record" aria-label={t("study.record")} title={t("study.globalHint")} disabled={!client.canMutate||capture.busy||active&&!retired} onClick={()=>void capture.start("manual")}><Circle size={13}/>{t("study.recordShort")}</Button>
      <Button data-testid="study-photo" disabled={!active||retired||photoPending||capture.busy||!client.camera?.decoded||!client.camera?.captureKey} onClick={()=>void capture.save(client.camera)}><Camera size={13}/>{t("cockpitPhoto")}</Button>
      <span className={styles.small} data-testid="study-image-count">{t("study.images")} {capture.session?.counts.saved??0}</span>
      <Button data-testid="study-end" aria-label={t("study.end")} disabled={!active||capture.busy} onClick={()=>void capture.end()}><Square size={13}/>{t("study.endShort")}</Button>
      <Button data-testid="rider-pause" tone="caution" disabled={!client.canMutate} title={t("study.pauseHint")} onClick={()=>void client.stopManual().catch(()=>client.setNotice({key:"requestFailed",tone:"error"}))}><Pause size={14}/>{t("study.pause")}</Button>
    </div>
    <details className={styles.sampleDetails}><summary>{t("cockpitCaptureDetails")}</summary>
      <p className={styles.hint}>{t("study.globalHint")}</p>
      <AutomaticCapture collection={active?capture.session:null} sourceSession={client.config?.session??null} t={t}/>
      {imageError&&<p role="alert">{t("samples.failure")} · {String(imageError)}</p>}
      {!!capture.session?.counts.failed&&<p role="alert">{t("samples.failure")} · {capture.session.counts.failed}</p>}
      {capture.outcome&&<p role="status">{t("samples.request."+capture.outcome.state)}</p>}
      {capture.outcome?.state==="unknown"&&<><Button onClick={()=>void capture.check()}>{t("samples.check")}</Button><Button onClick={capture.dismiss}>{t("samples.dismiss")}</Button></>}
    </details>
  </div>;
}
