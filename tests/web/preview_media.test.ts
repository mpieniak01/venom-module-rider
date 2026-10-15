import assert from "node:assert/strict";
import { test } from "node:test";
import { decodePreview, nextPreviewDelay, previewLifetime, previewRequestCurrent } from "../../web-next/lib/preview-media";

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
