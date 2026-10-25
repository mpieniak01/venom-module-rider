// Full page against the isolated Demo ASGI server; never point this at a robot UI.
async page=>{
 const check=(v,m)=>{if(!v)throw new Error(m);};
 const origin=new URL(page.url()).origin;
 check(new URL(origin).port==="18798","Isolated fixture port required");
 const fixture=await(await page.request.get(origin+"/fixture")).json();
 check(fixture.config.devices.every(d=>d.mode==="demo"),"Demo fixture required");
 check(fixture.network_profile==="staggered","Explicit staggered network fixture required; zero-latency/drop path is tested separately");
 if(fixture.config.device_id!==fixture.config.devices[0].id)await page.getByTestId("rider-environment").selectOption(fixture.config.devices[0].id);
 await page.getByTestId("rider-stop").click();
 const requests=[];
 page.on("request",r=>{if(r.method()!=="GET"&&r.url().includes("/api/v1/rider"))requests.push({path:new URL(r.url()).pathname,body:r.postDataJSON()});});
 const bar=page.getByTestId("study-recording-bar"),state=page.getByTestId("study-recording-state");
 await page.getByRole("tab").nth(1).click();
 await page.getByTestId("study-record").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-record"]')?.disabled===true&&document.querySelector('[data-testid="study-end"]')?.disabled===false);
 const label=await state.innerText();
 const sessions=await(await page.request.get(origin+"/api/v1/rider/samples/sessions")).json();
 const active=sessions.sessions.filter(s=>s.state==="collecting");
 check(active.length===1,"Exactly one active recording required");
 const sid=active[0].session_id;
 check(label.includes(sid.slice(0,8)),"Header session identity missing");
 for(const index of [2,3,4,5,6,0]){
   await page.getByRole("tab").nth(index).click();
   check(await state.innerText()===label,"Tab replaced recording identity");
   check(await page.getByTestId("study-end").isEnabled(),"End unavailable across tabs");
   check(requests.filter(r=>r.path.endsWith("/end")).length===0,"Tab change ended recording");
 }
 await page.getByTestId("workspace-automatic").click();
 check(await state.innerText()===label,"Automatic view replaced recording");
 await page.getByTestId("workspace-journey").click();
 check(await state.innerText()===label,"Journey view replaced recording");
 await page.getByTestId("workspace-manual").click();
 await page.getByTestId("rider-arm").click();
 await page.getByTestId("rider-forward").focus();
 await page.getByTestId("rider-forward").press("Enter");
 await page.waitForFunction(()=>document.querySelector('[data-testid="rider-arm"]')?.getAttribute("aria-pressed")==="true");
 await page.getByRole("tab").nth(0).click();
 await page.getByTestId("rider-pad-stop").click();
 check(await page.getByTestId("study-end").isEnabled(),"Motion stop ended recording");
 let observed=false;
 for(let attempt=0;attempt<40;attempt++){
   const row=await(await page.request.get(origin+"/api/v1/rider/samples/sessions/"+sid+"/motion-timeline")).json();
   if(row.counts.imu_records>0){observed=true;break;}
   await new Promise(resolve=>setTimeout(resolve,250));
 }
 check(observed,"No telemetry arrived before end");
 const headerHeight=(await page.locator("header").boundingBox()).height;
 let failEnd=true;
 await page.route("**/api/v1/rider/samples/sessions/"+sid+"/end",async route=>{
   if(failEnd){failEnd=false;return route.fulfill({status:503,contentType:"application/json",body:JSON.stringify({detail:{code:"fixture_end_failure"}})});}
   return route.continue();
 });
 await page.getByTestId("study-end").click();
 await bar.getByRole("alert").waitFor();
 check(Math.abs((await page.locator("header").boundingBox()).height-headerHeight)<=1,"Recording error resized driving header");
 check(await page.getByTestId("study-end").isEnabled(),"Failed end lost retry");
 await page.getByRole("tab").nth(3).click();
 check((await state.innerText()).includes(sid.slice(0,8)),"Failed end lost recording after tab change");
 await page.getByTestId("study-end").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-record"]')?.disabled===false&&document.querySelector('[data-testid="study-end"]')?.disabled===true);
 const timeline=await(await page.request.get(origin+"/api/v1/rider/samples/sessions/"+sid+"/motion-timeline")).json();
 check(timeline.counts.imu_records>0,"No telemetry across tabs "+JSON.stringify(timeline.counts));
 check(timeline.streams.some(s=>s.events.some(e=>e.intent.action==="move")),"Manual command missing from archive");
 const layouts=[];
 for(const language of ["pl","en","de"]){
  await page.getByRole("combobox",{name:"Fixture language"}).selectOption(language);
  for(const size of [{width:1440,height:1000},{width:390,height:844},{width:844,height:390}]){
   await page.setViewportSize(size);
   const boxes=await page.evaluate(()=>{
     const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return{x:r.x,y:r.y,right:r.right,bottom:r.bottom};};
     return {header:rect('header'),bar:rect('[data-testid="study-recording-bar"]'),tabs:rect('[role="tablist"]'),overflow:document.documentElement.scrollWidth>innerWidth};
   });
   check(!boxes.overflow,"Horizontal overflow "+language+JSON.stringify(size));
   check(boxes.bar.x>=0&&boxes.bar.right<=size.width+1&&boxes.bar.bottom<=size.height,"Recording controls outside viewport "+language+JSON.stringify({size,boxes}));
   check(boxes.bar.bottom<=boxes.tabs.y+1,"Controls are not above tabs");
   layouts.push({language,...size});
  }
 }
 await page.getByRole("combobox",{name:"Fixture language"}).selectOption("pl");
 await page.setViewportSize({width:390,height:844});
 await page.getByRole("tab").nth(2).click();
 await page.getByTestId("study-record").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-end"]')?.disabled===false);
 await page.evaluate(()=>{Object.defineProperty(document,"hidden",{configurable:true,get:()=>true});document.dispatchEvent(new Event("visibilitychange"));});
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-end"]')?.disabled===true);
 await page.evaluate(()=>{Object.defineProperty(document,"hidden",{configurable:true,get:()=>false});document.dispatchEvent(new Event("visibilitychange"));});
 await page.getByTestId("study-record").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-end"]')?.disabled===false);
 await page.getByTestId("rider-environment").selectOption("");
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-end"]')?.disabled===true);
 const finalSessions=await(await page.request.get(origin+"/api/v1/rider/samples/sessions")).json();
 check(!finalSessions.sessions.some(s=>s.state==="collecting"),"Device switch left recording active");
 check(requests.filter(r=>r.path.endsWith("/samples/sessions")).length===3,"Tab switching created extra recordings");
 return {sameSessionAcrossTabs:true,manualRecorded:true,pausePreservesRecording:true,endFailureRetry:true,hiddenEnds:true,deviceChangeEnds:true,layouts:layouts.length,networkProfile:fixture.network_profile,fixtureOnly:true};
}
