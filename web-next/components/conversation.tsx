import { record, str } from "../lib/model";
import type { Translator } from "../lib/i18n";
import type { RiderClient } from "../lib/use-rider";
import { Button, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";

export function Conversation({ client, t }: { client: Pick<RiderClient, "status" | "mutate" | "canMutate" | "busy">; t: Translator }) {
  const config = record(client.status?.conversation);
  const enabled = config.enabled === true;
  const result = record(config.last_result);
  const known = typeof config.revision === "number" && typeof config.enabled === "boolean";
  return <Panel title={t("conversationTitle")} hint={t("conversationHint")} extra={<Pill tone={enabled ? "good" : undefined}>{t(enabled ? "active" : "inactive")}</Pill>}>
    <div className={styles.flex}><Button tone={enabled ? undefined : "primary"} disabled={!known || !client.canMutate || !!client.busy} onClick={() => void client.mutate("/conversation/config", { enabled: !enabled, revision: config.revision })}>{t(enabled ? "conversationDisable" : "conversationEnable")}</Button><span className={styles.small}>{t("conversationExample")}</span></div>
    {enabled && config.credential_configured !== true && <p className={styles.hint}>{t("conversationCredentialMissing")}</p>}
    {str(result.reply) && <p className={styles.hint}>{result.stale === true && <Pill tone="warning">{t("stale")}</Pill>} {str(result.reply)}</p>}
  </Panel>;
}
