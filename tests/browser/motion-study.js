async page=>{
  const check=(value,message)=>{if(!value)throw new Error(message)};
  const origin=new URL(page.url()).origin;
  const config=await(await page.request.get(origin+"/api/v1/rider/config")).json();
  check(config.devices.every(d=>d.mode==="demo"),"fixture-only required");
  const actions=[];
  page.on("request",r=>{if(r.method()!=="GET"&&/\/rider\/(control|goal|resource|safety)/.test(r.url()))actions.push(r.url());});
  const sessions=await(await page.request.get(origin+"/api/v1/rider/samples/sessions")).json();
  const [a,b]=sessions.sessions.filter(s=>s.policy.scene_group==="motion-study-v1:wood");
  await page.getByTestId("study-session-A").selectOption(a.session_id);
  await page.getByTestId("motion-timeline").waitFor();
  await page.getByTestId("study-session-B").selectOption(b.session_id);
  await page.getByTestId("study-quality-B").waitFor();
  check(await page.getByRole("img",{name:"Polecenia i rezultaty HTTP · B",exact:true}).count()===1,"B command trace identity missing");
  const fields=page.getByTestId("calibration-forms").locator('input[type="number"]');
  const number=fields.first(),original=await number.inputValue();
  await number.fill("-1");
  check(await page.getByTestId("calibration-save").isDisabled(),"invalid draft enabled save");
  await number.fill(original);
  const time=page.getByTestId("study-time");
  const initial=await time.inputValue();
  await page.getByTestId("study-next").click();
  check(await time.inputValue()!==initial,"cursor did not advance");
  check(await page.locator("svg circle").count()>12,"trace observations missing");
  const results=[];
  for(const width of [1440,1024,768,390]){
    await page.setViewportSize({width,height:1000});
    for(const language of ["pl","en","de"]){
      await page.evaluate(value=>window.studyFixture.language(value),language);
      const forms=await page.getByTestId("calibration-forms").boundingBox();
      const operator=await page.getByTestId("calibration-operator").boundingBox();
      const small=await fields.first().boundingBox();
      check(small.width<=160,"numeric input too wide");
      if(width>=1024)check(operator.x>forms.x&&operator.y<=forms.y+2,"manual trial not at upper right");
      check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),"page horizontal overflow");
      const invalid=await page.locator("svg polyline").evaluateAll(nodes=>nodes.some(n=>/NaN|Infinity/.test(n.getAttribute("points")??"")));
      check(!invalid,"invalid plot geometry");
      results.push({width,language});
    }
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.evaluate(()=>{window.studyFixture.language("pl");document.body.style.zoom="2";});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),"200% zoom overflow");
  await page.getByTestId("study-time").focus();
  const beforeKey=await time.inputValue();
  await time.press("ArrowRight");
  check(await time.inputValue()!==beforeKey,"keyboard cursor inactive");
  await page.evaluate(()=>document.body.style.zoom="1");
  await page.getByTestId("study-record").click();
  await page.getByTestId("study-end").waitFor({state:"visible"});
  check(await page.getByTestId("study-record").isDisabled(),"recording did not start");
  await page.getByTestId("study-end").click();
  await page.getByTestId("study-record").waitFor({state:"visible"});
  check(actions.length===0,"actuator request during archive/calibration");
  return {layouts:results.length,archiveAB:true,cursor:true,recordWithoutArming:true,actuatorRequests:actions.length};
}
