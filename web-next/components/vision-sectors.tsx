import { useEffect, useRef, useState } from "react";
import { sectorPresentation, SECTOR_PRESENTATION_INTERVAL_MS } from "../lib/preview-sectors";
import type { Frame } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function VisionSectors({frame,t,compact=false}:{frame:Frame|null;t:Translator;compact?:boolean}) {
  const latest = useRef(frame);
  const [sample, setSample] = useState(() => sectorPresentation(null, frame));
  useEffect(() => { latest.current = frame; }, [frame]);
  useEffect(() => {
    const timer = setInterval(() => setSample(previous => sectorPresentation(previous, latest.current, true)), SECTOR_PRESENTATION_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);
  const presentation = sectorPresentation(sample, frame);
  // Only source/policy changes or invalidation adjust state between timer ticks.
  if (presentation !== sample) setSample(presentation);
  const view = presentation.view;
  return <section className={styles.sectorLegend} data-compact={compact} data-testid="directional-results" data-sector-space={view.space} data-assessment-frame={presentation.frameId} aria-label={t("sectorsTitle")}>
    <p hidden={compact} className={styles.hint} data-testid="sector-legend">{t(view.space === "projected_paths" ? "sectorsProjectedLegend" : view.space === "image" ? "sectorsImageLegend" : "sectorsUnavailable")}</p>
    <div className={styles.sectorGrid}>{view.rows.map(row=><div key={row.side} className={styles.sectorCard} data-testid={"sector-"+row.side} data-sector-state={row.state}>
      <b><span className={styles.sectorBadge} data-side={row.side}>{row.code}{row.state === "unknown" ? "?" : ""}</span>{t((view.space === "projected_paths" ? "sector.path." : "sector.")+row.side)}</b>
      <p className={row.state === "clear" ? styles.verdictGo : styles.verdictStop}>{t("vision."+row.state)}</p>
      {row.incomplete&&<p className={styles.small}>{t("sectorIncomplete")}</p>}
    </div>)}</div>
    {!compact&&!view.current&&frame&&<p className={styles.hint} data-testid="sector-historical">{t("sectorsHistorical")}</p>}
    <p hidden={compact} className={styles.small}>{t("sectorsDecisionHint")}</p>
  </section>;
}
