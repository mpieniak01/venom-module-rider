import { WifiOff, X } from "lucide-react";
import type { Translator } from "../lib/i18n";
import { formatTime, safetyOf, str } from "../lib/model";
import type { RiderClient } from "../lib/use-rider";
import { operatorPrioritySupported } from "../lib/navigation";
import { ManualPad } from "./control";
import { Button, Panel } from "./primitives";
import styles from "../rider.module.css";

export function RiderMessages({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
  const connection = client.status?.connection;
  return <div className={styles.messages} data-testid="rider-messages">
    {client.notice && <div className={styles.banner + " " + styles[client.notice.tone]} role={client.notice.tone === "error" ? "alert" : "status"}><div className={styles.noticeText}>{t(client.notice.key)}{client.notice.code && <code className={styles.small}> · {client.notice.code}</code>}</div><Button aria-label={t("close")} onClick={() => client.setNotice(null)}><X size={14}/></Button></div>}
    {connection?.status === "offline" && <div className={styles.banner + " " + styles.demo} role="status"><WifiOff size={16}/><div><strong>{t("offline")}</strong><div>{t("offlineHint")}</div>{connection.last_seen && <div className={styles.small}>{t("lastSeen")}: {formatTime(connection.last_seen, language)}</div>}</div></div>}
    {connection?.mode === "demo" && <div className={styles.banner + " " + styles.demo} role="status">{t("demo")}</div>}
    {str(connection?.error) && <p className={styles.small}>{t("state")}: <code>{connection?.error}</code></p>}
    {!!Object.keys(client.status?.errors ?? {}).length && <details><summary>{t("partial")}</summary><pre className={styles.pre}>{JSON.stringify(client.status?.errors, null, 2)}</pre></details>}
  </div>;
}

export function OperatorConsole({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
  const safety = safetyOf(client.status);
  const ownsManual = safety.state === "ARMED" && safety.owner === "manual";
  const reason = client.canNavigate ? (safety.owner === "navigation" ? "operatorInterventionReady" : "workspaceManualReady") : ownsManual ? "workspaceTakeoverNeeded" : safety.state === "ARMED" ? "workspaceAutomaticOwner" : safety.state === "DISARMED" ? "workspaceArmNeeded" : "workspacePermissionUnknown";
  return <Panel className={styles.operatorPanel} title={t("manual")} extra={<Button size="large" data-testid="takeover-manual" tone="primary" title={t(operatorPrioritySupported(client.status) && safety.state === "ARMED" ? "operatorTakeoverHint" : "takeManualHint")} disabled={!client.canArm || !!client.busy && !(operatorPrioritySupported(client.status) && safety.state === "ARMED")} onClick={() => void client.takeManual(t("manual"))}>{t("takeManual")}</Button>}>
    <ManualPad client={client} t={t} language={language} compact>
    <div className={styles.operatorMessages} data-testid="operator-messages" tabIndex={0} role="region" aria-label={t("workspaceMessages")}>
      <RiderMessages client={client} t={t} language={language}/>
      <p className={styles.hint} role="status">{t(reason)}</p>
      <p className={styles.hint}>{t(operatorPrioritySupported(client.status) && safety.state === "ARMED" ? "operatorTakeoverHint" : "takeManualHint")}</p>
    </div>
    </ManualPad>
  </Panel>;
}
