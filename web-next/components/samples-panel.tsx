/* eslint-disable @next/next/no-img-element -- The preview uses the existing operator-selected camera stream. */
import {Camera} from "lucide-react";
import {getApiBaseUrl} from "@/lib/env";
import {record} from "../lib/model";
import {SAMPLES_BASE,videoErrorKey} from "../lib/samples";
import type {Translator} from "../lib/i18n";
import type {RiderClient} from "../lib/use-rider";
import type {StudyRecording} from "../lib/use-study-recording";
import {Button,Panel} from "./primitives";
import {AutomaticCapture} from "./automatic-capture";
import {SampleFacts} from "./sample-facts";
import {SamplesBrowser} from "./samples-browser";
import styles from "../rider.module.css";

export function SamplesPanel({recording,client,t,language}:{recording:StudyRecording;client:RiderClient;t:Translator;language:string}){
  const {capture}=recording,video=capture.session?.video;
  const active=capture.session?.state==="collecting",retired=capture.session?.evidence?.retired===true;
  const photoPending=["submitting","queued","running","unknown"].includes(capture.outcome?.state??"");
  const imageError=record(record(client.status?.loop_timing).study_sampling).image_error;
  const refreshKey=capture.session?capture.session.session_id+":"+capture.session.state:"";
  return <div className={styles.stack} data-testid="samples-panel">
    <SamplesBrowser t={t} language={language} refreshKey={refreshKey} standalone/>

    <Panel title={t("samples.currentSession")} hint={t("study.globalHint")}>
      <SampleFacts items={[{label:t("state"),value:active?t("study.activeShort"):t("study.idleShort")},{label:t("samples.session"),value:capture.session?.session_id.slice(0,8)??"—"},{label:t("samples.savedPhotos"),value:capture.session?.counts.saved??0}]}/>
      {capture.session&&<details className={styles.details}><summary>{t("details")}</summary><code className={styles.analysisHash}>{capture.session.session_id}</code></details>}
      {video&&<section data-testid="study-video-details" role="status">
        <SampleFacts items={[{label:t("study.video"),value:t("study.video."+video.state)},{label:t("analysis.metric.frames"),value:video.frames},{label:t("samples.duration"),value:video.duration_s.toFixed(1)+" s"}]}/>
        <details className={styles.details}><summary>{t("details")}</summary><p className={styles.hint}>{t("study.videoStats",{frames:video.frames,seconds:video.duration_s.toFixed(1),mib:(video.bytes/1024/1024).toFixed(1),fps:video.target_fps,gaps:video.gaps})}</p></details>
        {video.error&&<p role="alert">{t(videoErrorKey(video.error))}</p>}
        {!!video.frames&&video.ended_at!==null&&<a className={styles.button} href={getApiBaseUrl()+SAMPLES_BASE+"/sessions/"+capture.session!.session_id+"/video"} download>{t("study.downloadVideo")}</a>}
      </section>}
      <div className={styles.samplePhoto}>
        {client.camera&&<img data-testid="sample-photo-preview" className={styles.samplePhotoPreview} src={client.camera.url} alt={t("samples.photoPreview")} onLoad={e=>client.markCameraDecoded(e.currentTarget.currentSrc,true)} onError={e=>client.markCameraDecoded(e.currentTarget.currentSrc,false)}/>}
        <div><p className={styles.hint}>{t("samples.photoHint")}</p>
          <Button data-testid="study-photo" disabled={!active||retired||photoPending||capture.busy||!client.camera?.decoded||!client.camera?.captureKey} onClick={()=>void capture.save(client.camera)}><Camera size={15}/>{t("samples.saveFrame")}</Button>
          <p data-testid="study-image-count">{t("samples.savedPhotos")}: {capture.session?.counts.saved??0}</p>
        </div>
      </div>
      {typeof imageError==="string"&&imageError&&<p role="alert">{t("samples.failure")} · {String(imageError)}</p>}
      {!!capture.session?.counts.failed&&<p role="alert">{t("samples.failure")} · {capture.session.counts.failed}</p>}
      {capture.outcome&&<p role="status">{t("samples.request."+capture.outcome.state)}</p>}
      {capture.outcome?.state==="unknown"&&<><Button onClick={()=>void capture.check()}>{t("samples.check")}</Button><Button onClick={capture.dismiss}>{t("samples.dismiss")}</Button></>}
      <details className={styles.details}><summary>{t("samples.captureSettings")}</summary><AutomaticCapture collection={active?capture.session:null} sourceSession={client.config?.session??null} t={t}/></details>
    </Panel>
  </div>;
}
