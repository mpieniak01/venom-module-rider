import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { translate } from "../../web-next/lib/i18n";

test("conversation option does not arm, start hardware or create a second poller", () => {
  const source = fs.readFileSync(new URL("../../web-next/components/conversation.tsx", import.meta.url), "utf8");
  assert.equal(/\b(?:fetch|useEffect|setInterval)\s*\(/.test(source), false);
  assert.equal(/client\.(?:command|hold)|\/api\/control|operator_consent/.test(source), false);
  assert.equal(source.includes('client.mutate("/conversation/config", { enabled: !enabled, revision: config.revision })'), true);
  assert.equal(source.includes("!known || !client.canMutate"), true);
  assert.equal(source.includes("result.stale === true"), true);
  assert.equal(source.includes(".catch(() => undefined)"), true);
  assert.equal(source.includes("config.credential_transport_allowed === false"), true);
  const client = fs.readFileSync(new URL("../../web-next/lib/use-rider.ts", import.meta.url), "utf8");
  assert.equal(client.includes('path === "/conversation/config" ? "conversationConfigured" : "saved"'), true);
});

test("conversation guidance and compute capability are translated in all host languages", () => {
  for (const language of ["pl", "en", "de"] as const) {
    for (const key of ["conversationTitle", "conversationHint", "conversationEnable", "conversationDisable", "conversationExample", "conversationCredentialMissing", "conversationTransportInsecure", "conversationConfigured", "conversationTransportUnverified", "compute.task.conversation.respond"]) {
      assert.notEqual(translate(language, key), key);
    }
  }
});
