import assert from "node:assert/strict";
import {test} from "node:test";
import {collisionDraft,validCollision,collisionNavigationValid,geometryMode} from "../../web-next/lib/collision-geometry";
import {riderAnalysisSettings,analysisProfileSettings} from "../../web-next/lib/analysis-profile";
const roi=[.35,.8,.65,.95],geometry={mode:"image",collision:collisionDraft(roi)};
test("corridor edits leave floor support fixed and invalid geometry cannot be submitted",()=>{
 const collision=collisionDraft(roi);
 assert(validCollision(collision,roi));
 assert(validCollision(collision,[.4,.8,.6,.95]));
 assert.deepEqual(collision.reference_corridor,[.35,.65,.65,.95]);
 for(const invalid of [{version:true},{far_width_ratio:0},{left_shift:NaN},{end_fraction:0},{reference_corridor:[.4,.8,.6,.95]},{extra:1}])assert(!validCollision({...collision,...invalid},roi));
});
test("both peers and explicit avoidance are required for geometry changes",()=>{
 const base={collision_geometry_supported:true},draft={corridor:roi,avoidance:{enabled:true,protocol_version:3,geometry}};
 assert(collisionNavigationValid(base,draft));
 assert(!collisionNavigationValid({},draft));
 assert(!collisionNavigationValid(base,{...draft,perception:{input_region:{bounds:[.4,.7,.6,.95]}}}));
 assert(collisionNavigationValid({}, {...draft,avoidance:{geometry:{mode:"image"}}}));
 assert(!("collision" in geometryMode(geometry,"ground")));
 assert("collision" in geometry);
});
test("recorded analysis inherits exact geometry without motion authority",()=>{
 const source={revision:1,corridor:roi,avoidance:{geometry,sweep_verified:true},verified:true};
 const snapshot=riderAnalysisSettings(source);
 assert.deepEqual(snapshot.collision,geometry.collision);
 assert(!("verified" in snapshot));
 snapshot.collision!.left_shift=.2;
 assert.notEqual(snapshot.collision!.left_shift,geometry.collision.left_shift);
 const semantic=analysisProfileSettings({id:"segformer-b0-ade20k",task:"semantic",installed:true,windows:[1]},1,geometry,{});
 assert(!("collision" in semantic.geometry));
});
