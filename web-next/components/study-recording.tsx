import {Circle,Square,Pause,Camera} from "lucide-react";
import {record} from "../lib/model";
import {getApiBaseUrl} from "@/lib/env";
import {SAMPLES_BASE,videoErrorKey} from "../lib/samples";
import {SamplesBrowser} from "./samples-browser";
import type {Translator} from "../lib/i18n";
import type {RiderClient} from "../lib/use-rider";
import type {StudyRecording} from "../lib/use-study-recording";
import {Button} from "./primitives";
import {AutomaticCapture} from "./automatic-capture";
import styles from "../rider.module.css";

export function StudyRecordingBar({recording,client,t,language="pl"}:{recording:StudyRecording;client:RiderClient;t:Translator;language?:string}){
  const {capture}=recording;
  const rawImageError=record(record(client.status?.loop_timing).study_sampling).image_error;
  const imageError=typeof rawImageError==="string"?rawImageError:null;
  const active=capture.session?.state==="collecting";
  const video=capture.session?.video;
  const photoPending=["submitting","queued","running","unknown"].includes(capture.outcome?.state??"");
  const retired=active&&record(capture.session?.evidence).retired===true;
  return <div className={styles.recordingBar} data-testid="study-recording-bar" role="group" aria-label={t("study.global")}>
    <strong className={styles.recordingState} role="status"><span data-testid="study-recording-state">{capture.error?<span role="alert" title={capture.error}>{t("samples.failure")} · {capture.session?.session_id.slice(0,8)} · {capture.error}</span>:<>{t(retired?"study.retiredShort":active?"study.activeShort":"study.idleShort")}{capture.session&&<span> · {capture.session.session_id.slice(0,8)}</span>}</>}</span>{video&&<span data-testid="study-video-status" title={video.error?t(videoErrorKey(video.error)):t("study.videoStats",{frames:video.frames,seconds:video.duration_s.toFixed(1),mib:(video.bytes/1024/1024).toFixed(1),fps:video.target_fps,gaps:video.gaps})}> · {t("study.video")}: {t("study.video."+video.state)} · {video.frames} {t("study.videoFrames")}</span>}</strong>
    <div className={styles.recordingActions}>
      <Button data-testid="study-record" aria-label={t("study.record")} title={t("study.globalHint")} disabled={!client.canMutate||capture.busy||active&&!retired} onClick={()=>void capture.start("manual")}><Circle size={13}/>{t("study.recordShort")}</Button>
      <Button data-testid="study-photo" disabled={!active||retired||photoPending||capture.busy||!client.camera?.decoded||!client.camera?.captureKey} onClick={()=>void capture.save(client.camera)}><Camera size={13}/>{t("cockpitPhoto")}</Button>
      <span className={styles.small} data-testid="study-image-count">{t("study.images")} {capture.session?.counts.saved??0}</span>
      <Button data-testid="study-end" aria-label={t("study.end")} disabled={!active||capture.busy} onClick={()=>void capture.end()}><Square size={13}/>{t("study.endShort")}</Button>
      <Button data-testid="rider-pause" tone="caution" disabled={!client.canMutate} title={t("study.pauseHint")} onClick={()=>void client.stopManual().catch(()=>client.setNotice({key:"requestFailed",tone:"error"}))}><Pause size={14}/>{t("study.pause")}</Button>
    </div>
    <details className={styles.sampleDetails}><summary>{t("cockpitCaptureDetails")}</summary>
      <p className={styles.hint}>{t("study.globalHint")}</p>
      {video&&<div data-testid="study-video-details" role="status">
        <strong>{t("study.video")}: {t("study.video."+video.state)}</strong>
        <p className={styles.small}>{t("study.videoStats",{frames:video.frames,seconds:video.duration_s.toFixed(1),mib:(video.bytes/1024/1024).toFixed(1),fps:video.target_fps,gaps:video.gaps})}</p>
        {video.error&&<p role="alert">{t(videoErrorKey(video.error))}</p>}
        {!!video.frames&&video.ended_at!==null&&<a className={styles.button} href={getApiBaseUrl()+SAMPLES_BASE+"/sessions/"+capture.session!.session_id+"/video"} download>{t("study.downloadVideo")}</a>}
      </div>}
      <SamplesBrowser t={t} language={language} refreshKey={capture.session?capture.session.session_id+":"+capture.session.state:""}/>
      <AutomaticCapture collection={active?capture.session:null} sourceSession={client.config?.session??null} t={t}/>
      {imageError&&<p role="alert">{t("samples.failure")} · {String(imageError)}</p>}
      {!!capture.session?.counts.failed&&<p role="alert">{t("samples.failure")} · {capture.session.counts.failed}</p>}
      {capture.outcome&&<p role="status">{t("samples.request."+capture.outcome.state)}</p>}
      {capture.outcome?.state==="unknown"&&<><Button onClick={()=>void capture.check()}>{t("samples.check")}</Button><Button onClick={capture.dismiss}>{t("samples.dismiss")}</Button></>}
    </details>
  </div>;
}
