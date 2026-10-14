import { decisionView } from "../lib/loop-controls";
import { formatNumber, formatTime, record, safetyOf, type JsonRecord, type RiderStatus } from "../lib/model";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function LoopDecision({state,detector,policy,status,t,language}:{state:JsonRecord;detector:JsonRecord;policy:JsonRecord|null;status:RiderStatus|null;t:Translator;language:string}) {
  const view=decisionView(state,detector), safety=safetyOf(status);
  const decision=record(state.decision), key="vision.reason."+view.imageReason;
  const reason=t(key)===key?t("vision.reason.default"):t(key);
  const replyReasonKey="vision.reason."+view.reason;
  const replyReason=t(replyReasonKey)===replyReasonKey?t("vision.reason.default"):t(replyReasonKey);
  const classId=typeof decision.dominant_class_id==="number"?decision.dominant_class_id:null;
  const classKey="vision.class."+String(classId),className=t(classKey)===classKey?t("visionClassOther",{id:String(classId)}):t(classKey);
  const geometry=decision.perception_engine==="depth_plane";
  const moving=safety.moving===true;
  const observed=typeof safety.moving==="boolean"&&safety.stale!==true&&!status?.errors.control;
  return <section className={styles.decisionArea} aria-label={t("decisionTitle")} data-testid="loop-decision">
    {geometry&&<p className={styles.hint} data-testid="geometry-verdict">{t("perceptionGeometryHint")} · {t("perceptionDeviation")}: {formatNumber(Number(decision.outlier_fraction)*100,language,2)}% / {formatNumber(Number(decision.outlier_limit)*100,language,2)}%</p>}
    {classId!==null&&<p className={styles.modelClass} data-testid="vision-class">{t("visionClassInZone",{name:className,fraction:formatNumber(Number(decision.dominant_class_fraction)*100,language,1)})}{[6,11,28].includes(classId)&&<> {t("visionUnsupportedGround")}</>}</p>}
    <div className={styles.decisionGrid}>
      <div className={styles.decisionCard}><span>{t("decisionVision")}</span><strong data-testid="vision-verdict" className={view.vision==="go"?styles.verdictGo:styles.verdictStop}>{t("decision."+view.vision)}</strong><p data-testid="decision-reason">{view.vision==="go"?t("imageGoHint"):reason}</p>{view.current&&view.floor!==null&&<p>{t("confidentFloorCoverage",{value:formatNumber(view.floor*100,language,2)})}</p>}</div>
      <div className={styles.decisionCard}><span>{t("decisionReply")}</span><strong data-testid="pi-reply">{view.reply==="none"?"—":view.reply.toUpperCase()}</strong><p>{view.reply==="go"?t("decisionForward")+" · "+String(policy?.speed??"—")+" SDK · ≤ "+String(policy?.max_motion_ms??"—")+" ms":view.current?replyReason:t("decisionNoReply")}</p></div>
      <div className={styles.decisionCard}><span>{t("decisionExecutor")}</span><strong data-testid="pi-motion">{t(observed?moving?"decisionMoving":"decisionStopped":"unknown")}</strong><p>{t("owner")}: {String(safety.owner??"—")}</p><p>{t("decisionReportedMotion")}</p></div>
    </div>
    <details className={styles.details}><summary>{t("visionDiagnostics")}</summary><div className={styles.decisionMetrics}>
      {!geometry&&<><span>{t("decisionFloor")}: <b>{view.floor===null?"—":formatNumber(view.floor*100,language,1)+"%"}</b> / 100%</span>
      <span>{t("decisionConfidence")}: <b>{view.confidence===null?"—":formatNumber(view.confidence*100,language,1)+"%"}</b> / 99%</span></>}
      {geometry&&<><span>{t("perceptionResidual")}: <b>{formatNumber(Number(decision.plane_residual_p99),language,3)}</b> / {String(decision.residual_limit??"—")}</span><span>{t("perceptionSlope")}: <b>{formatNumber(Number(decision.plane_slope),language,2)}</b> / {String(decision.slope_min??"—")}</span></>}
      <span>{t("decisionClearFrames")}: <b>{view.count}/{view.required}</b></span>
      <span>{t("decisionLatency")}: <b>{String(decision.inference_ms??"—")} ms</b> / {String(policy?.decision_budget_ms??"—")} ms</span>
      <span>{t("decisionRoundtrip")}: <b>{String(state.request_roundtrip_ms??"—")} ms</b></span>
      <span>{t("decisionAge")}: <b>{String(state.response_age_ms??"—")} ms</b> / {String(policy?.decision_budget_ms??"—")} ms</span>
      <span>{t("decisionValidity")}: <b>{String(decision.valid_for_ms??"—")} ms</b></span>
      <span>{formatTime(state.observed_at,language)} · {t(view.current?"decisionLive":"decisionHistorical")}</span>
    </div></details>
  </section>;
}
