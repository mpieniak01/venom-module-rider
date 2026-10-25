async page=>{
 await page.reload();
 const check=(v,m)=>{if(!v)throw new Error(m);};
 const preset=page.getByTestId("input-region-preset");await preset.waitFor();
 check(await preset.inputValue()==="recommended","recommended crop missing");
 check(await page.getByTestId("input-region-top").inputValue()==="40","default percent not visible");
 check(await page.getByTestId("apply-crop").isEnabled(),"recommended crop invalid");
 const line=page.getByTestId("model-geometry-overlay").locator("rect").first();
 check(await line.getAttribute("stroke-width")==="1.5","common stroke width lost");
 check(await line.getAttribute("vector-effect")==="non-scaling-stroke","line width scales with source");
 await preset.selectOption("custom");check(await preset.inputValue()==="custom","custom selection jumps back to a preset");
 check(await page.getByTestId("input-region-top").inputValue()==="40","choosing custom changed the bounds");
 await preset.selectOption("full");check(await line.getAttribute("y")==="0","full frame not shown");
 await page.getByTestId("input-region-top").fill("70");check(await page.getByTestId("apply-crop").isDisabled(),"crop can omit floor reference");
 await preset.selectOption("recommended");check(await page.getByTestId("apply-crop").isEnabled(),"preset recovery failed");
}
