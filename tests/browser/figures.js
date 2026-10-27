// Dedicated inert fixture; no Pi URLs, credentials or hardware transport.
async page=>{
 const base=new URL(page.url()).origin;
 await page.goto(base+"/fixtures/figures.html");
 const check=(value,message)=>{if(!value)throw Error(message);};
 await page.getByTestId("figure-select").waitFor();
 await page.waitForFunction(()=>document.querySelectorAll('[data-testid="figure-select"] option').length===6);
 await page.getByTestId("figure-select").selectOption("vertical");
 check((await page.evaluate(()=>window.figureFixture.calls)).every(c=>c.kind.startsWith("GET")),"selection actuated");
 check((await page.getByTestId("figure-plan").innerText()).includes("90 SDK"),"posture plan missing");
 const before=await page.getByTestId("figure-start").boundingBox();
 await page.getByTestId("figure-start").click();
 await page.waitForFunction(()=>window.figureFixture.calls.some(c=>c.kind==="start"));
 check(await page.getByTestId("figure-start").isDisabled(),"duplicate Start enabled");
 await page.waitForFunction(()=>window.figureFixture.run.phase==="completed");
 await page.getByTestId("figure-start").waitFor({state:"visible"});
 await page.waitForTimeout(250);
 check((await page.evaluate(()=>window.figureFixture.calls)).filter(c=>c.kind==="start").length===1,"replayed start");
 const after=await page.getByTestId("figure-start").boundingBox();
 check(Math.abs(before.y-after.y)<=1,"Start jumped after status changed");
 check(await page.getByTestId("figure-export").getAttribute("href"),"export missing");
 await page.evaluate(()=>{window.figureFixture.complete=false;});
 await page.getByTestId("figure-start").click();
 await page.waitForFunction(()=>window.figureFixture.run.phase==="running");
 await page.getByTestId("figure-cancel").click();
 await page.waitForFunction(()=>window.figureFixture.run.phase==="cancelled");
 await page.waitForTimeout(350);
 check((await page.getByTestId("figure-status").innerText()).includes("Przerwano"),"cancel status missing");
 check((await page.evaluate(()=>window.figureFixture.calls)).filter(c=>c.kind==="start").length===2,"cancel replayed movement");
 return {fixtureOnly:true,scenarios:["inert selection","one explicit Start","duplicate disabled","stable controls","scoped cancel","export"]};
}
