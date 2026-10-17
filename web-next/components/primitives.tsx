import { useEffect, useId, useState, type ButtonHTMLAttributes, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button as VenomButton } from "@/components/ui/button";
import { Info } from "lucide-react";
import type { NumericDescriptor } from "../lib/model";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function Panel({ title, hint, extra, children, className = "" }: { title: string; hint?: string; extra?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`${styles.panel} ${className}`}><div className={styles.panelHeader}><h2 className={styles.panelTitle}>{title}</h2>{extra}</div>{hint && <Hint>{hint}</Hint>}{children}</section>;
}
/** Two action sizes; tooltips add no wrapper or hit target around the button. */
export function Button({ tone, size = "small", title, children, className = "", onMouseEnter, onMouseLeave, onFocus, onBlur, "aria-describedby": describedBy, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "primary" | "danger" | "caution"; size?: "small" | "large" }) {
  const tooltipId = useId();
  const [position, setPosition] = useState<CSSProperties | null>(null);
  const show = (target: HTMLButtonElement) => {
    if (!title || props.disabled) return;
    const rect = (target.closest("header") ?? target).getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    setPosition({ width, left: Math.max(12, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 12)), ...(rect.bottom + 140 > window.innerHeight ? { bottom: window.innerHeight - rect.top + 8 } : { top: rect.bottom + 8 }) });
  };
  useEffect(() => {
    if (!position) return;
    const close = () => setPosition(null);
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("resize", close); window.removeEventListener("keydown", key); };
  }, [position]);
  const visible = !!title && !!position && !props.disabled;
  return <><VenomButton variant={tone === "danger" ? "danger" : tone === "caution" ? "warning" : tone === "primary" ? "primary" : "secondary"} size={size === "large" ? "md" : "sm"} className={`${styles.button} ${size === "large" ? styles.buttonLarge : ""} ${className}`} data-size={size} title={props.disabled ? title : undefined} aria-describedby={[describedBy, visible ? tooltipId : undefined].filter(Boolean).join(" ") || undefined}
    onMouseEnter={event => { show(event.currentTarget); onMouseEnter?.(event); }} onMouseLeave={event => { setPosition(null); onMouseLeave?.(event); }}
    onFocus={event => { show(event.currentTarget); onFocus?.(event); }} onBlur={event => { setPosition(null); onBlur?.(event); }} {...props}>{children}</VenomButton>
    {visible && createPortal(<span role="tooltip" id={tooltipId} className={styles.tooltip} style={position!}>{title}</span>, document.body)}</>;
}
export function Hint({ children }: { children: ReactNode }) {
  return <p className={`${styles.hint} ${styles.help}`}><Info size={16} aria-hidden="true"/><span>{children}</span></p>;
}
export function Pill({ children, tone }: { children: ReactNode; tone?: "good" | "warning" | "danger" }) {
  return <span className={`${styles.pill} ${tone === "good" ? styles.pillGood : tone === "warning" ? styles.pillWarning : tone === "danger" ? styles.pillDanger : ""}`}>{children}</span>;
}
export function Field({ label, children }: { label: string; children: ReactNode }) { return <label className={styles.field}><span>{label}</span>{children}</label>; }
export function NumberField({ label, value, descriptor, onChange, disabled }: { label: string; value: number; descriptor: NumericDescriptor; onChange: (value: number) => void; disabled?: boolean }) {
  const factor = descriptor.display_factor ?? 1;
  const min = descriptor.minimum === undefined ? undefined : descriptor.minimum * factor;
  const max = descriptor.maximum === undefined ? undefined : descriptor.maximum * factor;
  const step = (descriptor.step ?? 10 ** -(descriptor.display_precision ?? 2) / factor) * factor;
  return <Field label={`${label}${descriptor.display_unit ? ` · ${descriptor.display_unit}` : ""}`}><input className={styles.input} type="number" value={Number.isFinite(value) ? Number((value * factor).toFixed(6)) : ""} min={min} max={max} step={step} onChange={event => onChange(event.target.value === "" ? Number.NaN : Number(event.target.value) / factor)} disabled={disabled} /></Field>;
}
export function Details({ value, t }: { value: unknown; t: Translator }) { return <details className={styles.details}><summary>{t("details")}</summary><pre className={styles.pre}>{JSON.stringify(value, null, 2)}</pre></details>; }
export function Empty({ children }: { children: ReactNode }) { return <div className={styles.empty}>{children}</div>; }