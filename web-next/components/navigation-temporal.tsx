import type { TemporalView } from "../lib/behavior";
import type { Translator } from "../lib/i18n";
import { scalarText, record } from "../lib/model";
import styles from "../rider.module.css";

/** Explain retained temporal evidence without turning historical diagnostics into permission. */
export function NavigationTemporal({ view, t }: { view: TemporalView; t: Translator }) {
  /** Render TemporalMetrics with the captured state and unchanged event handlers. */
  function renderTemporalMetrics(){
    return (<div className={styles.decisionMetrics}>
        <span>{t("temporal.raw")}: <b data-testid="temporal-raw">{view.raw || "—"}</b></span>
        <span>{t("temporal.stabilized")}: <b data-testid="temporal-stabilized">{view.stabilized || "—"}</b></span>
        <span>{t("temporal.risk")}: <b data-testid="temporal-action">{view.risk || "—"} / {view.action || "—"}</b>{view.speedFactor !== null && <> · ×{view.speedFactor}</>}</span>
        <span>{t("behavior.title")}: {view.profile ? view.custom ? t("behavior.custom", { profile: t("behavior.profile." + view.profile) }) : t("behavior.profile." + view.profile) : "—"} · r{view.revision || "—"}</span>
        <span>{t("temporal.evidence")}: {view.evidenceFrames ?? "—"}</span>
        <span>{t("temporal.near")}: {t(view.nearGuard === null ? "temporal.unknown" : view.nearGuard ? "temporal.yes" : "temporal.no")}</span>
      </div>);
  }

  const reasonKey = "vision.reason." + view.reason;
  const translatedReason = t(reasonKey);
  return <section className={styles.stack} aria-label={t("temporal.title")} data-testid="temporal-diagnostics">
    <h3>{t("temporal.title")}</h3>
    {!view.available ? <p className={styles.hint}>{t("temporal.none")}</p> : <>
      {!view.current && <p className={styles.hint} role="status">{t("temporal.historical")}</p>}
      {renderTemporalMetrics()}
      {view.persistence?.enabled === true && <p data-testid="temporal-persistence" className={styles.hint}>{t("behavior.persistenceStatus", { confirmed: scalarText(view.persistence.confirmed_pixels ?? "—"), raw: scalarText(view.persistence.raw_pixels ?? "—"), uncertain: scalarText(view.persistence.uncertain_pixels ?? "—") })}</p>}
      {view.objects?.state === "current" && Array.isArray(view.objects.objects) && <p data-testid="temporal-objects" className={styles.hint}>{t(view.objects.diagnostic_only === false ? "behavior.assessedObjects" : "behavior.diagnosticObjects", { count: view.objects.objects.length, confirmed: view.objects.objects.filter(value => record(value).confirmed === true).length, held: view.objects.objects.filter(value => record(value).evidence === "held").length, approaching: view.objects.objects.filter(value => record(value).approaching_candidate === true).length })}</p>}
      {view.objects?.diagnostic_only === false && view.objects?.state === "unavailable" && <p role="status" className={styles.hint}>{t("behavior.objectUnavailable")}</p>}
      <p className={styles.small} data-testid="temporal-reason">{t("temporal.reason")}: {view.reason ? (translatedReason === reasonKey ? view.reason : translatedReason) : t("temporal.noReason")}</p>
      <p className={styles.hint} data-testid="temporal-boundary">{view.boundaryPoints > 0 ? t("temporal.boundary") + ": " + view.boundaryPoints : t("temporal.noBoundary")}</p>
      {view.boundaryPoints > 0 && <p className={styles.hint}>{t("temporal.overlayLegend")}</p>}
    </>}
  </section>;
}
