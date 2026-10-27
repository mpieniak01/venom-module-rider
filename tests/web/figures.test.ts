import assert from "node:assert/strict";
import {test} from "node:test";
import {figureActive,figureCatalog} from "../../web-next/lib/figures";
const row={id:"sway",version:1,implementation:"own_sequence",qualification:"manual_trial",physical_completion:"unverified",duration_ms:4000,available:true,maximum_speed:.25,intensity_factor:.3};
const wrap=(value:unknown)=>({version:1,implementation:"own_sequence",figures:[value]});
test("only known own recipes can be selected; native or unverified metadata is not promoted",()=>{
 assert.equal(figureCatalog(wrap(row)).length,1);
 for(const patch of [{id:"action255"},{version:2},{implementation:"firmware"},{duration_ms:NaN},{duration_ms:10001},{maximum_speed:Infinity},{intensity_factor:0},{qualification:"verified"}]){
  assert.deepEqual(figureCatalog(wrap({...row,...patch})),[]);
 }
 assert.deepEqual(figureCatalog({}),[]);
});
test("only preparing and running states request ongoing contact",()=>{
 assert.equal(figureActive(null),false);
 for(const phase of ["preparing","running"])assert.equal(figureActive({phase}),true);
 for(const phase of ["completed","cancelled","failed","unknown"])assert.equal(figureActive({phase}),false);
});
