// Dedicated inert component: no Rider API and no hardware transport.
async page=>{
 const base=new URL(page.url()).origin;
 await page.goto(base+"/fixtures/in-place-turn.html");
 const check=(ok,message)=>{if(!ok)throw new Error(message);};
 const left=page.getByTestId("rider-spin-left"),right=page.getByTestId("rider-spin-right");
 await left.waitFor({state:"visible"});
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:844});
  for(const target of [left,right,page.getByTestId("rider-pad-stop")]){
   const box=await target.boundingBox();check(box&&box.width>=44&&box.height>=44,"target below 44px");
  }
 }
 await left.click();
 let calls=await page.evaluate(()=>window.spinCalls);
 check(calls.length===1&&calls[0].turn_mode==="in_place"&&calls[0].mode==="pulse","new arrow did not send in-place pulse");
 await page.getByTestId("rider-left").click();
 calls=await page.evaluate(()=>window.spinCalls);
 check(calls.length===2&&!calls[1].turn_mode,"old turn changed");
 const box=await right.boundingBox();
 await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
 await page.waitForTimeout(220);
 await page.mouse.up();
 await page.waitForTimeout(60);
 calls=await page.evaluate(()=>window.spinCalls);
 check(calls.at(-2).mode==="hold"&&calls.at(-1).mode==="release"&&calls.at(-1).turn_mode==="in_place","release waited for stalled hold response");
 await page.waitForTimeout(650);
 check((await page.evaluate(()=>window.spinCalls)).length===calls.length,"released turn renewed later");
 await page.getByTestId("old-pi").click();
 check(await left.isDisabled()&&await right.isDisabled(),"unsupported Pi accepted new turn");
 check(await page.getByTestId("rider-left").isEnabled(),"compatibility disabled old turn");
 return {pass:true,robot_contacted:false,targets:7,legacy_turn_preserved:true};
}
