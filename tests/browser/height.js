// The fixture owns all API responses; it has no robot transport.
async page=>{
 const check=(value,message)=>{if(!value)throw new Error(message);};
 const base=page.url()==="about:blank"?"http://127.0.0.1":page.url();
 await page.goto(new URL("/fixtures/height.html",base).href);
 const slider=page.getByTestId("operator-height"),apply=page.getByTestId("operator-height-apply");
 await slider.waitFor();await page.waitForFunction(()=>!document.querySelector('[data-testid="operator-height"]').disabled);
 check(await page.getByTestId("operator-height-value").innerText()==="—","unknown height invented a measurement");
 check(await apply.isDisabled(),"unknown target allowed Apply");
 check((await page.getByTestId("operator-posture").innerText()).includes("75–115"),"height range missing");
 await slider.focus();await slider.press("Home");for(let i=0;i<20;i++)await slider.press("ArrowRight");
 check(await slider.inputValue()==="95","SDK target mismatch");
 check(await page.evaluate(()=>window.heightFixture.calls.length)===0,"draft edit sent actuator command");
 await apply.click();await page.waitForFunction(()=>window.heightFixture.target===95);
 await page.waitForFunction(()=>!document.querySelector('[data-testid="operator-height"]').disabled);
 check(await page.evaluate(()=>window.heightFixture.calls.map(row=>row.action).join(","))==="height","height required separate ARM/STOP");
 await page.getByTestId("height-after-figure").click();
 await page.waitForFunction(()=>window.heightFixture.phase==="DISARMED");
 await slider.focus();await slider.press("End");
 await page.waitForFunction(()=>!document.querySelector('[data-testid="operator-height-apply"]').disabled);
 await apply.click();await page.waitForFunction(()=>window.heightFixture.target===115);
 check(await page.evaluate(()=>window.heightFixture.calls.length)===2,"duplicate Apply");
 await page.getByTestId("height-estop").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="operator-height-apply"]').disabled);
 await page.getByTestId("height-old-pi").click();
 await page.waitForFunction(()=>!window.heightFixture.supported);
 check(await apply.isDisabled(),"old Pi implicitly granted authority");
 for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
  await page.setViewportSize(viewport);
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"height layout overflow");
 }
 return {result:"PASS",fixtureOnly:true,cases:["disarmed Apply","no separate ARM","after figure","E-Stop","old Pi","inert edits","75–115","desktop/mobile"]};
}
