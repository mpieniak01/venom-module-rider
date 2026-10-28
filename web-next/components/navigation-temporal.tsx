import type { TemporalView } from "../lib/behavior";
import type { Translator } from "../lib/i18n";
import { scalarText, record } from "../lib/model";
import styles from "../rider.module.css";

/** Distinguish an absent profile from a customized named profile. */
function temporalProfileLabel(view:TemporalView,t:Translator):string {
  if(!view.profile)return "—";
  const profile=t("behavior.profile."+view.profile);
  return view.custom?t("behavior.custom",{profile}):profile;
}
/** Keep an unknown near guard distinct from an observed false guard. */
function temporalNearLabel(near:boolean|null):string {
  if(near===null)return "temporal.unknown";
  return near?"temporal.yes":"temporal.no";
}
/** Preserve the raw reason when no translation exists and label an absent reason. */
function temporalReasonLabel(reason:string,key:string,translated:string,t:Translator):string {
  if(!reason)return t("temporal.noReason");
  return translated===key?reason:translated;
}
/** Explain retained temporal evidence without turning historical diagnostics into permission. */
export function NavigationTemporal({ view, t }: { view: TemporalView; t: Translator }) {
  /** Render TemporalMetrics with the captured state and unchanged event handlers. */
  function renderTemporalMetrics(){
    return (<div className={styles.decisionMetrics}>
        <span>{t("temporal.raw")}: <b data-testid="temporal-raw">{view.raw || "—"}</b></span>
        <span>{t("temporal.stabilized")}: <b data-testid="temporal-stabilized">{view.stabilized || "—"}</b></span>
        <span>{t("temporal.risk")}: <b data-testid="temporal-action">{view.risk || "—"} / {view.action || "—"}</b>{view.speedFactor !== null && <> · ×{view.speedFactor}</>}</span>
        <span>{t("behavior.title")}: {temporalProfileLabel(view,t)} · r{view.revision || "—"}</span>
        <span>{t("temporal.evidence")}: {view.evidenceFrames ?? "—"}</span>
        <span>{t("temporal.near")}: {t(temporalNearLabel(view.nearGuard))}</span>
      </div>);
  }

  const reasonKey = "vision.reason." + view.reason;
  const translatedReason = t(reasonKey);
  return <section className={styles.stack} aria-label={t("temporal.title")} data-testid="temporal-diagnostics">
    <h3>{t("temporal.title")}</h3>
    {!view.available ? <p className={styles.hint}>{t("temporal.none")}</p> : <>
      {!view.current && <output className={styles.statusParagraph+" "+(styles.hint)} aria-live="polite">{t("temporal.historical")}</output>}
      {renderTemporalMetrics()}
      {view.persistence?.enabled === true && <p data-testid="temporal-persistence" className={styles.hint}>{t("behavior.persistenceStatus", { confirmed: scalarText(view.persistence.confirmed_pixels ?? "—"), raw: scalarText(view.persistence.raw_pixels ?? "—"), uncertain: scalarText(view.persistence.uncertain_pixels ?? "—") })}</p>}
      {view.objects?.state === "current" && Array.isArray(view.objects.objects) && <p data-testid="temporal-objects" className={styles.hint}>{t(view.objects.diagnostic_only === false ? "behavior.assessedObjects" : "behavior.diagnosticObjects", { count: view.objects.objects.length, confirmed: view.objects.objects.filter(value => record(value).confirmed === true).length, held: view.objects.objects.filter(value => record(value).evidence === "held").length, approaching: view.objects.objects.filter(value => record(value).approaching_candidate === true).length })}</p>}
      {view.objects?.diagnostic_only === false && view.objects?.state === "unavailable" && <output aria-live="polite" className={styles.statusParagraph+" "+(styles.hint)}>{t("behavior.objectUnavailable")}</output>}
      <p className={styles.small} data-testid="temporal-reason">{t("temporal.reason")}: {temporalReasonLabel(view.reason,reasonKey,translatedReason,t)}</p>
      <p className={styles.hint} data-testid="temporal-boundary">{view.boundaryPoints > 0 ? t("temporal.boundary") + ": " + view.boundaryPoints : t("temporal.noBoundary")}</p>
      {view.boundaryPoints > 0 && <p className={styles.hint}>{t("temporal.overlayLegend")}</p>}
    </>}
  </section>;
}
