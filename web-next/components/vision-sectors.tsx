import { previewSectors } from "../lib/preview-sectors";
import type { Frame } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function VisionSectors({frame,t}:{frame:Frame|null;t:Translator}) {
  const view = previewSectors(frame);
  return <section className={styles.sectorLegend} data-testid="directional-results" data-sector-space={view.space} aria-label={t("sectorsTitle")}>
    <p className={styles.hint} data-testid="sector-legend">{t(view.space === "projected_paths" ? "sectorsProjectedLegend" : view.space === "image" ? "sectorsImageLegend" : "sectorsUnavailable")}</p>
    <div className={styles.sectorGrid}>{view.rows.map(row=><div key={row.side} className={styles.sectorCard} data-testid={"sector-"+row.side} data-sector-state={row.state}>
      <b><span className={styles.sectorBadge} data-side={row.side}>{row.code}{row.state === "unknown" ? "?" : ""}</span>{t((view.space === "projected_paths" ? "sector.path." : "sector.")+row.side)}</b>
      <p className={row.state === "clear" ? styles.verdictGo : styles.verdictStop}>{t("vision."+row.state)}</p>
      {row.incomplete&&<p className={styles.small}>{t("sectorIncomplete")}</p>}
    </div>)}</div>
    {!view.current&&frame&&<p className={styles.hint} data-testid="sector-historical">{t("sectorsHistorical")}</p>}
    <p className={styles.small}>{t("sectorsDecisionHint")}</p>
  </section>;
}
