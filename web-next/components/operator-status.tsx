import type { Translator } from "../lib/i18n";
import { formatNumber } from "../lib/model";
import { operatorTelemetry } from "../lib/operator-telemetry";
import type { RiderClient } from "../lib/use-rider";
import { Posture } from "./control";
import { Pill } from "./primitives";
import styles from "../rider.module.css";

export function OperatorStatus({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
  const telemetry = operatorTelemetry(client.status);
  const unit = (value: string) => value === "sdk_raw" ? "SDK" : value;
  return <section className={styles.operatorStatus} aria-label={t("operator.robotStatus")} data-testid="operator-status">
    <div className={styles.operatorBattery}>
      <span>{t("battery")}</span>
      <strong data-testid="operator-battery">{formatNumber(telemetry.battery.value, language, 0)}{telemetry.battery.value !== null && <small> %</small>}</strong>
      <Pill tone={telemetry.quality === "fresh" ? "good" : "warning"}>{t(telemetry.quality)}</Pill>
    </div>
    <div className={styles.operatorAxes} data-testid="operator-axes">
      <div>{t("operator.orientation")}{telemetry.unit && <span>{unit(telemetry.unit)}</span>}</div>
      <dl>{telemetry.axes.map((axis, index) => <div key={index}><dt>{t("motion.axis", { axis: index + 1 })}</dt><dd>{formatNumber(axis.value, language)}{!telemetry.unit && axis.unit && <small> {unit(axis.unit)}</small>}</dd></div>)}</dl>
    </div>
    <Posture client={client} t={t} language={language} compact/>
  </section>;
}
