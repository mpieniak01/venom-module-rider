async page=>{
 await page.reload();const check=(v,m)=>{if(!v)throw new Error(m);};
 await page.getByTestId("model-tuning").locator("summary").first().click();
 const preset=page.getByTestId("input-region-preset"),apply=page.getByTestId("tuning-apply");
 check(await preset.inputValue()==="full","legacy policy migrated on read");
 check(await apply.isDisabled(),"unchanged config can be saved");
 await preset.selectOption("recommended");check(await apply.isEnabled(),"operator cannot apply recommended crop");
 await page.getByTestId("input-region-top").fill("70");check(await apply.isDisabled(),"invalid crop can be saved");
 await preset.selectOption("recommended");await apply.click();
 check(await page.evaluate(()=>window.inputRegionWrites.length)===1,"no single explicit write");
 const write=await page.evaluate(()=>window.inputRegionWrites[0]);
 check(write.revision===9&&JSON.stringify(write.values.perception.input_region.bounds)==="[0,0.4,1,1]","wrong region or revision");
 await preset.selectOption("full");await page.getByTestId("external-revision").click();check(await apply.isDisabled(),"stale draft can overwrite external config");
}
