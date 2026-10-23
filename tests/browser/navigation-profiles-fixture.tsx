import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { ModelTuning } from "../../web-next/components/model-tuning";
import { NavigationProfiles } from "../../web-next/components/navigation-profiles";
import { NavigationTemporal } from "../../web-next/components/navigation-temporal";
import { temporalView } from "../../web-next/lib/behavior";
import { translate, type RiderLanguage } from "../../web-next/lib/i18n";
import type { JsonRecord } from "../../web-next/lib/model";
import type { RiderClient } from "../../web-next/lib/use-rider";

const calls: JsonRecord[] = [];
let revision = 9, selected = "legacy", model = "segformer-b0-ade20k";
let perception: JsonRecord = { engine: "segformer", model_id: model, artifact_sha256: "a".repeat(64), residual_limit: .06, outlier_limit: .005, slope_min: 2, semantic_models: { "yolo26n-sem-ade20k": { version: 1, surface: "floor_rug", pixel_confidence: .9 } } };
const snapshot = (supported = true): JsonRecord => ({
  revision, mode: "observe", verified: false, model_tuning_supported: supported, perception: { ...perception, model_id: model, engine: model.includes("depth-anything") ? "depth_plane" : "segformer" },
  perception_tuning: { version: 1, active_model_id: model, effective: { pixel_confidence: .8, min_floor_fraction: 1, max_uncertain_fraction: .01, min_region_fraction: 0, surface: (perception.semantic_models as Record<string, JsonRecord>)?.[model]?.surface ?? "floor_only" } },
  behavior: { version: 1, profile_id: selected, profile_revision: 1 },
  behavior_catalog: { version: 1, supported, active: { version: 1, profile_id: selected, profile_revision: 1 },
    effective_policy_sha256: "a".repeat(64),
    profiles: ["legacy", "cautious", "balanced", "fluid"].map(id => ({ id, profile_revision: 1,
      status: id === "legacy" ? "legacy" : "experimental", parameters: { server_fixture_value: 137 } })),
  },
});
declare global { interface Window { profileFixtureCalls: JsonRecord[] } }
window.profileFixtureCalls = calls;
function Fixture() {
  const [policy, setPolicy] = useState(snapshot), [pending, setPending] = useState(false), [goals, setGoals] = useState({});
  const [provider, setProvider] = useState<"venom" | "local">("venom");
  const [supported, setSupported] = useState(true), [language, setLanguage] = useState<RiderLanguage>("pl");
  const t = (key: string, values?: Record<string, string | number>) => translate(language, key, values);
  const client = {
    pipeline: { status: { revision: 4, profile: { analysis_location: provider, local_engine: "canny" } } },
    config: { session: "fixture" }, currentSession: () => "fixture", canMutate: true, controlFresh: true,
    status: { connection: { session: "fixture", status: "online" }, control: { safety: { state: "ARMED", moving: false, operator_control: { state: "paused" } } }, errors: {} },
    mutate: async (path: string, body: JsonRecord) => {
      if (path !== "/navigation/config" || body.revision !== revision) throw { data: { detail: { code: "navigation_configuration_conflict" } } };
      calls.push({ path, body }); await new Promise(resolve => setTimeout(resolve, 80));
      revision++; const values = body.values as JsonRecord;
      if (values.behavior) selected = String((values.behavior as JsonRecord).profile_id);
      if (values.perception) perception = values.perception as JsonRecord;
      const config = snapshot(supported); setPolicy(config); return { config };
    },
  } as unknown as RiderClient;
  return <main style={{maxWidth:900,margin:"24px auto",padding:16,fontFamily:"sans-serif"}}>
    <div style={{position:"relative",minHeight:450}}>
    <NavigationProfiles client={client} policy={policy} goals={goals} locked={false} t={t}
      onApplied={setPolicy} onRefresh={() => setPolicy(snapshot(supported))} onPendingChange={setPending}/>
    </div>
    <div style={{position:"relative",minHeight:120}}><ModelTuning client={client} policy={policy} goals={goals} locked={false} t={t} onApplied={setPolicy} onRefresh={() => setPolicy(snapshot(supported))} onPendingChange={setPending}/></div>
    <NavigationTemporal t={t} view={temporalView({ running: false, stale: true, decision: { raw_state: "UNKNOWN",
      behavior: { profile_id: "balanced", profile_revision: 1 }, temporal: { action: "slow", state: "CLEAR", risk: "warning",
        reason: "brief_uncertainty", near_guard_clear: true, evidence_frames: 3, speed_factor: .5, spatial: {} } } })}/>
    <hr/>
    <label>Fixture language <select data-testid="fixture-language" value={language} onChange={event => setLanguage(event.target.value as RiderLanguage)}><option>pl</option><option>en</option><option>de</option></select></label>
    <button data-testid="fixture-other-panel" onClick={() => { revision++; setPolicy(snapshot(supported)); }}>Fixture external revision</button>
    <button data-testid="fixture-support" onClick={() => {setSupported(!supported);setPolicy(snapshot(!supported));}}>Fixture support</button>
    <label>Fixture provider <select data-testid="fixture-provider" value={provider} onChange={event => setProvider(event.target.value as "venom" | "local")}><option>venom</option><option>local</option></select></label>
    <label>Fixture model <select data-testid="fixture-model" value={model} onChange={event => { model=event.target.value; revision++; setPolicy(snapshot(supported)); }}><option>segformer-b0-ade20k</option><option>yolo26n-sem-ade20k</option><option>depth-anything-v2-indoor-small</option></select></label>
    <button data-testid="fixture-drive" onClick={() => setGoals({ drive: true })}>Fixture drive</button>
    <button data-testid="fixture-pause" onClick={() => setGoals({})}>Fixture pause</button>
    <p data-testid="fixture-pending">{pending ? "pending" : "idle"}</p>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
