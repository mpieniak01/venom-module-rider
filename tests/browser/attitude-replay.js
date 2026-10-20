async page=>{
  const check=(v,m)=>{if(!v)throw new Error(m)};
  const origin=new URL(page.url()).origin;
  const fixture=await(await page.request.get(origin+"/fixture")).json();
  check(fixture.config.devices.every(d=>d.mode==="demo"),"Fixture-only required");
  const writes=[];
  page.on("request",r=>{if(r.method()!=="GET"&&r.url().includes("/api/v1/rider"))writes.push(r.url());});
  await page.getByTestId("study-session-A").selectOption(fixture.sessions.A);
  await page.getByTestId("study-session-B").selectOption(fixture.sessions.B);
  await page.getByTestId("attitude-body-A").waitFor();
  await page.getByTestId("attitude-body-B").waitFor();
  const slider=page.getByTestId("study-time");
  const first=await slider.inputValue();
  await page.getByTestId("attitude-reference").click();
  check(await page.getByTestId("attitude-frame").inputValue()==="relative","relative reference inactive");
  await page.getByTestId("study-next").click();
  check(await slider.inputValue()!==first,"cursor did not advance");
  await page.getByTestId("attitude-body-A").waitFor();
  await page.getByTestId("study-next").click(); // HTTP result, no exact IMU
  check(await page.getByTestId("attitude-body-A").count()===0,"model interpolated across missing IMU");
  await page.getByTestId("study-prev").click();
  await page.getByTestId("attitude-body-A").waitFor();
  await page.getByTestId("attitude-frame").selectOption("source");
  await page.getByTestId("study-play").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="study-play"]')?.getAttribute("aria-pressed")==="true");
  await page.getByTestId("study-play").click();
  check(await page.getByTestId("study-play").getAttribute("aria-pressed")==="false","pause inactive");
  await slider.focus();
  await slider.press("End");
  await page.getByTestId("attitude-body-A").waitFor();
  const layouts=[];
  for(const width of [1440,768,390]){
    await page.setViewportSize({width,height:1000});
    for(const language of ["pl","en","de"]){
      await page.evaluate(v=>window.studyFixture.language(v),language);
      check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"page overflow");
      const invalid=await page.getByTestId("attitude-body-A").locator("polygon").evaluateAll(nodes=>nodes.some(n=>/NaN|Infinity/.test(n.getAttribute("points")??"")));
      check(!invalid,"invalid model coordinates");
      layouts.push({width,language});
    }
  }
  await page.emulateMedia({reducedMotion:"reduce"});
  await page.getByTestId("study-prev").click();
  check(await page.getByTestId("study-play").getAttribute("aria-pressed")==="false","reduced motion auto-started");
  await page.getByTestId("study-session-A").selectOption(fixture.sessions.raw);
  await page.getByTestId("attitude-card-A").waitFor();
  await page.getByTestId("attitude-body-A").waitFor();
  await page.getByTestId("attitude-provisional-A").waitFor();
  await page.getByTestId("attitude-unit-assumption-A").waitFor();
  await page.getByTestId("attitude-preview").uncheck();
  await page.waitForFunction(()=>document.querySelector('[data-testid="attitude-card-A"]')?.textContent?.includes("nicht qualifiziert"));
  check(await page.getByTestId("attitude-body-A").count()===0,"sdk_raw interpreted as degrees");
  check(await page.locator("svg circle").count()>8,"raw plots disappeared");
  check(writes.length===0,"archive replay sent a mutation");
  await page.emulateMedia({reducedMotion:"no-preference"});
  await page.evaluate(()=>window.studyFixture.language("pl"));
  await page.getByTestId("study-session-A").selectOption(fixture.sessions.A);
  await page.getByTestId("attitude-body-A").waitFor();
  return {layouts:layouts.length,qualifiedAB:true,relativeReference:true,exactSampleOnly:true,replayAndPause:true,rawFallback:true,mutations:writes.length};
}
