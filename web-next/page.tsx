"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Bot, Boxes, CircleStop, RefreshCw, ShieldCheck, SlidersHorizontal, Smile, Wifi, WifiOff, X } from "lucide-react";
import { useLanguage } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { translate } from "./lib/i18n";
import { formatTime, safetyOf, str, type Section } from "./lib/model";
import { useRider } from "./lib/use-rider";
import { Calibration } from "./components/calibration";
import { CameraPanel, ManualPad, Posture } from "./components/control";
import { FaceStudio } from "./components/face";
import { Measurements, RequestHistory, SensorMetrics } from "./components/measurements";
import { Button, Empty, Panel, Pill } from "./components/primitives";
import { Resources } from "./components/resources";
import styles from "./rider.module.css";

const tabs = [{ id: "control", Icon: Bot }, { id: "overview", Icon: Activity }, { id: "calibration", Icon: SlidersHorizontal }, { id: "resources", Icon: Boxes }, { id: "face", Icon: Smile }] as const;
export default function RiderPage() {
  const { language } = useLanguage();
  const { theme } = useTheme();
  const t = useCallback((key: string) => translate(language, key), [language]);
  const [section, setSection] = useState<Section>("control");
  const client = useRider(section);
  const safety = safetyOf(client.status);
  const connection = client.status?.connection;
  const armed = safety.state === "ARMED";
  const { command } = client;
  const stop = useCallback(() => { void command({ action: "stop" }); }, [command]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === "Escape" && !event.repeat) { event.preventDefault(); stop(); } };
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  }, [stop]);
  const armToggle = () => void client.command(armed ? { action: "disarm" } : { action: safety.state === "ESTOP" ? "clear_estop" : "arm", operator_consent: true, operator_plan: t("manual"), owner: "manual", arming_mode: "operator" });
  const changeSection = (next: Section) => { client.hold.release(); setSection(next); };
  return <main className={styles.page} data-testid="rider-page" data-theme={theme}>
    <header className={styles.header}>
      <div className={styles.brand}><div className={styles.brandIcon}><Bot size={30} /></div><div><h1 className={styles.title}>{t("title")}</h1><p className={styles.subtitle}>{t("subtitle")}</p></div></div>
      <div className={styles.toolbar}>
        <select aria-label={t("device")} className={styles.select} style={{ width: 185 }} value={client.config?.device_id ?? ""} disabled={!client.config || !!client.busy} onChange={event => void client.selectDevice(event.target.value || null)}><option value="">{t("selectDevice")}</option>{client.config?.devices.map(device => <option key={device.id} value={device.id}>{device.label}{device.mode === "demo" ? ` · ${t("demoTag")}` : ""}</option>)}</select>
        <Pill tone={client.canMutate ? "good" : "warning"}>{client.canMutate ? <Wifi size={12} /> : <WifiOff size={12} />}{t(connection?.status ?? "unknown")}</Pill>
        <Button aria-label={t("refresh")} title={t("refresh")} onClick={() => void client.poll()} disabled={client.loading}><RefreshCw size={15} /></Button>
        <Button className={armed ? styles.armArmed : styles.armDisarmed} title={t("armHint")} aria-pressed={armed} disabled={(!armed && !client.canArm) || !!client.busy} onClick={armToggle} data-testid="rider-arm"><ShieldCheck size={17} />{t(armed ? "disarm" : safety.state === "ESTOP" ? "clearStop" : "arm")}</Button>
        <Button tone="danger" className={styles.stop} onClick={stop} title={t("stopHint")} data-testid="rider-stop"><CircleStop size={20} />{t("stop")}</Button>
      </div>
    </header>
    {connection?.mode === "demo" && <div className={`${styles.banner} ${styles.demo}`} role="status">{t("demo")}</div>}
    {connection?.status === "offline" && <div className={`${styles.banner} ${styles.demo}`}><WifiOff size={18} /><div><strong>{t("offline")}</strong><div>{t("offlineHint")}</div>{connection.last_seen && <div className={styles.small}>{t("lastSeen")}: {formatTime(connection.last_seen, language)}</div>}</div></div>}
    {client.notice && <div className={`${styles.banner} ${styles[client.notice.tone]}`} role={client.notice.tone === "error" ? "alert" : "status"}><div className={styles.noticeText}>{t(client.notice.key)}{client.notice.code && <code className={styles.small}> · {client.notice.code}</code>}</div><Button aria-label={t("close")} onClick={() => client.setNotice(null)}><X size={14} /></Button></div>}
    <nav className={styles.tabs} role="tablist" aria-label={t("title")}>{tabs.map(({ id, Icon }) => <button type="button" key={id} role="tab" aria-selected={section === id} aria-controls={`rider-panel-${id}`} id={`rider-tab-${id}`} className={`${styles.tab} ${section === id ? styles.tabActive : ""}`} onClick={() => changeSection(id)}><Icon size={16} />{t(id)}</button>)}</nav>
    <div id={`rider-panel-${section}`} role="tabpanel" aria-labelledby={`rider-tab-${section}`}>
      {client.loading && !client.status ? <Panel title={t("device")}><Empty>{t("loading")}</Empty></Panel> : !connection?.configured ? <Panel title={t("device")}><Empty><Bot size={40} />{t("unconfigured")}</Empty></Panel> : <>
        {section === "control" && <div className={styles.stack}><div className={styles.grid}><div className={styles.stack}><Panel title={t("manual")} hint={t("armHint")}><ManualPad client={client} t={t} language={language} /></Panel><Posture client={client} t={t} language={language} /></div><div className={styles.stack}><CameraPanel client={client} t={t} language={language} /><Panel title={t("measurements")} hint={t("measurementsHint")}><SensorMetrics status={client.status} t={t} language={language} /></Panel></div></div><RequestHistory status={client.status} t={t} language={language} /></div>}
        {section === "overview" && <Measurements status={client.status} t={t} language={language} />}
        {section === "calibration" && <Calibration key={connection.session} client={client} t={t} language={language} />}
        {section === "resources" && <Resources key={connection.session} client={client} t={t} language={language} />}
        {section === "face" && <FaceStudio key={connection.session} client={client} t={t} language={language} />}
      </>}
    </div>
    {!!Object.keys(client.status?.errors ?? {}).length && <details className={styles.details}><summary>{t("partial")}</summary><pre className={styles.pre}>{JSON.stringify(client.status?.errors, null, 2)}</pre></details>}
    {str(connection?.error) && <div className={styles.meta}>{t("state")}: <code>{connection?.error}</code></div>}
  </main>;
}