import assert from "node:assert/strict";
import { test } from "node:test";
import { freshnessCadence, previewInterval, validTiming } from "../../web-next/lib/loop-timing";
import { nextPreviewDelay } from "../../web-next/lib/preview-media";

test("preview cadence changes without changing expiry or allowing overlapping requests", () => {
  assert.equal(previewInterval(undefined), 500);
  assert.equal(previewInterval({effective:{preview_ms:2000}}), 2000);
  assert.equal(previewInterval({effective:{preview_ms:0}}), 500);
  assert.equal(previewInterval({effective:{preview_ms:Infinity}}), 500);
  assert.equal(nextPreviewDelay(0,200,2000), 1800);
  assert.equal(nextPreviewDelay(0,2500,2000), 0);
});
test("freshness retains original cadence independently of configurable display polling", () => {
  assert.equal(freshnessCadence("control"),500);
  assert.equal(freshnessCadence("calibration"),1000);
  assert.equal(freshnessCadence("overview"),2000);
});
test("timing draft validation rejects empty, extra, nonfinite and out-of-bounds values", () => {
  const fields={preview_ms:{minimum:500,maximum:2000,step:100}};
  assert.equal(validTiming({preview_ms:500},fields),true);
  assert.equal(validTiming({preview_ms:NaN},fields),false);
  assert.equal(validTiming({preview_ms:499},fields),false);
  assert.equal(validTiming({preview_ms:501},fields),false);
  assert.equal(validTiming({preview_ms:500,other:1},fields),false);
  assert.equal(validTiming({},fields),false);
  assert.equal(validTiming({},{}),false);
});
