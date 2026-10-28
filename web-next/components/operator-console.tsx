import { WifiOff, X } from "lucide-react";
import type { Translator } from "../lib/i18n";
import { scalarText, formatTime, record, safetyOf, str } from "../lib/model";
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
    {connection?.mode === "demo" && <output className={styles.statusBlock+" "+(styles.banner + " " + styles.demo)} aria-live="polite">{t("demo")}</output>}
    {str(connection?.error) && <p className={styles.small}>{t("state")}: {errorMessage === errorKey ? t("requestFailed") : errorMessage} <code>({connection?.error})</code></p>}
    {connection?.transport?.profile === "trusted_lan" && <p className={styles.small} data-testid="rider-transport-state">{t("transport.trustedLan")} · {t("transport.state." + connection.transport.state)}</p>}
    {!!Object.keys(client.status?.errors ?? {}).length && <details><summary>{t("partial")}</summary><pre className={styles.pre}>{JSON.stringify(client.status?.errors, null, 2)}</pre></details>}
  </div>;
}

/** Present the highest-priority connection, notice or manual authority message. */
export function OperatorMessages({client,t,authority=true}:{client:RiderClient;t:Translator;authority?:boolean}) {
  /** Present the selected notice or manual authority message with unchanged precedence. */
  function renderMessageText(){
    if(notice)return <span>{t(notice.code==="panel_owner_mismatch"?"workspace.panelChanged":notice.key)}{notice.code&&notice.code!=="panel_owner_mismatch"&&<> · {notice.code}</>}</span>;
    return <span>{authority&&reason?t(reason):null}</span>;
  }

  /** Keep manual ownership and connection blockers in operator-visible priority order. */
  function manualMessageReason():string|null {
    if(!client.canMutate)return "workspace.connectionLost";
    if(client.status?.errors.control||safety.stale===true)return "workspacePermissionUnknown";
    if(client.canNavigate)return null;
    if(journey&&!direct)return "manualGestureUnsupported";
    if(ownsManual)return "workspaceTakeoverNeeded";
    if(safety.state==="ARMED")return "workspaceAutomaticOwner";
    return safety.state==="DISARMED"?null:"workspacePermissionUnknown";
  }
  const safety = safetyOf(client.status);
  const journey = journeyOwnsMotion(client.status);
  const ownsManual = safety.state === "ARMED" && safety.owner === "manual";
  const direct = manualInterventionSupported(client.status);
  const reason=manualMessageReason();
  const notice=client.canMutate&&!client.status?.errors.control&&safety.stale!==true&&client.notice?.tone==="error"?client.notice:null;
  if(!notice&&(!authority||!reason))return null;
  return <div className={styles.operatorMessages+" "+(notice?.tone==="error"?styles.operatorMessageError:"")} data-testid="operator-messages" role={notice?.tone==="error"?"alert":"status"}>
        {renderMessageText()}
        {notice&&<Button aria-label={t("close")} onClick={()=>client.setNotice(null)}><X size={14}/></Button>}
      </div>;
}

/** Prefer a journey takeover hint before the supported armed priority gesture. */
function manualTakeoverLabel(journey:boolean,client:RiderClient,state:unknown):string {
  if(journey)return "journeyManualTakeover";
  return operatorPrioritySupported(client.status)&&state==="ARMED"?"operatorTakeoverHint":"takeManualHint";
}
export function OperatorConsole({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
  const safety = safetyOf(client.status);
  const journey = journeyOwnsMotion(client.status);
  return <Panel className={styles.operatorPanel} title={t("manual")} extra={<Button size="large" data-testid="takeover-manual" tone="primary" title={t(manualTakeoverLabel(journey,client,safety.state))} disabled={!client.canArm || !!client.busy && !(operatorPrioritySupported(client.status) && safety.state === "ARMED")} onClick={() => void client.takeManual(t("manual"))}>{t("takeManual")}</Button>}>
    <ManualPad client={client} t={t} language={language} compact minimal>
      <OperatorStatus client={client} t={t} language={language}/>
      <OperatorMessages client={client} t={t}/>
    </ManualPad>
  </Panel>;
}

/** Display confirmed manual acknowledgement and executor state without inferring motion. */
export function OperatorDiagnostics({client,t,language}:{client:RiderClient;t:Translator;language:string}) {
  const safety=safetyOf(client.status),execution=record(safety.execution);
  const feedback=manualFeedback(client.status,client.manualRequest,client.controlFresh);
  return <Panel title={t("workspace.controlDiagnostics")}>
    <RiderMessages client={client} t={t} language={language}/>
    <div className={styles.meta}><span>{t("owner")}: {scalarText(safety.owner??"—")}</span><span>{t("manualAck")}: {scalarText(client.manualRequest?.phase??"—")}</span><span>{t("manualOutput")}: {scalarText(execution.state??"—")}</span></div>
    <div className={styles.meta} data-testid="manual-execution">{t(feedback.executionKey)} · {feedback.output}</div>
  </Panel>;
}
