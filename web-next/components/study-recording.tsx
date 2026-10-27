import {Circle,Square} from "lucide-react";
import {videoErrorKey} from "../lib/samples";
import type {Translator} from "../lib/i18n";
import type {RiderClient} from "../lib/use-rider";
import type {StudyRecording} from "../lib/use-study-recording";
import {Button} from "./primitives";
import styles from "../rider.module.css";

export function StudyRecordingBar({recording,client,t,actions=true}:{recording:StudyRecording;client:RiderClient;t:Translator;actions?:boolean}){
  const {capture}=recording;
  const active=capture.session?.state==="collecting";
  const video=capture.session?.video;
  const retired=active&&capture.session?.evidence?.retired===true;
  return <div className={styles.recordingBar} data-testid="study-recording-bar" role="group" aria-label={t("study.global")}>
    <strong className={styles.recordingState} role="status"><span data-testid="study-recording-state">{capture.error?<span role="alert" title={capture.error}>{t("samples.failure")} · {capture.session?.session_id.slice(0,8)} · {capture.error}</span>:<>{t(retired?"study.retiredShort":active?"study.activeShort":"study.idleShort")}{capture.session&&<span> · {capture.session.session_id.slice(0,8)}</span>}</>}</span>{video&&<span data-testid="study-video-status" title={video.error?t(videoErrorKey(video.error)):t("study.videoStats",{frames:video.frames,seconds:video.duration_s.toFixed(1),mib:(video.bytes/1024/1024).toFixed(1),fps:video.target_fps,gaps:video.gaps})}> · {t("study.video")}: {t("study.video."+video.state)} · {video.frames} {t("study.videoFrames")}</span>}</strong>
    {actions&&<StudyRecordingActions recording={recording} client={client} t={t}/>}

  </div>;
}

export function StudyRecordingActions({recording,client,t}:{recording:StudyRecording;client:RiderClient;t:Translator}){
 const {capture}=recording,active=capture.session?.state==="collecting",retired=active&&capture.session?.evidence?.retired===true;
 return (    <div className={styles.recordingActions}>
      <Button size="large" data-testid="study-record" aria-label={t("study.record")} title={t("study.globalHint")} disabled={!client.canMutate||capture.busy||active&&!retired} onClick={()=>void capture.start("manual")}><Circle size={13}/>{t("study.recordShort")}</Button>
      <Button size="large" data-testid="study-end" aria-label={t("study.end")} disabled={!active||capture.busy} onClick={()=>void capture.end()}><Square size={13}/>{t("study.endShort")}</Button>
    </div>);
}
