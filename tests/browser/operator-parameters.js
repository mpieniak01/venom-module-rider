// Inert fixture: there is no robot client, HTTP API or actuator route.
async page=>{
 const check=(v,m)=>{if(!v)throw new Error(m);};
 await page.getByTestId("navigation-profiles").waitFor();
 await page.locator('[data-testid="navigation-profiles"] details summary').click();
 const field=page.getByTestId("behavior-param-obstacle_support_frames"),apply=page.getByTestId("behavior-apply");
 check(await field.inputValue()==="2","server Fluid default not shown");
 check(await page.evaluate(()=>window.parameterFixtureCalls.length)===0,"opening mutated policy");
 await field.fill("3");check((await page.getByTestId("behavior-custom-state").textContent()).includes("Własny"),"custom state missing");
 await apply.click();await page.getByTestId("behavior-saved").waitFor();
 check(await page.evaluate(()=>window.parameterFixtureCalls.length)===1,"save count");
 check((await page.getByTestId("behavior-summary").textContent()).includes("Własny"),"saved custom label missing");
 await page.getByTestId("behavior-param-object_growth_min").fill("1.3");
 await page.getByTestId("external-change").click();check(await apply.isDisabled(),"stale draft writable");
 await page.getByTestId("behavior-refresh").click();
 await page.getByTestId("behavior-reset-preset").click();await apply.click();await page.getByTestId("behavior-saved").waitFor();
 check((await page.getByTestId("behavior-summary").textContent()).includes("Własny")===false,"reset did not restore named preset");
 await field.fill("5");check(await apply.isDisabled(),"invalid support/window relation accepted");
 await page.getByTestId("behavior-refresh").click();
 await page.getByTestId("fixture-offline").click();await field.fill("3");check(await apply.isDisabled(),"offline save permitted");
 await page.getByTestId("fixture-language").selectOption("de");check((await page.getByTestId("navigation-profiles").textContent()).includes("Pixelgruppen"),"German parameter labels missing");
 await page.setViewportSize({width:390,height:844});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"mobile overflow");
 return {result:"PASS",fixtureOnly:true,mutations:await page.evaluate(()=>window.parameterFixtureCalls),robotContacted:false};
}
