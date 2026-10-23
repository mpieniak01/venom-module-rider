import type { TemporalView } from "../lib/behavior";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function NavigationTemporal({ view, t }: { view: TemporalView; t: Translator }) {
  return <section className={styles.stack} aria-label={t("temporal.title")} data-testid="temporal-diagnostics">
    <h3>{t("temporal.title")}</h3>
    {!view.available ? <p className={styles.hint}>{t("temporal.none")}</p> : <>
      {!view.current && <p className={styles.hint} role="status">{t("temporal.historical")}</p>}
      <div className={styles.decisionMetrics}>
        <span>{t("temporal.raw")}: <b data-testid="temporal-raw">{view.raw || "—"}</b></span>
        <span>{t("temporal.stabilized")}: <b data-testid="temporal-stabilized">{view.stabilized || "—"}</b></span>
        <span>{t("temporal.risk")}: <b data-testid="temporal-action">{view.risk || "—"} / {view.action || "—"}</b>{view.speedFactor !== null && <> · ×{view.speedFactor}</>}</span>
        <span>{t("behavior.title")}: {view.profile ? t("behavior.profile." + view.profile) : "—"} · r{view.revision || "—"}</span>
        <span>{t("temporal.evidence")}: {view.evidenceFrames ?? "—"}</span>
        <span>{t("temporal.near")}: {t(view.nearGuard === null ? "temporal.unknown" : view.nearGuard ? "temporal.yes" : "temporal.no")}</span>
      </div>
      <p className={styles.small} data-testid="temporal-reason">{t("temporal.reason")}: {view.reason || t("temporal.noReason")}</p>
      <p className={styles.hint} data-testid="temporal-boundary">{view.boundaryPoints > 0 ? t("temporal.boundary") + ": " + view.boundaryPoints : t("temporal.noBoundary")}</p>
    </>}
  </section>;
}
