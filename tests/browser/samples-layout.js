// Full RiderPage on Demo ASGI only. Never run against a physical robot panel.
async page=>{
 const check=(value,message)=>{if(!value)throw new Error(message);};
 const origin=new URL(page.url()).origin;
 check(new URL(origin).port==="18810","isolated fixture required");
 const fixture=await(await page.request.get(origin+"/fixture")).json();
 check(fixture.config.devices.every(d=>d.mode==="demo"),"Demo-only fixture required");
 const writes=[];
 page.on("request",r=>{if(r.method()!=="GET"&&r.url().includes("/api/v1/rider"))writes.push({path:new URL(r.url()).pathname,body:r.postDataJSON()});});
 const tabs=page.getByRole("tab");
 await page.getByTestId("study-record").waitFor();
 check(JSON.stringify((await tabs.allTextContents()).slice(-2))===JSON.stringify(["Percepcja","Próbki"]),"samples tab not after perception");
 const bar=page.getByTestId("study-recording-bar");
 check(await bar.getByRole("button").count()===2,"recording bar still crowded");
 check(await bar.locator("details").count()===0,"header still contains management");
 check(await page.getByTestId("study-photo").count()===0,"photo duplicated on control");
 check(await page.getByTestId("camera-preview-toggle").locator("svg.lucide-eye,svg.lucide-eye-off").count()===1,"preview still uses a camera icon");
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-record"]')?.disabled===false);
 await page.getByTestId("study-record").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-end"]')?.disabled===false);
 const label=await page.getByTestId("study-recording-state").textContent();
 for(const name of ["Próbki","Sterowanie","Próbki"]){
  await page.getByRole("tab",{name,exact:true}).click();
  check(await page.getByTestId("study-recording-state").textContent()===label,"tab changed session");
  check(await page.getByTestId("study-end").isEnabled(),"tab ended recording");
 }
 check(await page.getByTestId("study-photo").count()===1,"sample action not unique");
 await page.getByTestId("samples-session").waitFor();
 check(writes.every(row=>row.path.endsWith("/samples/sessions")),"tab navigation sent unexpected writes");
 await page.getByTestId("study-end").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-record"]')?.disabled===false);
 const first=(await(await page.request.get(origin+"/api/v1/rider/samples/sessions")).json()).sessions[0].session_id;
 await page.getByTestId("study-record").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-end"]')?.disabled===false);
 await page.getByTestId("study-end").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-record"]')?.disabled===false);
 const sessions=(await(await page.request.get(origin+"/api/v1/rider/samples/sessions")).json()).sessions;
 check(sessions.length===6,"two new recordings did not preserve four old sessions");
 await page.getByTestId("samples-session").selectOption(first);
 await page.getByTestId("samples-export").waitFor();
 check((await page.getByTestId("samples-export").getAttribute("href")).includes(first),"archive points to wrong session");
 for(const lang of ["en","de","pl"]){await page.getByRole("combobox",{name:"Fixture language"}).selectOption(lang);check(await page.getByRole("tab").count()===7,"translated tab disappeared");}
 await page.setViewportSize({width:390,height:844});
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"mobile page overflow");
 check(writes.every(row=>row.path.endsWith("/samples/sessions")||row.path.endsWith("/end")),"unexpected camera or actuation write");
 return{result:"PASS",tabs:7,recordingSurvivesTabs:true,headerButtons:2,archiveSessions:sessions.length,robotContacted:false};
}
