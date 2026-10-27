import assert from "node:assert/strict";
import {test} from "node:test";
import {errorCode} from "../../web-next/lib/model";
test("local and API navigation cancellation have the same nonfailure code",()=>{
 assert.equal(errorCode(new Error("navigation_trial_cancelled")),"navigation_trial_cancelled");
 assert.equal(errorCode({data:{detail:{code:"navigation_trial_cancelled"}}}),"navigation_trial_cancelled");
 assert.equal(errorCode({data:{error:"navigation_trial_cancelled"}}),"navigation_trial_cancelled");
});
test("local error messages are not generally promoted to API codes",()=>{
 assert.equal(errorCode(new Error("untrusted private details")),"request_failed");
 assert.equal(errorCode({name:"AbortError"}),"cancelled");
});
