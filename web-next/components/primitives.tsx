import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { NumericDescriptor } from "../lib/model";
import type { Translator } from "../lib/i18n";
import styles from "../rider.module.css";

export function Panel({ title, hint, extra, children, className = "" }: { title: string; hint?: string; extra?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`${styles.panel} ${className}`}><div className={styles.panelHeader}><h2 className={styles.panelTitle}>{title}</h2>{extra}</div>{hint && <p className={styles.hint}>{hint}</p>}{children}</section>;
}
export function Button({ tone, children, className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "primary" | "danger" }) {
  return <button type="button" className={`${styles.button} ${tone ? styles[tone] : ""} ${className}`} {...props}>{children}</button>;
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