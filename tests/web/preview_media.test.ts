import assert from "node:assert/strict";
import { test } from "node:test";
import { displayedPreview, decodePreview, nextPreviewDelay, previewLifetime, previewRequestCurrent, retainedPreview } from "../../web-next/lib/preview-media";

test("camera cadence is start-to-start while a slow read never overlaps another request", () => {
  assert.equal(nextPreviewDelay(1000, 1000), 500);
  assert.equal(nextPreviewDelay(1000, 1120), 380);
  assert.equal(nextPreviewDelay(1000, 1500), 0);
  assert.equal(nextPreviewDelay(1000, 1873), 0);
});

test("decode does not reset age or extend the existing 1000ms display limit", () => {
  const frame = { ageMs: 120, receivedAt: 1000 };
  assert.equal(previewLifetime(frame, 1000), 880);
  assert.equal(previewLifetime(frame, 1500), 380);
  assert.equal(previewLifetime(frame, 1880), 0);
  assert.equal(previewLifetime(frame, 2200), 0);
  assert.equal(previewLifetime({ageMs: NaN, receivedAt:1000}, 1000), null);
});

test("new preview becomes displayable only after the candidate has decoded", async () => {
  let finish: () => void = () => {};
  const decoder = { src: "", decode: () => new Promise<void>(resolve => { finish=resolve; }) };
  const abort = new AbortController();
  let ready = false;
  const task = decodePreview("blob:new-frame", abort.signal, () => decoder).then(() => { ready=true; });
  await Promise.resolve();
  assert.equal(ready, false);
  assert.equal(decoder.src, "blob:new-frame");
  finish();
  await task;
  assert.equal(ready, true);
});

test("cancelled candidate cannot become displayable after a late decode completes", async () => {
  let finish: () => void = () => {};
  const decoder = { src: "", decode: () => new Promise<void>(resolve => { finish=resolve; }) };
  const abort = new AbortController();
  let ready=false;
  const task = decodePreview("blob:old-stream", abort.signal, () => decoder).then(() => { ready=true; });
  abort.abort();
  await assert.rejects(task, { name:"AbortError" });
  finish();
  await Promise.resolve();
  assert.equal(ready,false);
  assert.equal(decoder.src,"");
});

test("an already cancelled read never starts decoding, and corrupt media rejects", async () => {
  const abort = new AbortController();
  abort.abort();
  await assert.rejects(decodePreview("blob:cancelled",abort.signal,()=>{throw new Error("must not create image");}),{name:"AbortError"});
  await assert.rejects(decodePreview("blob:bad",new AbortController().signal,()=>({
    src:"", decode:async()=>{throw new Error("invalid JPEG");},
  })),/invalid JPEG/);
});


test("handoff pauses only processed preview and rejects pre-handoff frames after resume", () => {
  // The same guard runs before issuing GET and before installing its response.
  assert.equal(previewRequestCurrent("camera","/camera/venom",false,0,0),true);
  assert.equal(previewRequestCurrent("camera","/camera/venom",true,0,1),false);
  assert.equal(previewRequestCurrent("camera","/camera/venom",true,1,1),false);
  assert.equal(previewRequestCurrent("camera","/camera/venom",false,0,2),false);
  assert.equal(previewRequestCurrent("camera","/camera/venom",false,2,2),true);
  for(const [kind,path] of [["camera","/camera/raw"],["camera","/camera/proc"],["face","/face/runtime/frame"]] as const){
    assert.equal(previewRequestCurrent(kind,path,true,0,2),true,path);
  }
});


test("one busy reply keeps the original display frame only until its original expiry", () => {
  const frame = { ageMs: 50, receivedAt: 1000, stale: false, knownFreshness: true, id: "capture-17" };
  assert.equal(retainedPreview(frame, true, 1500), frame);
  assert.equal(previewLifetime(retainedPreview(frame, true, 1500), 1500), 450);
  const expired = retainedPreview(frame, true, 1950);
  assert.equal(expired.stale, true);
  assert.equal(expired.knownFreshness, false);
  assert.equal(expired.receivedAt, 1000);
  assert.equal(expired.ageMs, 50);
  assert.equal(retainedPreview(expired, true, 1951), expired);
});

test("unknown freshness and hard camera failures never retain a live label", () => {
  const frame = { ageMs: 50, receivedAt: 1000, stale: false, knownFreshness: true };
  assert.equal(retainedPreview(frame, false, 1100).stale, true);
  assert.equal(retainedPreview({ ...frame, knownFreshness: false }, true, 1100).stale, true);
  assert.equal(retainedPreview({ ...frame, ageMs: NaN }, true, 1100).stale, true);
});


test("a fresh candidate keeps the displayed image analysis until its load acknowledgement", () => {
  const old = { url:"blob:old", id:"capture-7", ageMs:20, receivedAt:1000, stale:false, knownFreshness:true };
  const next = { ...old, url:"blob:next", id:"capture-8", ageMs:10, receivedAt:1400 };
  assert.equal(displayedPreview(next, old, 1500), old);
  assert.equal(displayedPreview(next, next, 1500), next);
  assert.equal(displayedPreview(next, null, 1500), null);
  assert.equal(displayedPreview(null, old, 1500), null);
});

test("pending candidates cannot renew the displayed image or suppress a hard stale flag", () => {
  const old = { url:"blob:old", id:"capture-7", ageMs:20, receivedAt:1000, stale:false, knownFreshness:true };
  const next = { ...old, url:"blob:next", id:"capture-8", ageMs:10, receivedAt:1900 };
  const expired = displayedPreview(next, old, 1980)!;
  assert.equal(expired.id, old.id);
  assert.equal(expired.stale, true);
  assert.equal(expired.knownFreshness, false);
  assert.equal(expired.receivedAt, old.receivedAt);
  assert.equal(previewLifetime(expired,1980),0);
  assert.equal(displayedPreview({ ...next, stale:true }, old, 1500)!.stale,true);
  const retained = { ...old, stale:true, knownFreshness:false };
  assert.deepEqual(displayedPreview(retained, old, 1500),retained);
});

test("unknown timing remains unconfirmed and a confirmed displayed flag is not borrowed", () => {
  const unknown = { url:"blob:unknown", ageMs:undefined as number | undefined, receivedAt:1000, stale:false, knownFreshness:false };
  const previous = { ...unknown, url:"blob:old", ageMs:20, knownFreshness:true };
  assert.equal(displayedPreview(unknown,unknown,1500),unknown);
  assert.equal(displayedPreview(unknown,unknown,1500)!.knownFreshness,false);
  assert.equal(displayedPreview(unknown,previous,1500),previous);
  assert.equal(displayedPreview({ ...previous,stale:true },previous,1500)!.knownFreshness,false);
});
