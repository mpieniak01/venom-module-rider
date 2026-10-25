import { slowLeaseFactor, temporalView } from "../lib/behavior";
import { NavigationTemporal } from "./navigation-temporal";
import { frameAge } from "../lib/vision-frame";
import type { Frame } from "../lib/use-rider";
import { decisionView, diagnosticImageCurrent } from "../lib/loop-controls";
import { formatNumber, formatTime, record, safetyOf, type JsonRecord, type RiderStatus } from "../lib/model";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function LoopDecision({frame,state,detector,policy,status,t,language}:{frame?:Frame|null;state:JsonRecord;detector:JsonRecord;policy:JsonRecord|null;status:RiderStatus|null;t:Translator;language:string}) {
  const view=decisionView(state,detector), safety=safetyOf(status);
  const sameRun=frame?.analysis?.run_id===state.run_id&&frame?.analysis?.config_revision===state.config_revision;
  const age=frameAge(frame??null);
  const previewOnly=state.running!==true&&diagnosticImageCurrent(frame??null,policy,detector);
  const imageState=frame?.analysis ? {running: (sameRun||previewOnly)&&!frame.stale&&age!==null&&age<1000, stale:frame.stale, decision:frame.analysis} : state;
  const imageView=decisionView(imageState,detector);
  const decision=record(state.decision), key="vision.reason."+imageView.imageReason;
  const reason=t(key)===key?t("vision.reason.default"):t(key);
  const replyReasonKey="vision.reason."+view.reason;
  const replyReason=t(replyReasonKey)===replyReasonKey?t("vision.reason.default"):t(replyReasonKey);
  const classId=typeof decision.dominant_class_id==="number"?decision.dominant_class_id:null;
  const classKey="vision.class."+String(classId),className=t(classKey)===classKey?t("visionClassOther",{id:String(classId)}):t(classKey);
  const geometry=decision.perception_engine==="depth_plane";
  const adaptive=decision.geometry==="ground_capsule";
  const moving=safety.moving===true;
  const slowFactor=slowLeaseFactor(state,policy,safety);
  const observed=typeof safety.moving==="boolean"&&safety.stale!==true&&!status?.errors.control;
  return <section className={styles.decisionArea} aria-label={t("decisionTitle")} data-testid="loop-decision">
    {classId!==null&&<p className={styles.modelClass} data-testid="vision-class">{t("visionClassInZone",{name:className,fraction:formatNumber(Number(decision.dominant_class_fraction)*100,language,1)})}{[6,11,28].includes(classId)&&<> {t("visionUnsupportedGround")}</>}</p>}
    <div className={styles.decisionGrid}>
      <div className={styles.decisionCard}><span>{t("decisionVision")}</span><strong data-testid="vision-verdict" className={imageView.vision==="go"?styles.verdictGo:styles.verdictStop}>{t("decision."+imageView.vision)}</strong><p data-testid="decision-reason">{imageView.vision==="go"?t("imageGoHint"):reason}</p>{view.current&&view.floor!==null&&<p>{t("confidentFloorCoverage",{value:formatNumber(view.floor*100,language,2)})}</p>}</div>
      <div className={styles.decisionCard}><span>{t("decisionReply")}</span><strong data-testid="pi-reply">{view.reply==="none"?"—":view.reply==="slow"?t("decisionSlow"):view.reply==="steer_left"?t("decisionSteerLeft"):view.reply==="steer_right"?t("decisionSteerRight"):view.reply==="turn_left"?t("decisionTurnLeft"):view.reply==="turn_right"?t("decisionTurnRight"):view.reply.toUpperCase()}</strong><p>{view.reply==="go"?t("decisionForward")+" · "+String(policy?.speed??"—")+" SDK · ≤ "+String(policy?.max_motion_ms??"—")+" ms":view.reply==="slow"?slowFactor!==null?t("decisionSlowLease",{speed:formatNumber(Number(policy?.speed)*slowFactor,language,3),factor:String(slowFactor),duration:String(policy?.max_motion_ms??"—")}):t("decisionSlowUnconfirmed"):view.current?replyReason:t("decisionNoReply")}</p></div>
      <div className={styles.decisionCard}><span>{t("decisionExecutor")}</span><strong data-testid="pi-motion">{t(observed?moving?"decisionMoving":"decisionStopped":"unknown")}</strong><p>{t("owner")}: {String(safety.owner??"—")}</p><p>{t("decisionReportedMotion")}</p></div>
    </div>
    <p className={styles.hint} data-testid="decision-timing">{t("decisionTiming",{sequence:String(decision.sequence??"—"),revision:String(decision.config_revision??state.config_revision??"—"),inference:String(decision.inference_ms??"—"),roundtrip:String(state.request_roundtrip_ms??"—"),age:String(state.decision_age_ms??"—"),remaining:String(state.permit_remaining_ms??"—")})}</p>
    <NavigationTemporal view={temporalView(state)} t={t}/>
    <details className={styles.details}><summary>{t("visionDiagnostics")}</summary>{geometry&&!adaptive&&<p className={styles.hint} data-testid="geometry-verdict">{t("perceptionGeometryHint")} · {t("perceptionDeviation")}: {formatNumber(Number(decision.outlier_fraction)*100,language,2)}% / {formatNumber(Number(decision.outlier_limit)*100,language,2)}%</p>}<div className={styles.decisionMetrics}>
      {!geometry&&<><span>{t("decisionFloor")}: <b>{view.floor===null?"—":formatNumber(view.floor*100,language,1)+"%"}</b> / 100%</span>
      <span>{t("decisionConfidence")}: <b>{view.confidence===null?"—":formatNumber(view.confidence*100,language,1)+"%"}</b> / 99%</span></>}
      {geometry&&!adaptive&&<><span data-testid="geometry-pixel-count">{t("perceptionOutlierPixels")}: <b>{String(decision.outlier_pixels??"—")} / {String(decision.allowed_outlier_pixels??"—")}</b> · {t("perceptionNearer")}: {String(decision.nearer_outlier_pixels??"—")} · {t("perceptionFarther")}: {String(decision.farther_outlier_pixels??"—")}</span><span>{t("perceptionResidualP99")}: <b>{formatNumber(Number(decision.plane_residual_p99),language,3)}</b> · {t("perceptionResidual")}: {String(decision.residual_limit??"—")}</span><span>{t("perceptionSlope")}: <b>{formatNumber(Number(decision.plane_slope),language,2)}</b> / {String(decision.slope_min??"—")}</span></>}
      {adaptive&&<><span>{t("geometryGround")}: {formatNumber(Number(decision.ground_support)*100,language,1)}%</span><span>{t("geometryGaps")}: {Array.isArray(decision.gaps)?decision.gaps.length:0}</span><span>{t("geometryProgress")}: {formatNumber(typeof decision.progress_delta==="number"?decision.progress_delta:null,language,3)} · {t("avoidanceStalled")}: {String(decision.stalled_corrections??0)} · {t("geometryPoseResets")}: {String(decision.pose_resets??0)}</span></>}
      <span>{t("decisionClearFrames")}: <b>{view.count}/{view.required}</b></span>
      <span>{t("decisionLatency")}: <b>{String(decision.inference_ms??"—")} ms</b> / {String(policy?.decision_budget_ms??"—")} ms</span>
      <span>{t("decisionRoundtrip")}: <b>{String(state.request_roundtrip_ms??"—")} ms</b></span>
      <span>{t("decisionAge")}: <b>{String(state.response_age_ms??"—")} ms</b> / {String(policy?.decision_budget_ms??"—")} ms</span>
      <span>{t("decisionPermit")}: <b>{String(state.permit_remaining_ms??"—")} ms</b></span>
      <span>{t("decisionFrame")}: <b>{String(decision.sequence??"—")} · {String(decision.frame_id??"—").slice(0,12)}</b></span>
      <span>{formatTime(state.observed_at,language)} · {t(view.current?"decisionLive":"decisionHistorical")}</span>
    </div></details>
  </section>;
}
