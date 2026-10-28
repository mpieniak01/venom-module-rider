import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { controlIdentity, type RiderStatus } from "../../web-next/lib/model";
import { controlChanged, retainControl } from "../../web-next/lib/navigation";
import { retainSystem } from "../../web-next/lib/system-history";
import { translate } from "../../web-next/lib/i18n";

const source = (path: string) => fs.readFileSync(new URL("../../" + path, import.meta.url), "utf8");
const status = (generation: number): RiderStatus => ({
  connection: {configured: true, device_id: "prod", session: "same-panel-session", mode: "device", status: "online", transport: {profile: "trusted_lan", state: "online", generation, identity_verified: true, retry_in_ms: 0}},
  control: {safety: {state: "ARMED", generation: "same-pi-generation"}, server_time: 12},
  system: {server_time: 12, hist_ts: [1, 2], hist_cpu: [3, 4]}, settings: {revision: 1},
  polling: {}, telemetry: null, health: null, resources: {}, catalog: null, logic: null, history: null, face: {library: null, runtime: null}, errors: {},
});

test("a new LAN generation discards old permission and pending gesture identity while retaining system history", () => {
  const previous = status(1);
  const next = {...status(2), control: null, system: null, errors: {control: "pi_connect_failed", system: "pi_connect_failed"}};
  assert.notEqual(controlIdentity(previous), controlIdentity(next));
  assert.equal(controlChanged(previous, next), true);
  assert.equal(retainControl(previous, next).control, null);
  const retained = retainSystem(previous, next);
  assert.deepEqual(retained.system?.hist_cpu, previous.system?.hist_cpu);
  assert.notEqual(retained.system?.read_error, null);
  assert.equal(next.connection.device_id, previous.connection.device_id);
  assert.equal(next.connection.session, previous.connection.session);
});

test("all browser request IDs consume the public CSPRNG host helper on HTTP LAN", () => {
  for (const path of ["web-next/lib/use-rider.ts", "web-next/lib/use-samples.ts", "web-next/lib/use-camera-pipeline.ts", "web-next/components/analysis-panel.tsx", "web-next/components/automatic-capture.tsx", "web-next/components/navigation-loop.tsx"]) {
    const text = source(path);
    assert.match(text, /import \{ createBrowserUuid \} from "@\/lib\/optional-modules\/transport"/);
    assert.equal(text.includes("crypto.randomUUID"), false);
    assert.equal(text.includes("Math.random"), false);
    if (text.includes('"use client";')) assert.ok(text.startsWith('"use client";'));
  }
  assert.ok(source("web-next/lib/use-rider.ts").includes("status.connection.transport?.generation"));
});

test("LAN diagnostics and identity readiness have PL EN and DE guidance", () => {
  for (const language of ["pl", "en", "de"] as const) {
    for (const key of ["transport.trustedLan", "transport.state.unverified", "transport.state.resolving", "transport.state.connecting", "transport.state.verifying_identity", "transport.state.online", "transport.state.backoff", "transport.state.identity_mismatch", "transport.error.pi_name_unresolved", "transport.error.pi_connect_failed", "transport.error.pi_connection_timeout", "transport.error.pi_identity_mismatch", "transport.error.pi_identity_invalid", "transport.error.pi_identity_unavailable", "transport.error.pi_redirect_refused", "transport.error.stale_transport_generation", "conversationTransportUnverified"]) assert.notEqual(translate(language, key), key);
  }
  assert.ok(source("web-next/components/conversation.tsx").includes('config.transport_profile !== "trusted_lan"'));
  assert.ok(source("web-next/components/conversation.tsx").includes("config.transport_identity_verified !== true"));
});


test("control identity preserves valid scalar segments without coercing object metadata", () => {
  const base = status(0);
  const identified = (generation: unknown, instance: unknown): RiderStatus => ({
    ...base, control: { safety: { generation, instance_id: instance } },
  });
  assert.equal(controlIdentity(identified("pi-generation", 0)), "same-panel-session:pi-generation:0:0");
  assert.equal(controlIdentity(identified("pi-generation", false)), "same-panel-session:pi-generation:false:0");
  assert.equal(controlIdentity({ ...base, control: { safety: { generation: "pi-generation", instance_id: null, instance: "legacy" } } }), "same-panel-session:pi-generation:legacy:0");
  assert.equal(controlIdentity(null), ":::0");
  const poisonous = { toString() { throw new Error("Identity must not stringify objects"); } };
  for (const malformed of [[], ["pi-generation"], {}, poisonous, undefined, null]) {
    assert.equal(controlIdentity(identified(malformed, malformed)), "same-panel-session:::0");
  }
});
