import {InputRegionControls} from "./input-region";
import {inputCovers} from "../lib/input-region";
import { useEffect, useRef, useState } from "react";
import { scalarText, data, record, type JsonRecord } from "../lib/model";
import { behaviorError, behaviorSwitchBlock } from "../lib/behavior";
import { effectiveTuning, semanticModel, semanticTuning, tuningDraft, tuningDraftStale, tuningModelId, tuningWrite, validTuning, TUNING_BOUNDS, TUNING_FIELDS, type ModelTuningDraft, type TuningField } from "../lib/model-tuning";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Field, Input, Pill, Select } from "./primitives";
import styles from "../rider.module.css";

/** Device depth controls keep their displayed percentage scale and numeric ranges. */
const depthFields={
  residual_limit:{label:"perceptionResidual",range:"0.01–0.15",minimum:.01,maximum:.15,step:.005,scale:1},
  outlier_limit:{label:"perceptionOutliers",range:"0–2",minimum:0,maximum:2,step:.1,scale:100},
  slope_min:{label:"perceptionSlope",range:"1–4",minimum:1,maximum:4,step:.1,scale:1},
} as const;
type ScopedDraft = ModelTuningDraft & { pipelineRevision: number; provider: string };
type Props = { editor?:{draft:JsonRecord|null;onChange:(value:JsonRecord)=>void}; client: RiderClient; policy: JsonRecord | null; goals: JsonRecord; locked: boolean; t: Translator; onApplied: (config: JsonRecord) => void; onRefresh: () => void; onPendingChange?: (pending: boolean) => void };
/** Apply operator perception edits with session, pipeline and policy revision guards. */
export function ModelTuning({ client, policy, goals, locked, t, onApplied, onRefresh, onPendingChange, editor }: Props) {
  /** Render TuningFeedback with the captured state and unchanged event handlers. */
  function renderTuningFeedback(){
  /** Render TuningActions from this render's captured facts. */
  function renderTuningActions(){
    return (!editor&&<div className={styles.flex}><Button data-testid="tuning-apply" disabled={!changed || !valid || !!block || !client.canMutate} onClick={() => void apply()}>{t(pending ? "behavior.saving" : "tuning.apply")}</Button><Button data-testid="tuning-refresh" disabled={pending} onClick={discard}>{t(visibleDraft ? "behavior.discard" : "refresh")}</Button></div>);
  }

  /** Render TuningMessage from this render's captured facts. */
  function renderTuningMessage(){
    return (!editor && !!(failure || block) && <p role={failure || conflict ? "alert" : "status"} data-testid="tuning-message" className={failure || conflict ? styles.loopError : styles.hint}>{t(failure || block, { model: pipeline?.profile.local_engine ?? "—" })}</p>);
  }

    return (<div className={styles.perceptionBody}>
      {!editor&&<p className={styles.hint}>{t("tuning.hint")}</p>}
      <p className={styles.small} data-testid="tuning-current">{t("tuning.current", { model: displayId || "—", revision: scalarText(policy?.revision ?? "—") })}</p>
      {renderSemanticTuning()}
      {renderDepthTuning()}
      {host && !semantic && !depth && <p className={styles.hint}>{t("tuning.noModel")}</p>}
      {host && effective && <details data-testid="tuning-effective"><summary>{t("behavior.technicalDetails")}</summary><strong>{t("tuning.effective")}</strong><pre className={styles.pre}>{JSON.stringify(effective, null, 2)}</pre></details>}
      {host && visibleDraft && <Pill tone={valid && !conflict ? "warning" : "danger"}>{t(valid && !conflict ? "unsaved" : "invalidDraft")}</Pill>}
      {renderTuningMessage()}
      {saved !== null && saved === policy?.revision && <p data-testid="tuning-saved" role="status" className={styles.hint}>{t("tuning.saved", { revision: saved })}</p>}
      {renderTuningActions()}
    </div>);
  }

  /** Derive block from the current captured render without effects. */
  function modelTuningBlock(){return (local ? "tuning.local" : !host ? "tuning.providerUnknown" : !supported ? "tuning.unsupported" : conflict ? "behavior.conflict"
    : behaviorSwitchBlock(client.status, client.controlFresh, goals, locked || pending || !!client.busy));}

  /** Derive valid from the current captured render without effects. */
  function validModelDraft(){return (!!visibleDraft && validTuning(visibleDraft) && (visibleDraft.perception.input_region === undefined || inputCovers(visibleDraft.perception.input_region,policy?.corridor) && !(record(policy?.avoidance).enabled === true && record(record(policy?.avoidance).geometry).mode === "ground" && JSON.stringify(record(visibleDraft.perception.input_region).bounds)!==JSON.stringify([0,0,1,1]))));}

  /** Derive displayId from the current captured render without effects. */
  function displayModelId(){return (local ? "Rider-Pi · " + (pipeline?.profile.local_engine ?? "—") : host ? tuningModelId(policy) : t("tuning.providerUnknown"));}

  /** Render the DepthTuning section using this render's captured state. */
  function renderDepthTuning(){
    return (depth && <fieldset className={styles.stack} disabled={pending||!!editor&&locked} onFocusCapture={captureBase}><legend>{t("tuning.depth")}</legend>
        {(["residual_limit","outlier_limit","slope_min"] as const).map(field=>{
          const spec=depthFields[field];
          return <Field label={t(spec.label)+` (${spec.range})`} key={field}>
            <Input data-testid={"tuning-"+field} type="number" min={spec.minimum} max={spec.maximum} step={spec.step}
              value={Number.isFinite(perception[field])?Number(perception[field])*spec.scale:""}
              onChange={event=>edit(current=>({...current,[field]:event.target.value===""?Number.NaN:Number(event.target.value)/spec.scale}))}/>
          </Field>;
        })}
        <InputRegionControls value={perception.input_region} onChange={value=>edit(current=>({...current,input_region:value}))} disabled={policy?.input_region_supported!==true} t={t}/>{policy?.input_region_supported!==true&&<p className={styles.hint}>{t("inputRegion.unsupported")}</p>}
        <p className={styles.hint}>{t("perceptionGeometryHint")}</p>
      </fieldset>);
  }

  /** Render the SemanticTuning section using this render's captured state. */
  function renderSemanticTuning(){
    return (semantic && <fieldset className={styles.stack} disabled={!supported || pending} onFocusCapture={captureBase}>
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
            value={row[field] === null ? "" : Number.isFinite(row[field]) ? row[field]! : ""} placeholder={effective?.[field] === undefined ? "—" : scalarText(effective[field])} disabled={row[field] === null}
            onChange={event => semanticEdit(field, event.target.value === "" ? Number.NaN : Number(event.target.value))}/></Field>
        </div>)}
      </fieldset>);
  }

  const [draft, setDraft] = useState<ScopedDraft | null>(null), [pending, setPending] = useState(false);
  const [failure, setFailure] = useState(""), [saved, setSaved] = useState<number | null>(null);
  const applying = useRef(false), mounted = useRef(true), editBase = useRef<ScopedDraft | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; onPendingChange?.(false); }; }, [onPendingChange]);
  const session = client.config?.session ?? client.status?.connection.session ?? "";
  const pipeline = client.pipeline?.status, provider = pipeline?.profile.analysis_location ?? "";
  const local = provider === "local", host = provider === "venom";
  const displayId = displayModelId();
  const id = tuningModelId(policy), semantic = host && semanticModel(id), depth = host && record(policy?.perception).engine === "depth_plane";
  const baseDraft = (): ScopedDraft | null => { const base = tuningDraft(policy, session); return base && pipeline ? { ...base, pipelineRevision: pipeline.revision, provider } : null; };
  const visibleDraft = draft?.session === session && draft.modelId === id ? draft : null;
  const perception = editor ? record(editor.draft?.perception) : visibleDraft?.perception ?? record(policy?.perception), row = semanticTuning(perception, id);
  const effective = effectiveTuning(policy);
  const conflict = !!draft && (tuningDraftStale(draft, policy, session) || draft.pipelineRevision !== pipeline?.revision || draft.provider !== provider);
  const supported = host && (semantic ? policy?.model_tuning_supported === true : depth);
  const changed = (!!editor || !!visibleDraft) && JSON.stringify(perception) !== JSON.stringify(record(policy?.perception));
  const valid = validModelDraft();
  const block = modelTuningBlock();
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
    {renderTuningFeedback()}
  </Container>;
}
