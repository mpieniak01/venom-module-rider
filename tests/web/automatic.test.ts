import assert from "node:assert/strict";
import { test } from "node:test";
import { autoIntent, autoStatus, recoveredAutoEnable, defaultEventProfile, eventNames } from "../../web-next/lib/automatic";
import { translate } from "../../web-next/lib/i18n";
test("automatic enable needs explicit existing collection and device epoch",()=>{
  assert.equal(autoIntent(null,"host",defaultEventProfile,"request"),null);
  assert.equal(autoIntent("session",null,defaultEventProfile,"request"),null);
  assert.equal(autoIntent("session","host",{...defaultEventProfile,events:[]},"request"),null);
  const intent=autoIntent("session","host",defaultEventProfile,"request");
  assert.equal(intent?.headers["X-Rider-Session"],"host");
  assert.equal(intent?.body.session_id,"session");
  assert.equal("camera" in intent!.body,false);
});
test("automatic status does not treat queue ACK as saved",()=>{
  assert.equal(autoStatus({state:"off"}).state,"off");
  assert.equal(autoStatus({state:"on",id:"a".repeat(32),saved:0}).saved,0);
  for(const value of [{state:"queued"},{state:"on"},{state:"saved"},null])assert.throws(()=>autoStatus(value));
});
test("all automatic events and controls have module translations",()=>{
  for(const language of ["pl","en","de"] as const)for(const key of ["title","hint","on","off","start","stop","profile","noObjectDetector","collectionRequired",...eventNames.map(name=>"event."+name)]){
    assert.notEqual(translate(language,"auto."+key),"auto."+key);
  }
});

test("only the exact server-confirmed enable recovers a lost response",()=>{
  const status=autoStatus({state:"on",id:"a".repeat(32),request_id:"original"});
  assert.equal(recoveredAutoEnable(status,"original"),true);
  assert.equal(recoveredAutoEnable(status,"different-tab"),false);
  assert.equal(recoveredAutoEnable(status,null),false);
  assert.equal(recoveredAutoEnable(autoStatus({state:"off"}),"original"),false);
});
