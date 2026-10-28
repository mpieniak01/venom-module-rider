import { useOperatorTuning } from "../lib/operator-tuning";
import { useCallback, useEffect, useRef, useState } from "react";
import { Download, RefreshCw, Square } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { scalarText, data, record, str, type JsonRecord } from "../lib/model";
import { perceptionCatalog, perceptionMetric, perceptionReason, perceptionRuntime, perceptionSwitchBlock, perceptionSelectionKey, perceptionSelectionIdentityKey, selectionMatchesPrepared, perceptionError, perceptionApplyNeeded, perceptionInstallAction, type PerceptionCatalog } from "../lib/perception";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Field, Pill, Select } from "./primitives";
import styles from "../rider.module.css";

type Props = {
  beforeApply?:()=>Promise<{policy:JsonRecord;token:{session:string;epoch:number;generation:string};wasObserving:boolean}>; embedded?:boolean; client: RiderClient; t: Translator; language: string; policy: JsonRecord | null; goals: JsonRecord;
  locked: boolean; onApplied: (config: JsonRecord) => void; onRefresh: () => void;
};

/** Resolve the selected local engine or host model without changing provider precedence. */
function selectedCatalogModel(catalog:PerceptionCatalog|null):string {
  return catalog?.provider==="local"?catalog.local_models.find(model=>model.engine===catalog.local_engine)?.id??catalog.local_engine:catalog?.selection?.model_id??"";
}
/** Present and apply device-owned model choices with captured-session safeguards. */
export function PerceptionModelsPanel({ client, t, language, policy, goals, locked, onApplied, onRefresh, embedded, beforeApply }: Props) {
  /** Present model selection status and errors separately from the operation lifecycle. */
  function renderModelFeedback(){
    return (<div className={styles.perceptionBody}>
    <div className={styles.perceptionSummary}>
      <div><strong>{t("perception.title")}</strong><p className={styles.small} data-testid="perception-active-model">{t("perception.applied")}: <strong>{catalog ? t("perception.location." + catalog.provider) : "—"} · {selected?.label ?? "—"}</strong> · r{catalog?.pipeline_revision ?? "—"} / {catalog?.selection?.revision ?? "—"}</p></div>
      <Button aria-label={t("perception.refresh")} disabled={!!pending} onClick={clearDraft}><RefreshCw size={14}/>{t("refresh")}</Button>
    </div>
    {renderModelSelection()}
    {!embedded&&<p className={styles.hint}>{t(draftProvider === "local" ? "perception.localOwned" : "perception.hostOwned")}</p>}
    {renderModelRuntime()}
    {block && <p className={styles.hint} role="status" data-testid="perception-switch-block">{t(block)}</p>}
    {candidate && modelBlock && <p className={styles.hint} role="status" data-testid="perception-model-block">{t(modelBlock)}{candidate.reason && <> · <code>{candidate.reason}</code></>}</p>}
    {!!candidate?.qualification_details.length && <ul className={styles.small}>{candidate.qualification_details.map(detail => <li key={detail}>{t(["external_data_missing", "preprocessing_unverified"].includes(detail) ? "perception.qualification." + detail : "perception.reason.review")}</li>)}</ul>}
    {catalog?.selection_error && <p className={styles.loopError} role="alert" data-testid="perception-selection-error">{t("perception.selectionReadFailed")} · <code>{catalog.selection_error}</code></p>}
    {draftProvider === "local" && catalog?.local_models_error && <p className={styles.loopError} role="alert">{t("perception.localReadFailed")} · <code>{catalog.local_models_error}</code></p>}
    {readError && <p className={styles.loopError} role="alert" data-testid="perception-read-error">{t("perception.readFailed")} · <code>{readError}</code></p>}
    {failure && <p className={styles.loopError} role="alert" data-testid="perception-operation-error">{t(perceptionReason(failure))} · <code>{failure}</code></p>}
    {renderModelManagement()}
    </div>);
  }

  /** Render ModelManagement with the captured state and unchanged event handlers. */
  function renderModelManagement(){
  /** Render CandidateInstallation from this render's captured facts. */
  function renderCandidateInstallation(){
    return (candidate && <div className={styles.stack}>
        <p className={styles.small}>{t(draftProvider === "local" ? "perception.availability" : "perception.installation")}: {t("perception.install." + candidate.installation)} · {t("perception.revision")}: <code>{candidate.revision || "N/A"}</code> · {t("perception.provider")}: {candidate.execution_provider || "N/A"}</p>
        {candidate.sha256 && <p className={styles.small}>{t("perception.artifact")}: <code>{candidate.sha256}</code></p>}
        <div className={styles.flex}>
          {readyToInstall && <Button data-testid="perception-install" disabled={!session || !!pending || !!readError} onClick={() => void run("install")} title={t("perception.installHint")}><Download size={14}/>{t(candidate?.installation === "failed" ? "perception.retry" : candidate?.installed ? "perception.verify" : "perception.install")}</Button>}
          {installing && <><span role="status">{t(["waiting", "cancelling"].includes(str(job.state)) ? "perception.job." + str(job.state) : "perception.install." + candidate.installation)} {typeof job.progress === "number" ? Math.round(Math.max(0, Math.min(100, job.progress))) + "%" : ""}</span><Button data-testid="perception-install-cancel" disabled={!session || !!pending} onClick={() => void run("cancel")}><Square size={14}/>{t("perception.cancel")}</Button></>}
        </div>
        {!!job.error && <p className={styles.loopError} role="alert">{t("perception.installFailed")} · <code>{scalarText(job.error)}</code></p>}
      </div>);
  }

    return (<details className={styles.details} data-testid="perception-model-details"><summary>{t("perception.manage")}</summary>
      {renderCandidateInstallation()}
      <div className={styles.perceptionDiagnostics} data-testid="perception-diagnostics">
        <span>{t("perception.inference")}: {perceptionMetric(catalog?.provider === "venom" ? catalog.diagnostics.inference_ms : null, language)} ms</span>
        <span>FPS: {perceptionMetric(catalog?.provider === "venom" ? catalog.diagnostics.fps : null, language)}</span>
        <span>RAM: {perceptionMetric(catalog?.provider === "venom" ? catalog.diagnostics.ram_mb : null, language)} MB</span>
        <span>VRAM: {perceptionMetric(catalog?.provider === "venom" ? catalog.diagnostics.vram_mb : null, language)} MB</span>
      </div>
      <p className={styles.hint}>{t("perception.metricsHint")}</p>
    </details>);
  }

  /** Render ModelRuntime with the captured state and unchanged event handlers. */
  function renderModelRuntime(){
    return (<div className={styles.perceptionStatus} aria-live="polite" data-testid="perception-runtime">
      <Pill tone={runtime === "active" ? "good" : "warning"}>{t("perception.runtime." + runtime)}</Pill>
      {!embedded&&<><span>{t("perception.loaded")}: {active ?? "—"}</span>
      {catalog?.provider === "venom" && <span>{t("perception.effectiveProvider")}: {str(details.execution_provider) || "N/A"}</span>}
      {str(details.error ?? "").startsWith("vision_cuda_") && <span role="status">{t("perception.cudaRequired")}</span>}
      <Pill>{t(selected?.drive_supported === false ? "perception.observeOnly" : "perception.noMotionGrant")}</Pill></>}
    </div>);
  }

  /** Render ModelSelection with the captured state and unchanged event handlers. */
  function renderModelSelection(){
    return (<div className={styles.perceptionSelection}>
      <Field label={t("perception.location")}><Select data-testid="perception-provider-choice" value={draftProvider} disabled={!catalog || !!pending || locked || catalog.switching} onChange={event => {
        const provider = event.target.value; setDraftProvider(provider); setFailure(""); setConflict(false);
        setDraft(provider === "local" ? catalog?.local_models.find(model => model.engine === catalog.local_engine)?.id ?? catalog?.local_models[0]?.id ?? "" : catalog?.selection?.model_id ?? catalog?.models[0]?.id ?? "");
      }}>
        {!draftProvider && <option value="">{t("perception.noSelection")}</option>}
        {draftProvider === "unknown" && <option value="unknown" disabled>{t("perception.location.unknown")}</option>}
        {catalog?.providers.map(provider => <option key={provider.id} value={provider.id} disabled={!provider.available}>{t("perception.location." + provider.id)}</option>)}
      </Select></Field>
      <Field label={t("perception.choose")}><Select data-testid="perception-model-choice" value={draft} disabled={!catalog || !!pending || locked || catalog.switching} onChange={event => { setDraft(event.target.value); setFailure(""); setConflict(false); }}>
        {!draft && <option value="">{t("perception.noSelection")}</option>}
        {models.map(model => <option key={model.id} value={model.id}>{model.label} · {t("perception.install." + model.installation)}</option>)}
      </Select></Field>
      {!embedded&&<Button tone="primary" data-testid="perception-model-apply" disabled={!candidate || !changed || !!block || !!modelBlock} title={t(block || modelBlock || "perception.applyHint")} onClick={() => void run("apply").catch(()=>undefined)}>{t(pending === "apply" ? "perception.applying" : "perception.apply")}</Button>}
    </div>);
  }

  /** Derive active from the current captured render without effects. */
  function activeModelLabel(){return (catalog?.provider === "local"
    ? runtime === "active" ? selected?.label : null
    : selected?.loaded === true && (!details.active_model_id || details.active_model_id === selected.id) ? selected.label : null);}

  /** Derive block from the current captured render without effects. */
  function selectionBlock(){
    if(!beforeApply)return legacyBlock;
    if(conflict)return "perception.reason.conflict";
    if(readError)return "perception.block.catalog";
    if(!client.canMutate)return "loopOffline";
    return pending||catalog?.switching?"behavior.block.busy":"";
  }

  /** Derive modelBlock from the current captured render without effects. */
  function candidateModelBlock(){return (!candidate ? "perception.block.policy" : !candidate.selectable ? draftProvider === "local" && candidate.installation === "unavailable" ? "perception.reason.localUnavailable" : perceptionReason(candidate.reason || "model_not_installed") : "");}

  /** Derive legacyBlock from the current captured render without effects. */
  function legacySelectionBlock(){return (conflict ? "perception.reason.conflict" : readError ? "perception.block.catalog" : perceptionSwitchBlock(client.status, client.controlFresh, goals, policy, locked || !!client.busy || !!pending || !!catalog?.switching || client.pipeline.busy));}

  /** Derive runtime from the current captured render without effects. */
  function selectedModelRuntime(){return (catalog?.provider === "local"
    ? selected?.installation === "unavailable" ? "unavailable" : selected?.loaded === true && client.pipeline.status?.state === "active" && client.pipeline.status.active_profile?.analysis_location === "local" && client.pipeline.status.active_profile.local_engine === selected?.engine ? "active" : client.pipeline.busy ? "loading" : "unloaded"
    : catalog?.provider === "venom" ? perceptionRuntime(catalog) : "unavailable");}

  const [catalog, setCatalog] = useState<PerceptionCatalog | null>(null);
  const workspace=useOperatorTuning();
  const setWorkspaceState=workspace.setState,modelApplyRef=workspace.modelApply;
  const { invalidatePerceptionPreview, currentSession } = client;
  const storedChoice=workspace.state.modelChoice;
  const draft=storedChoice?.id??"",draftProvider=storedChoice?.provider??"";
  const setDraft=useCallback((id:string)=>setWorkspaceState(previous=>({...previous,modelChoice:{id,provider:previous.modelChoice?.provider??"",session:currentSession()}})),[setWorkspaceState,currentSession]);
  const setDraftProvider=useCallback((provider:string)=>setWorkspaceState(previous=>({...previous,modelChoice:{id:previous.modelChoice?.id??"",provider,session:currentSession()}})),[setWorkspaceState,currentSession]);
  const [readError, setReadError] = useState("");
  const [failure, setFailure] = useState("");
  const [conflict, setConflict] = useState(false);
  const [pending, setPending] = useState("");
  const [refresh, setRefresh] = useState(0);
  const mounted = useRef(true), request = useRef(0), applying = useRef(false);
  const selectionRef = useRef(""), catalogRef = useRef<PerceptionCatalog | null>(null), draftRef = useRef({ id: "", provider: "" });
  draftRef.current = { id: draft, provider: draftProvider };
  const session = client.config?.session ?? client.status?.connection.session ?? "";
  const read = useCallback(async (signal: AbortSignal) => {
    const epoch = ++request.current;
    const row = await apiFetch<JsonRecord>("/api/v1/rider/perception/models", { signal, cache: "no-store" });
    if (signal.aborted || !mounted.current || epoch !== request.current || applying.current) return;
    const next = perceptionCatalog(data(row));
    const key = perceptionSelectionIdentityKey(next), old = catalogRef.current;
    const oldModel = selectedCatalogModel(old);
    const changedElsewhere = !!selectionRef.current && key !== selectionRef.current;
    const dirtyDraft = !!old && (draftRef.current.provider !== old.provider || draftRef.current.id !== oldModel);
    if (changedElsewhere) {
      invalidatePerceptionPreview?.(); onRefresh();
      if (dirtyDraft) { setConflict(true); setFailure("model_selection_conflict"); }
    }
    if (!draftRef.current.provider || changedElsewhere && !dirtyDraft) {
      setDraftProvider(next.provider);
      setDraft(selectedCatalogModel(next));
    }
    if (next.switching && !old?.switching) invalidatePerceptionPreview?.();
    if (old?.switching && !next.switching) onRefresh();
    setCatalog(next); catalogRef.current = next; selectionRef.current = key; setReadError("");
  }, [invalidatePerceptionPreview, onRefresh,setDraft,setDraftProvider]);
  useEffect(() => {
    if(storedChoice?.session===session)return;
    setCatalog(null); catalogRef.current = null; setDraft(""); setDraftProvider("");
    setReadError(""); setFailure(""); setConflict(false); selectionRef.current = "";
  }, [session,storedChoice?.session,setDraft,setDraftProvider]);
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined, reading = false;
    const tick = async () => {
      if (reading || applying.current || abort.signal.aborted || document.hidden) return;
      reading = true;
      try { await read(abort.signal); }
      catch (error) { if (!abort.signal.aborted) setReadError(perceptionError(error)); }
      finally { reading = false; if (!abort.signal.aborted && !document.hidden) timer = setTimeout(() => void tick(), 1500); }
    };
    const visibility = () => { clearTimeout(timer); if (!document.hidden) void tick(); };
    document.addEventListener("visibilitychange", visibility); void tick();
    return () => { mounted.current = false; abort.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [read, session, refresh]);
  const models = draftProvider === "local" ? catalog?.local_models ?? [] : catalog?.models ?? [];
  const selectedModels = catalog?.provider === "local" ? catalog.local_models : catalog?.models ?? [];
  const selectedId = catalog?.provider === "local" ? catalog.local_engine : catalog?.selection?.model_id;
  const selected = selectedModels.find(model => model.id === selectedId || catalog?.provider === "local" && model.engine === selectedId);
  const candidate = models.find(model => model.id === draft);
  const runtime = selectedModelRuntime();
  const changed = embedded ? !!catalog&&!!draftProvider&&(draftProvider!==catalog.provider||draft!==selected?.id) : perceptionApplyNeeded(catalog, draftProvider, draft, selected?.id);
  const legacyBlock = legacySelectionBlock();
  const modelBlock = candidateModelBlock();
  const block=selectionBlock();
  useEffect(()=>{if(embedded)setWorkspaceState(previous=>previous.modelPending===changed&&previous.modelReady===(!modelBlock&&!conflict&&!readError)?previous:{...previous,modelPending:changed,modelReady:!modelBlock&&!conflict&&!readError});},[embedded,changed,modelBlock,conflict,readError,setWorkspaceState]);
  const run = async (kind: "apply" | "install" | "cancel",resumeObservation=true):Promise<{policy:JsonRecord;token:{session:string;epoch:number;generation:string}}|null> => {
    if (!candidate || applying.current || pending || (kind === "apply" ? !client.canMutate : !currentSession())) return null;
    if (kind === "apply" && (block || modelBlock || !policy || !changed)) return null;
    const origin = currentSession(), id = candidate.id, provider = draftProvider;
    let prepared:Awaited<ReturnType<NonNullable<Props["beforeApply"]>>>|null=null,appliedConfig:JsonRecord|null=null;
    const assertCurrent = () => {
      if (!mounted.current || currentSession() !== origin || document.hidden || prepared&&(client.currentMotionEpoch()!==prepared.token.epoch||client.currentGeneration()!==prepared.token.generation)) throw new Error("session_changed");
    };
    /** Build the result from the operation's latest captured preparation receipt. */
    function modelOperationResult(){
    return appliedConfig?{policy:appliedConfig,token:prepared?.token??{session:origin,epoch:client.currentMotionEpoch(),generation:client.currentGeneration()}}:null;
    }
    const chosen=candidate;
    /** Change only the captured provider and publish its confirmed saved policy. */
    async function configureProvider(current:JsonRecord,pipeline:NonNullable<typeof client.pipeline.status>){
        if (provider === "local") {
          await client.pipeline.change("configure", { ...pipeline.profile, analysis_location: "local", local_engine: chosen.engine as typeof pipeline.profile.local_engine }, pipeline.revision);
          assertCurrent();
          const updated = data(await apiFetch<JsonRecord>("/api/v1/rider/navigation/config", { cache: "no-store" }));
          assertCurrent(); appliedConfig=record(updated.config);onApplied(appliedConfig);
        } else {
          let config = record(current.config);
          if (pipeline.profile.analysis_location !== "venom") {
            await client.pipeline.change("configure", { ...pipeline.profile, analysis_location: "venom" }, pipeline.revision);
            assertCurrent();
            config = record(data(await apiFetch<JsonRecord>("/api/v1/rider/navigation/config", { cache: "no-store" })).config);
          }
          assertCurrent();
          const result = data(await client.mutate("/perception/selection", { revision: config.revision, model_id: id }, "POST", true));
          assertCurrent();
          const saved = record(result.config);
          if (!Number.isSafeInteger(saved.revision)) throw new Error("perception_selection_unconfirmed");
          appliedConfig=saved;onApplied(saved);
        }
    }
    /** Prepare and apply one model under the original session and generation assertions. */
    async function applySelection(){
        if(beforeApply){prepared=await beforeApply();assertCurrent();workspace.claimed.current=true;workspace.setState(previous=>({...previous,busy:true}));}
        const fresh = perceptionCatalog(data(await apiFetch<JsonRecord>("/api/v1/rider/perception/models", { cache: "no-store" })));
        assertCurrent();
        if ((prepared?!selectionMatchesPrepared(catalog,fresh,Number(prepared.policy.revision)):perceptionSelectionKey(fresh)!==perceptionSelectionKey(catalog)) || fresh.switching) throw new Error("model_selection_conflict");
        const current = data(await apiFetch<JsonRecord>("/api/v1/rider/navigation/config", { cache: "no-store" }));
        assertCurrent();
        if (record(current.config).revision !== (prepared?.policy??policy)?.revision) throw new Error("model_selection_conflict");
        const pipeline = client.pipeline.status;
        if (!pipeline || pipeline.revision !== catalog?.pipeline_revision) throw new Error("model_selection_conflict");
        await configureProvider(current,pipeline);
        await client.pipeline.refresh(); assertCurrent(); onRefresh();
        if(prepared?.wasObserving&&resumeObservation){await client.mutate("/goal/s15_venom_perception",{enabled:true},"POST",true);assertCurrent();}
        workspace.setState(previous=>({...previous,modelPending:false}));    }
    /** Report or rethrow a failed model operation with the original caller contract. */
    function reportModelFailure(error:unknown){
      if (mounted.current && currentSession() === origin) {
        setFailure(perceptionError(error));
        if (kind === "apply") { setConflict(perceptionError(error).includes("conflict")); onRefresh(); }
      }
      if(beforeApply&&kind==="apply")throw error;
    }
    /** Clear operation ownership unconditionally while guarding session-specific UI updates. */
    function finishModelOperation(){
      if (kind === "apply") selectionRef.current = "";
      applying.current = false;
      if(beforeApply&&kind==="apply"){workspace.claimed.current=false;workspace.setState(previous=>({...previous,busy:false}));}
      if (mounted.current && currentSession() === origin) { setPending(""); setRefresh(value => value + 1); }
    }
    applying.current = true; request.current++; setPending(kind); setFailure("");
    if (kind === "apply") invalidatePerceptionPreview?.();
    try {
      if (kind !== "apply") {
        await apiFetch<JsonRecord>("/api/v1/rider/perception/models/" + encodeURIComponent(id) + "/install" + (kind === "cancel" ? "/cancel" : ""), { method: "POST", headers: { "X-Rider-Session": origin }, body: "{}" });
      } else {
        await applySelection();
      }
    } catch (error) {
      reportModelFailure(error);
    } finally {
      finishModelOperation();
    }
    return modelOperationResult();
  };
  const runRef=useRef(run);runRef.current=run;
  useEffect(()=>{if(!embedded)return;const handler=(resume=true)=>runRef.current("apply",resume);modelApplyRef.current=handler;return()=>{if(modelApplyRef.current===handler)modelApplyRef.current=null;};},[embedded,modelApplyRef]);
  const job = record(candidate?.job);
  const installAction = draftProvider === "venom" ? perceptionInstallAction(candidate) : null;
  const installing = installAction === "cancel";
  const readyToInstall = installAction === "start";
  const details = record(catalog?.diagnostics.runtime);
  const active = activeModelLabel();
  const clearDraft = () => { setConflict(false); setFailure(""); setDraft(""); setDraftProvider(""); setRefresh(value => value + 1); };
  const Container=embedded?"section":"details";
  return <Container className={styles.perceptionModels} data-testid="perception-models">
    {embedded?<h3>{t("perception.choose")}</h3>:<summary>{t("perception.title")} <span>{catalog ? t("perception.location." + catalog.provider) + " · " + (selected?.label ?? "—") : t("loading")}</span></summary>}
    {renderModelFeedback()}
  </Container>;
}
