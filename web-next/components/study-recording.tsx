import {Circle,Square,Pause} from "lucide-react";
import {record} from "../lib/model";
import type {Translator} from "../lib/i18n";
import type {RiderClient} from "../lib/use-rider";
import type {StudyRecording} from "../lib/use-study-recording";
import {Button} from "./primitives";
import styles from "../rider.module.css";

export function StudyRecordingBar({recording,client,t}:{recording:StudyRecording;client:RiderClient;t:Translator}){
  const {capture}=recording;
  const active=capture.session?.state==="collecting";
  const retired=active&&record(capture.session?.evidence).retired===true;
  return <div className={styles.recordingBar} data-testid="study-recording-bar" role="group" aria-label={t("study.global")}>
    <strong className={styles.recordingState} role="status" data-testid="study-recording-state">{t(retired?"study.retiredShort":active?"study.activeShort":"study.idleShort")}{capture.session&&<span> · {capture.session.session_id.slice(0,8)}</span>}</strong>
    <div className={styles.recordingActions}>
      <Button data-testid="study-record" aria-label={t("study.record")} title={t("study.globalHint")} disabled={!client.canMutate||capture.busy||active&&!retired} onClick={()=>void capture.start("manual")}><Circle size={13}/>{t("study.recordShort")}</Button>
      <Button data-testid="study-end" aria-label={t("study.end")} disabled={!active||capture.busy} onClick={()=>void capture.end()}><Square size={13}/>{t("study.endShort")}</Button>
      <Button data-testid="rider-pause" tone="caution" disabled={!client.canMutate} title={t("study.pauseHint")} onClick={()=>void client.stopManual().catch(()=>client.setNotice({key:"requestFailed",tone:"error"}))}><Pause size={14}/>{t("study.pause")}</Button>
    </div>
    {capture.error&&<span role="alert" className={styles.recordingError}>{t("samples.failure")} · {capture.error}</span>}
  </div>;
}
