import { decisionView } from "../lib/loop-controls";
import { formatNumber, formatTime, record, safetyOf, type JsonRecord, type RiderStatus } from "../lib/model";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function LoopDecision({state,detector,policy,status,t,language}:{state:JsonRecord;detector:JsonRecord;policy:JsonRecord|null;status:RiderStatus|null;t:Translator;language:string}) {
  const view=decisionView(state,detector), safety=safetyOf(status);
  const decision=record(state.decision), key="vision.reason."+view.reason;
  const reason=t(key)===key?t("vision.reason.default"):t(key);
  const stoppedByFault=state.latched===true;
  const moving=safety.moving===true;
  const observed=typeof safety.moving==="boolean"&&safety.stale!==true&&!status?.errors.control;
  return <section className={styles.decisionArea} aria-label={t("decisionTitle")} data-testid="loop-decision">
    <p className={styles.hint}>{t("decisionScope")}</p>
    <div className={styles.decisionGrid}>
      <div className={styles.decisionCard}><span>{t("decisionVision")}</span><strong data-testid="vision-verdict" className={view.vision==="go"?styles.verdictGo:styles.verdictStop}>{t("decision."+(stoppedByFault?"stop":view.vision))}</strong><p data-testid="decision-reason">{reason}</p></div>
      <div className={styles.decisionCard}><span>{t("decisionReply")}</span><strong data-testid="pi-reply">{view.reply==="none"?"—":view.reply.toUpperCase()}</strong><p>{view.reply==="go"?t("decisionForward")+" · "+String(policy?.speed??"—")+" SDK · ≤ "+String(policy?.max_motion_ms??"—")+" ms":view.current?reason:t("decisionNoReply")}</p></div>
      <div className={styles.decisionCard}><span>{t("decisionExecutor")}</span><strong data-testid="pi-motion">{t(observed?moving?"decisionMoving":"decisionStopped":"unknown")}</strong><p>{t("owner")}: {String(safety.owner??"—")}</p><p>{t("decisionReportedMotion")}</p></div>
    </div>
    <div className={styles.decisionMetrics}>
      <span>{t("decisionFloor")}: <b>{view.floor===null?"—":formatNumber(view.floor*100,language,1)+"%"}</b> / 100%</span>
      <span>{t("decisionConfidence")}: <b>{view.confidence===null?"—":formatNumber(view.confidence*100,language,1)+"%"}</b> / 99%</span>
      <span>{t("decisionClearFrames")}: <b>{view.count}/{view.required}</b></span>
      <span>{t("decisionLatency")}: <b>{String(decision.inference_ms??"—")} ms</b> / {String(policy?.decision_budget_ms??"—")} ms</span>
      <span>{t("decisionRoundtrip")}: <b>{String(state.request_roundtrip_ms??"—")} ms</b></span>
      <span>{t("decisionAge")}: <b>{String(state.response_age_ms??"—")} ms</b> / {String(policy?.decision_budget_ms??"—")} ms</span>
      <span>{t("decisionValidity")}: <b>{String(decision.valid_for_ms??"—")} ms</b></span>
      <span>{formatTime(state.observed_at,language)} · {t(view.current?"decisionLive":"decisionHistorical")}</span>
    </div>
  </section>;
}
