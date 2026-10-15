import { test } from "node:test";
import assert from "node:assert/strict";
import { imagePoint, dragCorridor } from "../../web-next/lib/corridor-editor";

test("letterboxing maps only image content",()=>{
  assert.equal(imagePoint(10,50,200,100,100,100),null);
  assert.deepEqual(imagePoint(50,0,200,100,100,100),[0,0]);
  assert.deepEqual(imagePoint(100,50,200,100,100,100),[.5,.5]);
});
test("moving preserves width and stops at image boundary",()=>{
  const value=dragCorridor([.35,.8,.65,.95],.9,.9,"move");
  assert.equal(value[2],1);assert.equal(value[3],1);
  assert.ok(Math.abs(value[2]-value[0]-.3)<1e-9);
});
test("resize cannot invert or collapse region",()=>{
  const value=dragCorridor([.35,.8,.65,.95],-.9,-.9,"end");
  assert.ok(value[2]-value[0]>=.079999);assert.ok(value[3]-value[1]>=.079999);
});
