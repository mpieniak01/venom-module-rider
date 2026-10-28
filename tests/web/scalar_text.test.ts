import assert from "node:assert/strict";
import { test } from "node:test";
import { scalarText } from "../../web-next/lib/model";

test("scalar text preserves protocol numbers, booleans and literal strings", () => {
  for (const [value, expected] of [[0, "0"], [-12.5, "-12.5"], [true, "true"], [false, "false"], ["", ""], ["camera-17", "camera-17"]] as const) {
    assert.equal(scalarText(value, "missing"), expected);
  }
  assert.equal([3, 0, "camera-17"].map(value => scalarText(value)).join(":"), "3:0:camera-17");
});

test("scalar text rejects non-scalars without invoking conversion hooks", () => {
  const poisonous = { toString() { throw new Error("Must not stringify objects"); } };
  for (const value of [undefined, null, [], ["go"], {}, { toString: "bad" }, poisonous, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(scalarText(value), "");
    assert.equal(scalarText(value, "missing"), "missing");
  }
});
