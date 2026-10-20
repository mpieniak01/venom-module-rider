async page => {
  const check=(value,message)=>{if(!value)throw new Error(message)};
  const actuatorRequests=[];
  page.on("request",request=>{
    if(["POST","PUT","PATCH","DELETE"].includes(request.method()) && /\/rider\/(control|goal|resource)/.test(request.url())) actuatorRequests.push(request.url());
  });
  const panel=page.getByTestId("loop-timing-panel");
  await panel.waitFor();
  for(const width of [1440,768,390]){
    await page.setViewportSize({width,height:1000});
    for(const language of ["pl","en","de"]){
      await page.evaluate(value=>window.timingFixture.language(value),language);
      const field=page.getByTestId("timing-control_ms");
      await field.fill((await field.inputValue())==="1000"?"900":"1000");
      check(await page.getByTestId("timing-save").isEnabled(),"valid profile blocked");
      await page.getByTestId("timing-save").click();
      await page.getByTestId("timing-save").waitFor({state:"visible"});
      check(await page.getByTestId("timing-save").isDisabled(),"saved draft retained");
      await field.fill("499");
      check(await page.getByTestId("timing-save").isDisabled(),"invalid profile enabled");
      await field.fill("600");
      await page.evaluate(()=>window.timingFixture.conflict());
      check(await page.getByTestId("timing-save").isDisabled(),"revision conflict ignored");
      await panel.locator("button").first().click();
      const bad=await panel.locator("input").evaluateAll(nodes=>nodes.some(node=>{
        const box=node.getBoundingClientRect();return box.width<70||box.right>window.innerWidth||box.height<35;
      }));
      check(!bad,"number fields clipped or too small");
    }
    const details=panel.locator("details");
    await details.locator("summary").click();
    check(await details.locator("tbody tr").count()===1,"diagnostic row missing");
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),"page overflows");
    await details.locator("summary").click();
  }
  const writes=await page.evaluate(()=>window.timingFixture.writes);
  check(writes.length===9,"unexpected write count");
  check(writes.every(row=>row.path==="/loop-timing"&&row.method==="PUT"),"unexpected mutation");
  check(actuatorRequests.length===0,"actuator HTTP request during calibration");
  return {viewports:3,languages:3,writes:writes.length,actuatorRequests:actuatorRequests.length};
}
