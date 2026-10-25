// Inert fixture: all writes remain in memory and every API request is blocked.
async page=>{
 const check=(value,message)=>{if(!value)throw new Error(message);};
 await page.route("**/api/**",route=>route.abort());
 await page.goto("http://127.0.0.1:3312/");
 await page.getByTestId("behavior-profile").selectOption("room_fluid");
 await page.getByText("Szczegóły i własne ustawienia",{exact:true}).click();
 const policy=page.getByTestId("behavior-param-object_policy_enabled");
 check(await policy.isChecked(),"room object assessment not enabled");
 check(await page.getByTestId("behavior-param-object_tracking_enabled").isChecked(),"room tracking not enabled");
 check(await page.getByTestId("behavior-param-obstacle_support_frames").inputValue()==="2","room support default missing");
 await page.getByTestId("behavior-param-obstacle_support_frames").fill("3");
 await page.getByTestId("behavior-apply").click();
 let calls=await page.evaluate(()=>window.parameterFixtureCalls);
 check(calls.length===1&&calls[0].body.values.behavior.profile_id==="room_fluid","new preset not saved");
 check(calls[0].body.values.behavior.overrides.obstacle_support_frames===3,"custom support lost");
 check((await page.getByTestId("behavior-summary").textContent()).includes("Własne ustawienia"),"custom label missing");
 await page.getByTestId("behavior-reset-preset").click();
 await page.getByTestId("behavior-apply").click();
 calls=await page.evaluate(()=>window.parameterFixtureCalls);
 check(calls.length===2&&!calls[1].body.values.behavior.overrides,"restore did not remove corrections");
 await page.getByTestId("behavior-param-object_tracking_enabled").uncheck();
 check(await page.getByTestId("behavior-apply").isDisabled(),"assessment without tracking was allowed");
 await page.getByTestId("behavior-reset-preset").click();
 await page.getByTestId("behavior-profile").selectOption("fluid");
 check(await policy.count()===0,"room-only control leaked into fluid");
 for(const lang of ["en","de"]){await page.getByTestId("fixture-language").selectOption(lang);check((await page.getByTestId("behavior-profile").textContent()).includes(lang==="en"?"known room":"bekannter Raum"),"room translation missing");}
 return {result:"PASS",mockWrites:calls.length,robotContacted:false};
}
