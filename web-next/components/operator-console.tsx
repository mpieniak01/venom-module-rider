import { WifiOff, X } from "lucide-react";
import type { Translator } from "../lib/i18n";
import { formatTime, record, safetyOf, str } from "../lib/model";
import type { RiderClient } from "../lib/use-rider";
import { journeyOwnsMotion, manualInterventionSupported, operatorPrioritySupported } from "../lib/navigation";
import { ManualPad } from "./control";
import { OperatorStatus } from "./operator-status";
import { manualFeedback } from "../lib/manual-feedback";
import { Button, Panel } from "./primitives";
import styles from "../rider.module.css";

export function RiderMessages({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
  const connection = client.status?.connection;
  const errorKey = "transport.error." + connection?.error;
  const errorMessage = t(errorKey);
  return <div className={styles.messages} data-testid="rider-messages">
    {client.notice && <div className={styles.banner + " " + styles[client.notice.tone]} role={client.notice.tone === "error" ? "alert" : "status"}><div className={styles.noticeText}>{t(client.notice.key)}{client.notice.code && <code className={styles.small}> · {client.notice.code}</code>}</div><Button aria-label={t("close")} onClick={() => client.setNotice(null)}><X size={14}/></Button></div>}
    {connection?.status === "offline" && <div className={styles.banner + " " + styles.demo} role="status"><WifiOff size={16}/><div><strong>{t("offline")}</strong><div>{t("offlineHint")}</div>{connection.last_seen && <div className={styles.small}>{t("lastSeen")}: {formatTime(connection.last_seen, language)}</div>}</div></div>}
    {connection?.mode === "demo" && <div className={styles.banner + " " + styles.demo} role="status">{t("demo")}</div>}
    {str(connection?.error) && <p className={styles.small}>{t("state")}: {errorMessage === errorKey ? t("requestFailed") : errorMessage} <code>({connection?.error})</code></p>}
    {connection?.transport?.profile === "trusted_lan" && <p className={styles.small} data-testid="rider-transport-state">{t("transport.trustedLan")} · {t("transport.state." + connection.transport.state)}</p>}
    {!!Object.keys(client.status?.errors ?? {}).length && <details><summary>{t("partial")}</summary><pre className={styles.pre}>{JSON.stringify(client.status?.errors, null, 2)}</pre></details>}
  </div>;
}

export function OperatorMessages({client,t,authority=true}:{client:RiderClient;t:Translator;authority?:boolean}) {
  const safety = safetyOf(client.status);
  const journey = journeyOwnsMotion(client.status);
  const ownsManual = safety.state === "ARMED" && safety.owner === "manual";
  const direct = manualInterventionSupported(client.status);
  const reason = !client.canMutate ? "workspace.connectionLost" : client.status?.errors.control || safety.stale===true ? "workspacePermissionUnknown" : client.canNavigate ? null : journey && !direct ? "manualGestureUnsupported" : ownsManual ? "workspaceTakeoverNeeded" : safety.state === "ARMED" ? "workspaceAutomaticOwner" : safety.state === "DISARMED" ? null : "workspacePermissionUnknown";
  const notice=client.canMutate&&!client.status?.errors.control&&safety.stale!==true&&client.notice?.tone==="error"?client.notice:null;
  if(!notice&&(!authority||!reason))return null;
  return <div className={styles.operatorMessages+" "+(notice?.tone==="error"?styles.operatorMessageError:"")} data-testid="operator-messages" role={notice?.tone==="error"?"alert":"status"}>
        <span>{notice ? <>{t(notice.code==="panel_owner_mismatch"?"workspace.panelChanged":notice.key)}{notice.code && notice.code!=="panel_owner_mismatch" && <> · {notice.code}</>}</> : authority&&reason ? t(reason) : null}</span>
        {notice&&<Button aria-label={t("close")} onClick={()=>client.setNotice(null)}><X size={14}/></Button>}
      </div>;
}

export function OperatorConsole({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
  const safety = safetyOf(client.status);
  const journey = journeyOwnsMotion(client.status);
  return <Panel className={styles.operatorPanel} title={t("manual")} extra={<Button size="large" data-testid="takeover-manual" tone="primary" title={t(journey ? "journeyManualTakeover" : operatorPrioritySupported(client.status) && safety.state === "ARMED" ? "operatorTakeoverHint" : "takeManualHint")} disabled={!client.canArm || !!client.busy && !(operatorPrioritySupported(client.status) && safety.state === "ARMED")} onClick={() => void client.takeManual(t("manual"))}>{t("takeManual")}</Button>}>
    <ManualPad client={client} t={t} language={language} compact minimal>
      <OperatorStatus client={client} t={t} language={language}/>
      <OperatorMessages client={client} t={t}/>
    </ManualPad>
  </Panel>;
}

export function OperatorDiagnostics({client,t,language}:{client:RiderClient;t:Translator;language:string}) {
  const safety=safetyOf(client.status),execution=record(safety.execution);
  const feedback=manualFeedback(client.status,client.manualRequest,client.controlFresh);
  return <Panel title={t("workspace.controlDiagnostics")}>
    <RiderMessages client={client} t={t} language={language}/>
    <div className={styles.meta}><span>{t("owner")}: {String(safety.owner??"—")}</span><span>{t("manualAck")}: {String(client.manualRequest?.phase??"—")}</span><span>{t("manualOutput")}: {String(execution.state??"—")}</span></div>
    <div className={styles.meta} data-testid="manual-execution">{t(feedback.executionKey)} · {feedback.output}</div>
  </Panel>;
}
