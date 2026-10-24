import { useEffect, useRef, useState } from "react";
import { data, type JsonRecord } from "../lib/model";
import { BEHAVIOR_IDS, behaviorCatalog, behaviorDraft, behaviorDraftStale, behaviorError, behaviorSwitchBlock, behaviorWrite, behaviorEditDraft, editBehaviorParameter, sameBehavior, validBehaviorDraft, behaviorParameterValues, type BehaviorDraft } from "../lib/behavior";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Field, Input, Pill, Select } from "./primitives";
import styles from "../rider.module.css";

type Props = { client: RiderClient; policy: JsonRecord | null; goals: JsonRecord; locked: boolean; t: Translator; onApplied: (config: JsonRecord) => void; onRefresh: () => void; onPendingChange?: (pending: boolean) => void };
export function NavigationProfiles({ client, policy, goals, locked, t, onApplied, onRefresh, onPendingChange }: Props) {
  const [draft, setDraft] = useState<BehaviorDraft | null>(null);
  const [pending, setPending] = useState(false), [failure, setFailure] = useState(""), [saved, setSaved] = useState<number | null>(null);
  const editBase = useRef<BehaviorDraft | null>(null), applying = useRef(false);
  useEffect(() => () => onPendingChange?.(false), [onPendingChange]);
  const session = client.config?.session ?? client.status?.connection.session ?? "";
  const catalog = behaviorCatalog(policy);
  const visibleDraft = draft?.session === session ? draft : null;
  const selected = visibleDraft?.selection.profile_id ?? catalog.active.profile_id;
  const candidate = catalog.profiles.find(row => row.id === selected);
  const local = client.pipeline?.status?.profile.analysis_location === "local";
  const localModel = client.pipeline?.status?.profile.local_engine ?? "—";
  const conflict = !!visibleDraft && behaviorDraftStale(visibleDraft, policy, session);
  const changed = !!visibleDraft && !sameBehavior(visibleDraft.selection, catalog.active);
  const invalid = !!visibleDraft && !validBehaviorDraft(visibleDraft, policy);
  const selection = visibleDraft?.selection ?? catalog.active;
  const custom = !!Object.keys(selection.overrides ?? {}).length;
  const values = behaviorParameterValues(selection, policy);
  const profileName = (value: typeof selection) => Object.keys(value.overrides ?? {}).length
    ? t("behavior.custom", { profile: t("behavior.profile." + value.profile_id) }) : t("behavior.profile." + value.profile_id);
  const editParameter = (key: string, value: number | boolean) => {
    if (!policy) return;
    const base = visibleDraft ?? editBase.current ?? behaviorEditDraft(policy, session);
    if (base) setDraft(editBehaviorParameter(base, policy, key, value));
    setFailure(""); setSaved(null);
  };
  const block = invalid ? "behavior.invalidParameters" : !catalog.supported ? "behavior.unsupported" : conflict ? "behavior.conflict" : local && selected !== "legacy" ? "behavior.localOnly" : behaviorSwitchBlock(client.status, client.controlFresh, goals, locked || pending || !!client.busy);
  const discard = () => { editBase.current = null; setDraft(null); setFailure(""); setSaved(null); onRefresh(); };
  const apply = async () => {
    if (!visibleDraft || !changed || block || applying.current || !client.canMutate) return;
    const body = client.currentSession() === visibleDraft.session ? behaviorWrite(visibleDraft, policy, session) : null;
    if (!body) { setFailure("behavior.conflict"); return; }
    applying.current = true; setPending(true); onPendingChange?.(true); setFailure(""); setSaved(null);
    const origin = client.currentSession();
    try {
      const response = await client.mutate("/navigation/config", body, "POST", true);
      if (client.currentSession() !== origin) return;
      const config = data(response).config;
      if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error("navigation_profile_response_invalid");
      onApplied(config as JsonRecord); setSaved(Number((config as JsonRecord).revision)); setDraft(null); editBase.current = null;
      client.invalidatePerceptionPreview?.(); onRefresh();
    } catch (error) { if (client.currentSession() === origin) setFailure(behaviorError(error)); }
    finally { applying.current = false; setPending(false); onPendingChange?.(false); }
  };
  return <section className={styles.navigationProfiles} aria-label={t("behavior.title")} data-testid="navigation-profiles">
    <div className={styles.perceptionSummary} data-testid="behavior-summary"><strong>{t("behavior.title")}</strong><Pill tone={catalog.active.profile_id === "legacy" ? undefined : "warning"}>{profileName(catalog.active)} · r{catalog.active.profile_revision}</Pill></div>
    <div className={styles.perceptionBody}>

    <div className={styles.perceptionSelection}>
      <Field label={t("behavior.select")}><Select data-testid="behavior-profile" value={selected} disabled={!catalog.supported || locked || pending}
        onFocus={() => { if (policy && !visibleDraft) editBase.current = behaviorDraft(policy, catalog.active.profile_id, session); }}
        onBlur={() => { editBase.current = null; }}
        onChange={event => {
          if (!policy) return;
          const next = behaviorDraft(policy, event.target.value, session), base = visibleDraft ?? editBase.current;
          setDraft(next && base ? { ...base, selection: next.selection } : next); setFailure(""); setSaved(null);
        }}>
        {(catalog.supported ? catalog.profiles.map(row => row.id) : BEHAVIOR_IDS).map(id => <option key={id} value={id} disabled={!catalog.supported}>{t("behavior.profile." + id)}</option>)}
      </Select></Field>
      <Button data-testid="behavior-apply" disabled={!changed || !!block || !client.canMutate} title={t("behavior.applyHint")} onClick={() => void apply()}>{t(pending ? "behavior.saving" : "behavior.apply")}</Button>
      <Button data-testid="behavior-refresh" disabled={pending} onClick={discard}>{t(visibleDraft ? "behavior.discard" : "behavior.refresh")}</Button>
    </div>
    <p className={styles.small} data-testid="behavior-current">{t("behavior.current", { profile: profileName(catalog.active), revision: String(policy?.revision ?? "—") })}</p>
    {visibleDraft && <p className={styles.hint} data-testid="behavior-draft">{t("behavior.draft", { profile: profileName(selection) })}</p>}
    {!!(failure || block) && <p className={failure || conflict ? styles.loopError : styles.hint} role={failure || conflict ? "alert" : "status"} data-testid="behavior-message">{t(failure || block, { model: localModel })}</p>}
    {saved !== null && policy?.revision === saved && <p className={styles.hint} role="status" data-testid="behavior-saved">{t("behavior.saved", { revision: String(saved) })}</p>}
    {local && !(failure || block === "behavior.localOnly") && <p className={styles.hint} data-testid="behavior-provider-hint">{t("behavior.localOnly", { model: localModel })}</p>}
    <details className={styles.details}><summary>{t("behavior.parameters")}</summary>
    <p className={styles.hint}>{t("behavior.hint")}</p>
    {catalog.editable && selected !== "legacy" && <>
      <p className={styles.hint}>{t("behavior.customHint")}</p>
      <div className={styles.flex}>
        <Button data-testid="behavior-customize" disabled={locked || pending || !!visibleDraft} onClick={() => { if (policy) setDraft(behaviorEditDraft(policy, session)); }}>{t("behavior.customize")}</Button>
        <Button data-testid="behavior-reset-preset" disabled={locked || pending} onClick={() => { if (policy) setDraft(behaviorDraft(policy, selected, session)); }}>{t("behavior.resetPreset")}</Button>
      </div>
      {selection.profile_revision === 2 && <div data-testid="behavior-editor" onFocusCapture={() => { if (policy && !visibleDraft) editBase.current = behaviorEditDraft(policy, session); }}>
        {["evidence", "temporal", "persistence", "objects"].map(group => <fieldset key={group} disabled={locked || pending} className={styles.parameterGroup}>
          <legend>{t("behavior.group." + group)}</legend>
          {group === "evidence" && <p className={styles.hint}>{t("behavior.evidenceHint")}</p>}
          {group === "temporal" && <p className={styles.hint}>{t("behavior.temporalHint")}</p>}
          {group === "persistence" && <p className={styles.hint}>{t("behavior.persistenceHint", { required: String(values.obstacle_support_frames), window: String(values.obstacle_window_frames) })}</p>}
          {group === "objects" && <p className={styles.hint}>{t("behavior.objectsHint")}</p>}
          <div className={styles.grid3}>{Object.entries(catalog.parameterSchema ?? {}).filter(([key, spec]) => spec.group === group && (key !== "pixel_confidence" || (policy?.perception as JsonRecord)?.engine === "segformer")).map(([key, spec]) => <Field key={key} label={t("behavior.param." + key)}>
            {spec.kind === "bool" ? <input data-testid={"behavior-param-" + key} type="checkbox" checked={values[key] === true} onChange={e => editParameter(key, e.target.checked)}/>
              : <Input data-testid={"behavior-param-" + key} type="number" min={spec.min} max={spec.max} step={spec.step} value={typeof values[key] === "number" && Number.isFinite(values[key]) ? Number(values[key]) : ""} onChange={e => editParameter(key, e.target.value === "" ? Number.NaN : Number(e.target.value))}/>}
          </Field>)}</div>
        </fieldset>)}
        <p role="status" data-testid="behavior-custom-state">{t(custom ? "behavior.custom" : "behavior.profile." + selected, { profile: t("behavior.profile." + selected) })}</p>
      </div>}
    </>}
    <p className={styles.hint} data-testid="behavior-description">{t("behavior.description." + selected)}</p>
    {candidate?.status === "experimental" && <p className={styles.hint}>{t("behavior.experimentalHint")}</p>}
    {catalog.supported && <details data-testid="behavior-technical"><summary>{t("behavior.technicalDetails")}</summary>
      <p className={styles.small}>{t("behavior.effectiveHash")}: <code>{catalog.effective_policy_sha256}</code></p>
      <pre className={styles.pre} data-testid="behavior-parameters">{JSON.stringify(catalog.activeParameters ?? candidate?.parameters ?? {}, null, 2)}</pre>
    </details>}
    </details>
    </div>
  </section>;
}
