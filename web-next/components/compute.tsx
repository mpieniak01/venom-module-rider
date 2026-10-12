import { useState } from "react";
import Link from "next/link";
import { BrainCircuit } from "lucide-react";
import { canSelectExecutor, computeAge, computeError, computeQuality, computeState, computeView, executorSelection } from "../lib/compute";
import { formatNumber, record, str, type JsonRecord } from "../lib/model";
import type { Translator } from "../lib/i18n";
import type { RiderClient } from "../lib/use-rider";
import { Button, Details, Empty, Field, Panel, Pill } from "./primitives";
import styles from "../rider.module.css";
import { Conversation } from "./conversation";

type ComputeClient = Pick<RiderClient, "status" | "mutate" | "canMutate" | "busy">;

function ErrorLabel({ code, t }: { code: string; t: Translator }) {
  if (!code) return null;
  if (code === "conversation_disabled") return <span className={styles.small}>{t("inactive")}</span>;
  const key = `compute.error.${code}`, message = t(key);
  return <span className={styles.small} style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{message === key ? t("computeError") : message} <code>{code}</code></span>;
}

function Observation({ value, t, language }: { value: JsonRecord; t: Translator; language: string }) {
  const quality = computeQuality(value), metrics = record(value.metrics), text = str(record(value.data).text);
  const age = computeAge(value), runtime = record(value.runtime), code = computeError(value.error);
  return <div className={styles.resource}>
    <div className={styles.flex}><Pill tone={quality === "fresh" ? "good" : "warning"}>{t(quality === "demo" ? "demoTag" : quality)}</Pill><span className={styles.small}>{t(`compute.state.${computeState(value.state)}`)}</span></div>
    {text ? <p className={styles.hint} style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{text}</p> : <span className={styles.small}>{t("computeNoResult")}</span>}
    <div className={styles.meta}><span>{t("computeSourceAge")}: {formatNumber(age, language, 0)} ms</span><span>{t("computeQueueTime")}: {formatNumber(metrics.queue_ms, language, 0)} ms</span><span>{t("computeInferenceTime")}: {formatNumber(metrics.inference_ms, language, 0)} ms</span></div>
    <div className={styles.small}>{t("computeRuntime")}: {[str(runtime.provider), str(runtime.model)].filter(Boolean).join(" · ") || "—"}</div>
    <ErrorLabel code={code} t={t} />
  </div>;
}

export function Compute({ client, t, language }: { client: ComputeClient; t: Translator; language: string }) {
  const view = computeView(client.status);
  const [draft, setDraft] = useState<{ identity: string; value: string } | null>(null);
  const [applying, setApplying] = useState(false);
  const selected = draft?.identity === view.identity ? draft.value : view.selected;
  const changed = selected !== view.selected;
  const apply = async () => {
    const body = executorSelection(view, selected);
    if (!body || !client.canMutate || applying || !changed) return;
    setApplying(true);
    try { await client.mutate("/compute/executor", body); setDraft(null); }
    catch { /* The shared mutation handler displays the producer error and refreshes. */ }
    finally { setApplying(false); }
  };
  return <div className={styles.stack}>
    <Conversation client={client} t={t} />
    <Panel title={t("computeExecutor")} hint={t("computeExecutorHint")} extra={<Link className={styles.button} href="/models"><BrainCircuit size={15} />{t("computeModels")}</Link>}>
      {view.configured ? <><div className={styles.fieldRow}><Field label={t("computeExecutor")}><select className={styles.select} value={selected} disabled={!client.canMutate || applying || !!client.busy} onChange={event => setDraft({ identity: view.identity, value: event.target.value })}><option value="">{t("computeDisabled")}</option>{view.executors.map(executor => <option key={str(executor.id)} value={str(executor.id)} disabled={executor.credential_configured !== true}>{str(executor.id)}{executor.credential_configured !== true ? ` · ${t("computeNotConfigured")}` : ""}</option>)}</select></Field><Button tone="primary" disabled={!client.canMutate || applying || !!client.busy || !changed || !canSelectExecutor(view, selected)} onClick={() => void apply()}>{t("apply")}</Button></div><div className={styles.meta}><span>{t("computeSelected")}: {view.selected || t("computeDisabled")}</span><span>{t("computeRevision")}: {formatNumber(view.revision, language, 0)}</span></div></> : <Empty>{t("computeNotConfigured")}</Empty>}
      <ErrorLabel code={view.error} t={t} />
    </Panel>
    <Panel title={t("computeCapabilities")} hint={t("computeCapabilitiesHint")} extra={<span className={styles.small}>{t("computeRunning")}: {t(view.queue.running === true ? "yes" : view.queue.running === false ? "no" : "unknown")} · {t("computePending")}: {formatNumber(view.queue.pending, language, 0)}</span>}>
      <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>{t("computeTask")}</th><th>{t("computeRuntime")}</th><th>{t("computeInstalled")}</th><th>{t("computeConfigured")}</th><th>{t("computeReady")}</th></tr></thead><tbody>{view.tasks.map(task => { const runtime = record(task.runtime); return <tr key={task.task}><td>{t(`compute.task.${task.task}`)}<div className={styles.meta}>{record(task.quality).simulated === true && <Pill tone="warning">{t("demoTag")}</Pill>}{task.available === false && <ErrorLabel code={computeError(task.reason)} t={t} />}</div></td><td><span>{str(runtime.provider) || "—"}</span><div className={styles.small} style={{ overflowWrap: "anywhere" }}>{str(runtime.model) || "—"}</div></td>{[task.installed, task.configured, task.ready].map((value, index) => <td key={index}><Pill tone={value === true ? "good" : value === false ? undefined : "warning"}>{t(value === true ? "yes" : value === false ? "no" : "unknown")}</Pill></td>)}</tr>; })}</tbody></table></div>
    </Panel>
    <div className={styles.grid}>{view.domains.map(domain => <Panel key={domain.name} title={t(`compute.domain.${domain.name}`)} hint={t(domain.name === "vision" ? "computeVisionHint" : "computeVoiceHint")}><Observation value={domain} t={t} language={language} /></Panel>)}</div>
    <Panel title={t("computeJobs")} extra={<span className={styles.small}>{t("computeCompleted")}: {formatNumber(view.metrics.succeeded, language, 0)} · {t("computeFailed")}: {formatNumber(view.metrics.failed, language, 0)}</span>}>
      {view.jobs.length ? <div className={styles.stack}>{view.jobs.map((job, index) => <div key={str(job.job_id) || index} className={styles.goalCard}><div className={styles.flex}><strong>{t(`compute.task.${str(job.task)}`)}</strong><span className={styles.small}>{str(job.input_id)}</span></div><Observation value={job} t={t} language={language} /></div>)}</div> : <Empty>{t("computeNoJobs")}</Empty>}
      <Details value={{ executor: view.host.executor_id, session: view.host.executor_session, metrics: view.metrics, domains: view.domains.map(domain => ({ name: domain.name, runtime: domain.runtime, metrics: domain.metrics, quality: domain.quality })), limits: view.host.limits }} t={t} />
    </Panel>
  </div>;
}
