async page=>{
 await page.reload();
 const check=(ok,message)=>{if(!ok)throw new Error(message);};
 const enabled=page.getByTestId("collision-enabled");await enabled.waitFor();
 check(!(await enabled.isChecked()),"new mode must be explicit");
 await enabled.check();
 check(await page.getByTestId("save-collision").isEnabled(),"valid draft rejected");
 await page.getByTestId("collision-far_width_ratio").fill("60");
 const draft=JSON.parse(await page.getByTestId("geometry-draft").textContent());
 check(draft.collision.far_width_ratio===.6,"percentage not persisted in draft");
 check(Math.abs(draft.collision.reference_corridor[1]-.65)<1e-9,"taper changed floor reference");
 await page.getByTestId("collision-end_fraction").fill("0");
 check(await page.getByTestId("save-collision").isDisabled(),"empty endpoint footprint accepted");
 await page.getByTestId("collision-end_fraction").fill("20");
 check(await page.getByTestId("model-geometry-overlay").locator("path").count()===3,"actual paths/guard missing");
 await page.getByTestId("capability").click();
 check(await enabled.isEnabled(),"operator cannot disable unsupported geometry");
 check(await page.getByTestId("save-collision").isDisabled(),"unsupported peer can save new mode");
 await enabled.uncheck();check(await enabled.isDisabled(),"old peer can opt in again");
 check(await page.getByTestId("save-collision").isEnabled(),"legacy recovery unavailable");
}
