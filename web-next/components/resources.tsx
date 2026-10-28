import { useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { Boxes, FileText } from "lucide-react";
import { scalarText, data, errorCode, featureRows, formatTime, record, rows, str, type JsonRecord } from "../lib/model";
import type { Translator } from "../lib/i18n";
import type { RiderClient } from "../lib/use-rider";
import { Button, Details, Empty, Field, Panel, Pill, Select } from "./primitives";
import styles from "../rider.module.css";

function Goal({ goal, client, t }: { goal: JsonRecord; client: RiderClient; t: Translator }) {
  const [option, setOption] = useState<string | null>(null);
  const name = str(goal.name), schema = record(goal.option_schema), optionField = str(schema.field);
  const selected = option ?? str(record(goal.options)[optionField] ?? schema.default);
  const active = goal.requested === true || goal.active === true;
  const permission = record(record(goal.permissions)[active ? "stop" : "start"]);
  const allowed = active || (permission.allowed !== false && goal.start_available !== false);
  const invoke = () => void client.mutate(`/goal/${encodeURIComponent(name)}`, { enabled: !active, ...(optionField ? { options: { [optionField]: selected } } : {}) });
  return <div className={styles.goalCard}><div className={`${styles.flex} ${styles.space}`}><span className={styles.goalTitle}>{t(name)}</span><Pill tone={goal.active === true ? "good" : undefined}>{t(goal.active === true ? "active" : "inactive")}</Pill></div>
    {goal.can_move === true && <Pill tone="warning">{t("canMove")}</Pill>}
    {optionField && <Field label={t("options")}><Select value={selected} disabled={active || !client.canMutate || !!client.busy} onChange={event => setOption(event.target.value)}>{rows(schema.choices).map(choice => <option key={str(choice.value)} value={str(choice.value)}>{t(`option.${str(choice.value)}`)}</option>)}</Select></Field>}
    <div className={`${styles.flex} ${styles.space}`}><span className={styles.small}>{t("state")}: <code>{str(goal.state ?? goal.status) || "—"}</code></span><Button tone={active ? undefined : "primary"} disabled={!client.canMutate || !!client.busy || !allowed} onClick={invoke}>{t(active ? "disable" : "enable")}</Button></div>
    {!allowed && <span className={styles.small}>{t("unavailable")} · <code>{str(permission.reason ?? goal.start_unavailable_reason)}</code></span>}
    <Details value={goal} t={t} />
  </div>;
}
/** Present device-owned power and ownership with explicit stop/release confirmation. */
function Resource({ name, payload, client, t, language }: { name: string; payload: JsonRecord; client: RiderClient; t: Translator; language: string }) {
  /** Render ResourceDetails with the captured state and unchanged event handlers. */
  function renderResourceDetails(){
    return (<div className={styles.resource}><div className={styles.flex}><Pill tone={row.free === true ? "good" : undefined}>{t(row.free === true ? "free" : row.free === false ? "busy" : "unknown")}</Pill><span className={styles.small}>{t("owner")}: {owner || "—"}</span></div>
      <div className={styles.small}>{t("holders")}: {holders.length ? holders.map(holder => { const detail = record(holder); return str(detail.comm ?? detail.unit ?? detail.name) || scalarText(detail.pid ?? holder); }).join(", ") : "—"}</div>
      <div className={styles.small}>{t("consumers")}: {consumers.length ? consumers.map(consumer => str(record(consumer).name ?? record(consumer).unit) || scalarText(consumer)).join(", ") : "—"}</div>
      <div className={styles.flex}>{["lcd", "led"].includes(name) && <><Button disabled={!client.canMutate || !!client.busy} onClick={() => void client.mutate(`/resource/${encodeURIComponent(name)}`, { action: "on" })}>{t("enable")}</Button><Button disabled={!client.canMutate || !!client.busy} onClick={() => void client.mutate(`/resource/${encodeURIComponent(name)}`, { action: "off" })}>{t("disable")}</Button></>}
        <Button disabled={!client.canMutate || !!client.busy || unknown} onClick={() => setConfirmation("stop")}>{t("resourceStop")}</Button><Button disabled={!client.canMutate || !!client.busy || unknown} onClick={() => setConfirmation("release")}>{t("resourceRelease")}</Button>
      </div>
      {confirmation && <div className={`${styles.banner} ${styles.demo}`}><span className={styles.noticeText}>{t(confirmation === "stop" ? "resourceStop" : "resourceRelease")}?</span><Button tone="danger" onClick={() => { void client.mutate(`/resource/${encodeURIComponent(name)}`, { action: confirmation }); setConfirmation(null); }}>{t("apply")}</Button><Button onClick={() => setConfirmation(null)}>{t("cancel")}</Button></div>}
      <span className={styles.small}>{t("observedAt")}: {formatTime(row.observed_at, language)}</span>
    </div>);
  }

  /** Derive device resource power with native boolean precedence from the current captured render without effects. */
  function resourcePower(){return (typeof activity.on === "boolean" ? (activity.on ? "on" : "off") : typeof row.on === "boolean" ? (row.on ? "on" : "off") : str(activity.state ?? state.state ?? row.power ?? row.state ?? row.status));}

  const [confirmation, setConfirmation] = useState<string | null>(null);
  const row = data(payload), state = record(row.state), activity = record(row.activity);
  const power = resourcePower();
  const owner = str(row.owner ?? state.owner);
  const holders = Array.isArray(row.holders) ? row.holders : [];
  const consumers = Array.isArray(row.consumers) ? row.consumers : [];
  const unknown = row.ownership_known === false || row.free === null || row.diagnostic_available === false || !!row.error || row.offline === true;
  return <Panel title={t(name)} extra={<Pill tone={row.offline === true ? "warning" : undefined}>{t(row.offline === true ? "offline" : ["on", "off"].includes(power.toLowerCase()) ? power.toLowerCase() : "unknown")}</Pill>}>
    {renderResourceDetails()}<Details value={row} t={t} />
  </Panel>;
}
/** Load requested diagnostics and logs independently of resource actuation. */
export function Resources({ client, t, language }: { client: RiderClient; t: Translator; language: string }) {
  /** Render ResourceServices with the captured state and unchanged event handlers. */
  function renderResourceServices(){
    return (<Panel title={t("goals")} hint={t("goalsHint")}>{features.length ? <div className={styles.grid3}>{features.map((goal, index) => <Goal key={str(goal.name) || index} goal={goal} client={client} t={t} />)}</div> : <Empty><Boxes size={28} />{t("unavailable")}</Empty>}</Panel>);
  }

  /** Derive diagnostic log text without changing source precedence from the current captured render without effects. */
  function resourceLogText(){return (logs ? (Array.isArray(logs.lines) ? logs.lines.map(line=>scalarText(line)).join("\n") : typeof logs.logs === "string" ? logs.logs : typeof logs.output === "string" ? logs.output : JSON.stringify(logs, null, 2)) : "");}

  const [diagnostics, setDiagnostics] = useState<JsonRecord | null>(null);
  const [unit, setUnit] = useState(""); const [logs, setLogs] = useState<JsonRecord | null>(null); const [reading, setReading] = useState(false);
  const catalog = data(client.status?.catalog);
  const features = featureRows(client.status);
  const units = rows(catalog.units);
  const knownUnits = units.length ? units.map(row => str(row.name ?? row.unit)).filter(Boolean) : [...new Set(features.flatMap(feature => Array.isArray(feature.members) ? feature.members.filter((item): item is string => typeof item === "string") : []))];
  const selectedUnit = unit || knownUnits[0] || "";
  const readDiagnostics = async () => {
    setReading(true);
    try { setDiagnostics(data(await apiFetch("/api/v1/rider/diagnostics"))); }
    catch (error) { client.setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" }); }
    finally { setReading(false); }
  };
  const readLogs = async () => {
    if (!selectedUnit || !client.canMutate) return;
    setReading(true);
    try { setLogs(data(await apiFetch(`/api/v1/rider/logs/${encodeURIComponent(selectedUnit)}?lines=100`))); }
    catch (error) { client.setNotice({ key: "requestFailed", code: errorCode(error), tone: "error" }); }
    finally { setReading(false); }
  };
  const logText = resourceLogText();
  return <div className={styles.stack}>{renderResourceServices()}
    <p className={styles.hint}>{t("resourceHint")}</p><div className={styles.grid3}>{Object.entries(client.status?.resources ?? {}).map(([name, row]) => <Resource key={name} name={name} payload={row} client={client} t={t} language={language} />)}</div>
    <Panel title={t("services")} extra={<Button disabled={!client.canMutate || reading} onClick={() => void readDiagnostics()}>{t("readDiagnostics")}</Button>}>
      {diagnostics && <><div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>{t("unit")}</th><th>{t("state")}</th><th>{t("owner")}</th></tr></thead><tbody>{rows(diagnostics.nodes ?? diagnostics.services).map((node, index) => <tr key={str(node.unit ?? node.name ?? node.id) || index}><td>{str(node.unit ?? node.name ?? node.id) || "—"}</td><td>{str(record(node.state).active_state ?? record(node.state).active ?? node.status) || "—"}</td><td>{str(node.motion_owner ?? node.owner) || "—"}</td></tr>)}</tbody></table></div><Details value={diagnostics} t={t} /></>}
    </Panel>
    <Panel title={t("logs")}><div className={styles.fieldRow}><Field label={t("logs")}><Select value={selectedUnit} onChange={event => { setUnit(event.target.value); setLogs(null); }}>{knownUnits.map(name => <option key={name} value={name}>{name}</option>)}</Select></Field><Button disabled={!client.canMutate || !selectedUnit || reading} onClick={() => void readLogs()}><FileText size={15} />{t("readLogs")}</Button></div>{logText ? <pre className={styles.pre} style={{ marginTop: 16 }}>{logText}</pre> : <Empty>{t("noLogs")}</Empty>}</Panel>
  </div>;
}
