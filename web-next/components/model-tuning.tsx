import {InputRegionControls} from "./input-region";
import {inputCovers} from "../lib/input-region";
import { useEffect, useRef, useState } from "react";
import { data, record, type JsonRecord } from "../lib/model";
import { behaviorError, behaviorSwitchBlock } from "../lib/behavior";
import { effectiveTuning, semanticModel, semanticTuning, tuningDraft, tuningDraftStale, tuningModelId, tuningWrite, validTuning, TUNING_BOUNDS, TUNING_FIELDS, type ModelTuningDraft, type TuningField } from "../lib/model-tuning";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Field, Input, Pill, Select } from "./primitives";
import styles from "../rider.module.css";

type ScopedDraft = ModelTuningDraft & { pipelineRevision: number; provider: string };
type Props = { editor?:{draft:JsonRecord|null;onChange:(value:JsonRecord)=>void}; client: RiderClient; policy: JsonRecord | null; goals: JsonRecord; locked: boolean; t: Translator; onApplied: (config: JsonRecord) => void; onRefresh: () => void; onPendingChange?: (pending: boolean) => void };
/** Apply operator perception edits with session, pipeline and policy revision guards. */
export function ModelTuning({ client, policy, goals, locked, t, onApplied, onRefresh, onPendingChange, editor }: Props) {
  const [draft, setDraft] = useState<ScopedDraft | null>(null), [pending, setPending] = useState(false);
  const [failure, setFailure] = useState(""), [saved, setSaved] = useState<number | null>(null);
  const applying = useRef(false), mounted = useRef(true), editBase = useRef<ScopedDraft | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; onPendingChange?.(false); }; }, [onPendingChange]);
  const session = client.config?.session ?? client.status?.connection.session ?? "";
  const pipeline = client.pipeline?.status, provider = pipeline?.profile.analysis_location ?? "";
  const local = provider === "local", host = provider === "venom";
  const displayId = local ? "Rider-Pi · " + (pipeline?.profile.local_engine ?? "—") : host ? tuningModelId(policy) : t("tuning.providerUnknown");
  const id = tuningModelId(policy), semantic = host && semanticModel(id), depth = host && record(policy?.perception).engine === "depth_plane";
  const baseDraft = (): ScopedDraft | null => { const base = tuningDraft(policy, session); return base && pipeline ? { ...base, pipelineRevision: pipeline.revision, provider } : null; };
  const visibleDraft = draft?.session === session && draft.modelId === id ? draft : null;
  const perception = editor ? record(editor.draft?.perception) : visibleDraft?.perception ?? record(policy?.perception), row = semanticTuning(perception, id);
  const effective = effectiveTuning(policy);
  const conflict = !!draft && (tuningDraftStale(draft, policy, session) || draft.pipelineRevision !== pipeline?.revision || draft.provider !== provider);
  const supported = host && (semantic ? policy?.model_tuning_supported === true : depth);
  const changed = (!!editor || !!visibleDraft) && JSON.stringify(perception) !== JSON.stringify(record(policy?.perception));
  const valid = !!visibleDraft && validTuning(visibleDraft) && (visibleDraft.perception.input_region === undefined || inputCovers(visibleDraft.perception.input_region,policy?.corridor) && !(record(policy?.avoidance).enabled === true && record(record(policy?.avoidance).geometry).mode === "ground" && JSON.stringify(record(visibleDraft.perception.input_region).bounds)!==JSON.stringify([0,0,1,1])));
  const block = local ? "tuning.local" : !host ? "tuning.providerUnknown" : !supported ? "tuning.unsupported" : conflict ? "behavior.conflict"
    : behaviorSwitchBlock(client.status, client.controlFresh, goals, locked || pending || !!client.busy);
  const edit = (change: (value: JsonRecord) => JsonRecord) => {
    if(editor?.draft){editor.onChange({...editor.draft,perception:change(record(editor.draft.perception))});return;}
    const base = visibleDraft ?? editBase.current ?? baseDraft();
    if (!base || base.modelId !== id) return;
    setDraft({ ...base, perception: change(base.perception) }); setFailure(""); setSaved(null);
  };
  const semanticEdit = (field: TuningField | "surface", value: number | string | null) => edit(current => ({
    ...current, semantic_models: { ...record(current.semantic_models), [id]: { ...semanticTuning(current, id), [field]: value } },
  }));
  const captureBase = () => { if (!visibleDraft) editBase.current = baseDraft(); };
  const discard = () => { setDraft(null); editBase.current = null; setFailure(""); setSaved(null); onRefresh(); };
  const apply = async () => {
    if (!visibleDraft || !changed || !valid || block || applying.current || !client.canMutate) return;
    const body = client.currentSession() === session ? tuningWrite(visibleDraft, policy, session) : null;
    if (!body) { setFailure("behavior.conflict"); return; }
    applying.current = true; setPending(true); onPendingChange?.(true); setFailure(""); setSaved(null);
    const origin = client.currentSession();
    try {
      const response = await client.mutate("/navigation/config", body, "POST", true);
      if (!mounted.current || client.currentSession() !== origin) return;
      const config = record(data(response).config);
      if (!Number.isSafeInteger(config.revision)) throw new Error("model_tuning_response_invalid");
      onApplied(config); setSaved(Number(config.revision)); setDraft(null); editBase.current = null;
      client.invalidatePerceptionPreview?.(); onRefresh();
    } catch (error) { if (mounted.current && client.currentSession() === origin) setFailure(behaviorError(error)); }
    finally { applying.current = false; if (mounted.current) { setPending(false); onPendingChange?.(false); } }
  };
  const Container=editor?"section":"details";
  return <Container id="rider-model-tuning" className={styles.perceptionModels} data-testid="model-tuning">
    {editor?<h3>{t("tuning.title")}</h3>:<summary>{t("tuning.title")} · <span>{displayId || t("tuning.noModel")}</span></summary>}
    <div className={styles.perceptionBody}>
      {!editor&&<p className={styles.hint}>{t("tuning.hint")}</p>}
      <p className={styles.small} data-testid="tuning-current">{t("tuning.current", { model: displayId || "—", revision: String(policy?.revision ?? "—") })}</p>
      {semantic && <fieldset className={styles.stack} disabled={!supported || pending} onFocusCapture={captureBase}>
        <legend>{t("tuning.semantic")}</legend>
        <Field label={t("tuning.surface")}><Select data-testid="tuning-surface" value={row.surface} onChange={event => semanticEdit("surface", event.target.value)}>
          <option value="floor_only">{t("tuning.floorOnly")}</option><option value="floor_rug">{t("tuning.floorRug")}</option>
        </Select></Field>
        <p className={styles.hint}>{t("tuning.rugHint")}</p>
        {TUNING_FIELDS.map(field => <div className={styles.tuningField} key={field}>
          <Field label={t("tuning." + field)}><Select data-testid={"tuning-mode-" + field} value={row[field] === null ? "inherit" : "custom"} onChange={event => semanticEdit(field, event.target.value === "inherit" ? null : typeof effective?.[field] === "number" ? effective[field] as number : Number.NaN)}>
            <option value="inherit">{t("tuning.inherit")}</option><option value="custom">{t("tuning.custom")}</option>
          </Select></Field>
          <Field label={t("tuning.value")}><Input data-testid={"tuning-" + field} type="number" min={TUNING_BOUNDS[field][0]} max={TUNING_BOUNDS[field][1]} step={field === "min_region_fraction" ? ".0001" : ".01"}
            value={row[field] === null ? "" : Number.isFinite(row[field]) ? row[field]! : ""} placeholder={effective?.[field] === undefined ? "—" : String(effective[field])} disabled={row[field] === null}
            onChange={event => semanticEdit(field, event.target.value === "" ? Number.NaN : Number(event.target.value))}/></Field>
        </div>)}
      </fieldset>}
      {depth && <fieldset className={styles.stack} disabled={pending||!!editor&&locked} onFocusCapture={captureBase}><legend>{t("tuning.depth")}</legend>
        {(["residual_limit", "outlier_limit", "slope_min"] as const).map(field => <Field label={t(field === "residual_limit" ? "perceptionResidual" : field === "outlier_limit" ? "perceptionOutliers" : "perceptionSlope")+` (${field==="residual_limit"?"0.01–0.15":field==="outlier_limit"?"0–2":"1–4"})`} key={field}>
          <Input data-testid={"tuning-" + field} type="number" min={field === "residual_limit" ? .01 : field === "slope_min" ? 1 : 0} max={field === "residual_limit" ? .15 : field === "slope_min" ? 4 : 2} step={field === "residual_limit" ? .005 : .1}
            value={Number.isFinite(perception[field]) ? Number(perception[field]) * (field === "outlier_limit" ? 100 : 1) : ""}
            onChange={event => edit(current => ({ ...current, [field]: event.target.value === "" ? Number.NaN : Number(event.target.value) / (field === "outlier_limit" ? 100 : 1) }))}/>
        </Field>)}
        <InputRegionControls value={perception.input_region} onChange={value=>edit(current=>({...current,input_region:value}))} disabled={policy?.input_region_supported!==true} t={t}/>{policy?.input_region_supported!==true&&<p className={styles.hint}>{t("inputRegion.unsupported")}</p>}
        <p className={styles.hint}>{t("perceptionGeometryHint")}</p>
      </fieldset>}
      {host && !semantic && !depth && <p className={styles.hint}>{t("tuning.noModel")}</p>}
      {host && effective && <details data-testid="tuning-effective"><summary>{t("behavior.technicalDetails")}</summary><strong>{t("tuning.effective")}</strong><pre className={styles.pre}>{JSON.stringify(effective, null, 2)}</pre></details>}
      {host && visibleDraft && <Pill tone={valid && !conflict ? "warning" : "danger"}>{t(valid && !conflict ? "unsaved" : "invalidDraft")}</Pill>}
      {!editor && !!(failure || block) && <p role={failure || conflict ? "alert" : "status"} data-testid="tuning-message" className={failure || conflict ? styles.loopError : styles.hint}>{t(failure || block, { model: pipeline?.profile.local_engine ?? "—" })}</p>}
      {saved !== null && saved === policy?.revision && <p data-testid="tuning-saved" role="status" className={styles.hint}>{t("tuning.saved", { revision: saved })}</p>}
      {!editor&&<div className={styles.flex}><Button data-testid="tuning-apply" disabled={!changed || !valid || !!block || !client.canMutate} onClick={() => void apply()}>{t(pending ? "behavior.saving" : "tuning.apply")}</Button><Button data-testid="tuning-refresh" disabled={pending} onClick={discard}>{t(visibleDraft ? "behavior.discard" : "refresh")}</Button></div>}
    </div>
  </Container>;
}
