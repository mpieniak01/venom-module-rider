import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { NavigationProfiles } from "../../web-next/components/navigation-profiles";
import { NavigationTemporal } from "../../web-next/components/navigation-temporal";
import { temporalView } from "../../web-next/lib/behavior";
import { translate, type RiderLanguage } from "../../web-next/lib/i18n";
import type { JsonRecord } from "../../web-next/lib/model";
import type { RiderClient } from "../../web-next/lib/use-rider";

const calls: JsonRecord[] = [];
let revision = 9, selected = "legacy";
const snapshot = (supported = true): JsonRecord => ({
  revision, mode: "observe", verified: false,
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
  const [supported, setSupported] = useState(true), [language, setLanguage] = useState<RiderLanguage>("pl");
  const t = (key: string, values?: Record<string, string | number>) => translate(language, key, values);
  const client = {
    config: { session: "fixture" }, currentSession: () => "fixture", canMutate: true, controlFresh: true,
    status: { connection: { session: "fixture", status: "online" }, control: { safety: { state: "ARMED", moving: false, operator_control: { state: "paused" } } }, errors: {} },
    mutate: async (path: string, body: JsonRecord) => {
      if (path !== "/navigation/config" || body.revision !== revision) throw { data: { detail: { code: "navigation_configuration_conflict" } } };
      calls.push({ path, body }); await new Promise(resolve => setTimeout(resolve, 80));
      revision++; selected = String((body.values as { behavior: { profile_id: string } }).behavior.profile_id);
      const config = snapshot(supported); setPolicy(config); return { config };
    },
  } as unknown as RiderClient;
  return <main style={{maxWidth:900,margin:"24px auto",padding:16,fontFamily:"sans-serif"}}>
    <div style={{position:"relative",minHeight:450}}>
    <NavigationProfiles client={client} policy={policy} goals={goals} locked={false} t={t}
      onApplied={setPolicy} onRefresh={() => setPolicy(snapshot(supported))} onPendingChange={setPending}/>
    </div>
    <NavigationTemporal t={t} view={temporalView({ running: false, stale: true, decision: { raw_state: "UNKNOWN",
      behavior: { profile_id: "balanced", profile_revision: 1 }, temporal: { action: "slow", state: "CLEAR", risk: "warning",
        reason: "brief_uncertainty", near_guard_clear: true, evidence_frames: 3, speed_factor: .5, spatial: {} } } })}/>
    <hr/>
    <label>Fixture language <select data-testid="fixture-language" value={language} onChange={event => setLanguage(event.target.value as RiderLanguage)}><option>pl</option><option>en</option><option>de</option></select></label>
    <button data-testid="fixture-other-panel" onClick={() => { revision++; setPolicy(snapshot(supported)); }}>Fixture external revision</button>
    <button data-testid="fixture-support" onClick={() => {setSupported(!supported);setPolicy(snapshot(!supported));}}>Fixture support</button>
    <button data-testid="fixture-drive" onClick={() => setGoals({ drive: true })}>Fixture drive</button>
    <button data-testid="fixture-pause" onClick={() => setGoals({})}>Fixture pause</button>
    <p data-testid="fixture-pending">{pending ? "pending" : "idle"}</p>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
