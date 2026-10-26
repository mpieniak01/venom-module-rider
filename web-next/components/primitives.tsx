import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type CSSProperties, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button as VenomButton } from "@/components/ui/button";
import { Info } from "lucide-react";
import { Input as VenomInput } from "@/components/ui/input";
import { descriptorRange } from "../lib/field-range";
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
  const tipTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const close = () => { if (tipTimer.current) clearTimeout(tipTimer.current); tipTimer.current=null; setPosition(null); };
  useEffect(() => () => { if (tipTimer.current) clearTimeout(tipTimer.current); }, []);
  const show = (target: HTMLButtonElement) => {
    if (!title || props.disabled) return;
    const rect = target.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    close();
    tipTimer.current=setTimeout(() => {
      tipTimer.current=null;
      setPosition({ width, left: Math.max(12, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 12)), ...(rect.bottom + 140 > window.innerHeight ? { bottom: window.innerHeight - rect.top + 8 } : { top: rect.bottom + 8 }) });
    }, 650);
  };
  useEffect(() => {
    if (!position) return;
    const close = () => { if(tipTimer.current)clearTimeout(tipTimer.current);tipTimer.current=null;setPosition(null); };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("resize", close); window.removeEventListener("keydown", key); };
  }, [position]);
  const visible = !!title && !!position && !props.disabled;
  return <><VenomButton variant={tone === "danger" ? "danger" : tone === "caution" ? "warning" : tone === "primary" ? "primary" : "secondary"} size={size === "large" ? "md" : "sm"} className={`${styles.button} ${size === "large" ? styles.buttonLarge : ""} ${className}`} data-size={size} title={props.disabled ? title : undefined} aria-describedby={[describedBy, visible ? tooltipId : undefined].filter(Boolean).join(" ") || undefined}
    onMouseEnter={event => { show(event.currentTarget); onMouseEnter?.(event); }} onMouseLeave={event => { close(); onMouseLeave?.(event); }}
    onFocus={event => { if(event.currentTarget.matches(":focus-visible"))show(event.currentTarget); onFocus?.(event); }} onBlur={event => { close(); onBlur?.(event); }} onPointerDown={close} {...props}>{children}</VenomButton>
    {visible && createPortal(<span role="tooltip" id={tooltipId} className={styles.tooltip} style={position!}>{title}</span>, document.body)}</>;
}
export function Hint({ children }: { children: ReactNode }) {
  return <p className={`${styles.hint} ${styles.help}`}><Info size={16} aria-hidden="true"/><span>{children}</span></p>;
}
export function Pill({ children, tone }: { children: ReactNode; tone?: "good" | "warning" | "danger" }) {
  return <span className={`${styles.pill} ${tone === "good" ? styles.pillGood : tone === "warning" ? styles.pillWarning : tone === "danger" ? styles.pillDanger : ""}`}>{children}</span>;
}
export function Field({ label, children }: { label: string; children: ReactNode }) { return <label className={styles.field}><span>{label}</span>{children}</label>; }
/** Theme-aware Venom input; native attributes and change events are preserved. */
export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <VenomInput className={`${styles.formControl} ${className}`} data-rider-control="input" {...props}/>;
}
/** Keep native option values, disabled options and keyboard selection semantics. */
export function Select({ className = "", ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={`${styles.formControl} ${styles.formSelect} ${className}`} data-rider-control="select" {...props}/>;
}
/** Multiline counterpart of the same field, with a resizable text area. */
export function Textarea({ className = "", rows = 3, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${styles.formControl} ${styles.formTextarea} ${className}`} data-rider-control="textarea" rows={rows} {...props}/>;
}
/** Preserve checked/change and native fieldset-disabled behavior. */
export function Checkbox({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${styles.formCheckbox} ${className}`} data-rider-control="checkbox" {...props} type="checkbox"/>;
}
/** One shared range appearance without changing units or producer bounds. */
export function Range({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${styles.formRange} ${className}`} data-rider-control="range" {...props} type="range"/>;
}
export function NumberField({ label, value, descriptor, onChange, disabled, compact = false, testId }: { testId?:string; label: string; value: number; descriptor: NumericDescriptor; onChange: (value: number) => void; disabled?: boolean; compact?: boolean }) {
  const factor = descriptor.display_factor ?? 1;
  const min = descriptor.minimum === undefined ? undefined : descriptor.minimum * factor;
  const max = descriptor.maximum === undefined ? undefined : descriptor.maximum * factor;
  const step = (descriptor.step ?? 10 ** -(descriptor.display_precision ?? 2) / factor) * factor;
  return <Field label={`${label}${descriptorRange(descriptor) ? ` (${descriptorRange(descriptor)})` : ""}`}><Input data-testid={testId} className={compact ? styles.numericInput : undefined} type="number" value={Number.isFinite(value) ? Number((value * factor).toFixed(6)) : ""} min={min} max={max} step={step} onChange={event => onChange(event.target.value === "" ? Number.NaN : Number(event.target.value) / factor)} disabled={disabled} /></Field>;
}
export function Details({ value, t }: { value: unknown; t: Translator }) { return <details className={styles.details}><summary>{t("details")}</summary><pre className={styles.pre}>{JSON.stringify(value, null, 2)}</pre></details>; }
export function Empty({ children }: { children: ReactNode }) { return <div className={styles.empty}>{children}</div>; }