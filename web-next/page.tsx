"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Bot, Boxes, BrainCircuit, CircleStop, RefreshCw, ShieldCheck, SlidersHorizontal, Smile, Wifi, WifiOff, Maximize2, Minimize2 } from "lucide-react";
import { useLanguage } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { translate } from "./lib/i18n";
import { safetyOf, str, type Section } from "./lib/model";
import { useRider } from "./lib/use-rider";
import { Calibration } from "./components/calibration";
import { Posture } from "./components/control";
import { FaceStudio } from "./components/face";
import { Measurements, RequestHistory, SensorMetrics } from "./components/measurements";
import { Button, Empty, Panel, Pill } from "./components/primitives";
import { Resources } from "./components/resources";
import { NavigationLoop } from "./components/navigation-loop";
import { Compute } from "./components/compute";
import { SamplesBrowser } from "./components/samples-browser";
import { OperatorConsole, RiderMessages } from "./components/operator-console";
import styles from "./rider.module.css";

const tabs = [{ id: "control", Icon: Bot }, { id: "overview", Icon: Activity }, { id: "calibration", Icon: SlidersHorizontal }, { id: "resources", Icon: Boxes }, { id: "face", Icon: Smile }, { id: "compute", Icon: BrainCircuit }] as const;
export default function RiderPage() {
  const { language } = useLanguage();
  const { theme } = useTheme();
  const t = useCallback((key: string, replacements?: Record<string, string | number>) => translate(language, key, replacements), [language]);
  const [section, setSection] = useState<Section>("control");
  const pageRef = useRef<HTMLElement>(null);
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [focused]);
  useEffect(() => {
    const page = pageRef.current;
    // The optional module sits inside the host's main, below its sticky top bar.
    const hostMain = page?.parentElement?.closest("main");
    if (!page || !hostMain) return;
    const header = page.querySelector("header");
    let observed: Element | null = null;
    const update = () => {
      let topBar = hostMain.previousElementSibling;
      while (topBar && !["sticky", "fixed"].includes(getComputedStyle(topBar).position)) topBar = topBar.previousElementSibling;
      if (topBar !== observed) {
        if (observed) observer?.unobserve(observed);
        observed = topBar;
        if (observed) observer?.observe(observed);
      }
      page.style.setProperty("--rider-page-top", String(Math.max(0, page.getBoundingClientRect().top)) + "px");
      const offset = topBar ? Math.max(0, Number.parseFloat(getComputedStyle(topBar).top) || 0) + topBar.getBoundingClientRect().height : 0;
      page.style.setProperty("--rider-sticky-top", `${offset + 8}px`);
      if (header) page.style.setProperty("--rider-header-height", `${header.getBoundingClientRect().height}px`);
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    if (header) observer?.observe(header);
    const layoutObserver = new MutationObserver(update);
    if (hostMain.parentElement) layoutObserver.observe(hostMain.parentElement, { childList: true });
    update();
    window.addEventListener("resize", update);
    return () => { observer?.disconnect(); layoutObserver.disconnect(); window.removeEventListener("resize", update); page.style.removeProperty("--rider-sticky-top"); page.style.removeProperty("--rider-header-height"); };
  }, []);
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
  return <main ref={pageRef} className={styles.page + (focused ? " " + styles.pageFocused : "")} data-testid="rider-page" data-section={section} data-theme={theme}>
    <header className={styles.header}>
      <div className={styles.brand}><div className={styles.brandIcon}><Bot size={30} /></div><div><h1 className={styles.title}>{t("title")}</h1><p className={styles.subtitle}>{t("subtitle")}</p></div></div>
      <div className={styles.toolbar}>
        <label className={styles.environmentSelect}><span>{t("environment")}</span><select aria-label={t("environment")} title={t("environmentHint")} data-testid="rider-environment" className={styles.select} value={client.config?.device_id ?? ""} disabled={!client.config || !!client.busy} onChange={event => void client.selectDevice(event.target.value || null)}>
          <option value="">{t("selectDevice")}</option>
          {(["demo", "device"] as const).map(mode => {
            const devices = client.config?.devices.filter(device => device.mode === mode) ?? [];
            return devices.length ? devices.map(device => <option key={device.id} value={device.id}>{t(mode === "demo" ? "environmentDemo" : "environmentProd")}{devices.length > 1 ? ` · ${device.label}` : ""}</option>) : <option key={mode} disabled value={`unavailable-${mode}`}>{t(mode === "demo" ? "environmentDemo" : "environmentProd")} · {t("notConfigured")}</option>;
          })}
        </select></label>
        <Pill tone={client.canMutate ? "good" : "warning"}>{client.canMutate ? <Wifi size={12} /> : <WifiOff size={12} />}{t(connection?.status ?? "unknown")}</Pill>
        <Button aria-label={t("refresh")} title={t("refresh")} onClick={() => void client.poll()} disabled={client.loading}><RefreshCw size={15} /></Button>
        <Button className={armed ? styles.armArmed : styles.armDisarmed} title={t("armHint")} aria-pressed={armed} disabled={(!armed && !client.canArm) || !!client.busy} onClick={armToggle} data-testid="rider-arm"><ShieldCheck size={17} />{t(armed ? "disarm" : safety.state === "ESTOP" ? "clearStop" : "arm")}</Button>
        <Button tone="danger" className={styles.stop} onClick={stop} aria-label={t("emergencyStop")} title={t("stopHint")} data-testid="rider-stop"><CircleStop size={20} />{t("stop")}</Button>
        <Button aria-label={t(focused ? "workspaceRestore" : "workspaceExpand")} title={t(focused ? "workspaceRestore" : "workspaceExpand")} disabled={!!client.activeDirection} onClick={() => setFocused(value => !value)} data-testid="workspace-expand">{focused ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</Button>
      </div>
    </header>
    {section !== "control" && <RiderMessages client={client} t={t} language={language} />}
    <nav className={styles.tabs} role="tablist" aria-label={t("title")}>{tabs.map(({ id, Icon }) => <button type="button" key={id} role="tab" aria-selected={section === id} aria-controls={`rider-panel-${id}`} id={`rider-tab-${id}`} className={`${styles.tab} ${section === id ? styles.tabActive : ""}`} onClick={() => changeSection(id)}><Icon size={16} />{t(id)}</button>)}</nav>
    <div className={section === "control" ? styles.controlPanel : undefined} id={`rider-panel-${section}`} role="tabpanel" aria-labelledby={`rider-tab-${section}`}>
      {client.loading && !client.status ? <Panel title={t("device")}><Empty>{t("loading")}</Empty></Panel> : !connection?.configured ? <Panel title={t("device")}><Empty><Bot size={40} />{t("unconfigured")}</Empty></Panel> : <>
        {section === "control" && <NavigationLoop key={connection.session} client={client} t={t} language={language}
          manual={<OperatorConsole client={client} t={t} language={language} />}
          auxiliary={<details className={styles.details}><summary>{t("postureAndTelemetry")}</summary><div className={styles.grid}><Posture client={client} t={t} language={language}/><Panel title={t("measurements")} hint={t("measurementsHint")}><SensorMetrics status={client.status} t={t} language={language}/></Panel></div><RequestHistory status={client.status} t={t} language={language}/></details>}
        />}
        {section === "overview" && <Measurements status={client.status} t={t} language={language} />}
        {section === "calibration" && <Calibration key={connection.session} client={client} t={t} language={language} />}
        {section === "resources" && <Resources key={connection.session} client={client} t={t} language={language} />}
        {section === "face" && <FaceStudio key={connection.session} client={client} t={t} language={language} />}
        {section === "compute" && <Compute key={connection.session} client={client} t={t} language={language} />}
      </>}
    </div>
    {section === "compute" && <SamplesBrowser t={t} language={language}/>}
    {section !== "control" && !!Object.keys(client.status?.errors ?? {}).length && <details className={styles.details}><summary>{t("partial")}</summary><pre className={styles.pre}>{JSON.stringify(client.status?.errors, null, 2)}</pre></details>}
    {section !== "control" && str(connection?.error) && <div className={styles.meta}>{t("state")}: <code>{connection?.error}</code></div>}
  </main>;
}
