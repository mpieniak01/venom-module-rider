import assert from "node:assert/strict";
import { test } from "node:test";
import { translate } from "../../web-next/lib/i18n";

test("offline motion report exposes all operator qualifications in each locale",()=>{
  for(const language of ["pl","en","de"] as const)for(const suffix of ["title","run","running","hint","failure","status.available","status.partial","status.unavailable","counts","gaps","sampling","rawNorm","yawAvailable","yawUnqualified","speedUnavailable"]){
    const key="motionEvidence."+suffix;
    assert.notEqual(translate(language,key),key);
  }
});
