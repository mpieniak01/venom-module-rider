// Run with playwright-cli against the isolated loopback module stack.
async page => {
  const origin=new URL(page.url());
  if(!["localhost","127.0.0.1","[::1]"].includes(origin.hostname))throw new Error("loopback fixture required");
  const base=origin.origin+"/api/v1/rider";
  const config=await (await page.request.get(base+"/config")).json();
  if(!config.devices?.length||config.devices.some(item=>item.mode!=="demo"))throw new Error("fixture-only allowlist required");
  const check=(ok,message)=>{if(!ok)throw new Error(message);};
  const controls=[];
  const captures=[];
  page.on("request",request=>{if(request.method()==="POST"&&request.url().endsWith("/control"))controls.push(request.postDataJSON());if(request.url().endsWith("/samples/capture"))captures.push(request.postDataJSON());});
  await page.getByTestId("camera-preview-toggle").click();
  await page.getByText("Tor kamery i zbieranie próbek",{exact:true}).click();
  await page.getByTestId("samples-start").click();
  await page.getByTestId("samples-save").waitFor({state:"visible"});
  await page.waitForFunction(()=>!document.querySelector('[data-testid="samples-save"]').disabled);
  const displayed=await page.evaluate(async()=>{
    for(let attempt=0;attempt<12;attempt++){
      const image=document.querySelector("img[data-capture-key]");
      const button=document.querySelector('[data-testid="samples-save"]');
      if(!image||!button)throw new Error("visible source missing");
      const url=image.currentSrc;
      const bytes=Array.from(new Uint8Array(await (await fetch(url)).arrayBuffer()));
      if(image.currentSrc===url&&image.complete&&!button.disabled){const key=image.dataset.captureKey;button.click();return {key,bytes};}
    }
    throw new Error("could not bind the displayed source");
  });
  await page.waitForFunction(()=>document.querySelector('[data-testid="samples-result"]')?.textContent?.includes("Zapisano w Venomie"));
  check(controls.length===0,"manual RAW capture armed or moved robot");
  const first=captures[0];
  const outcome=await (await page.request.get(base+"/samples/requests/"+first.capture_request_id)).json();
  const sample=await (await page.request.get(base+"/samples/items/"+outcome.sample_id)).json();
  check(first.frame_key===displayed.key,"request differs from displayed source ticket");
  const raw=await (await page.request.get(base+"/samples/items/"+outcome.sample_id+"/original")).body();
  check(raw.equals(Uint8Array.from(displayed.bytes)),"saved RAW differs from visible blob");
  await page.getByTestId("takeover-manual").click();
  await page.getByTestId("rider-manual-mode").selectOption("continuous");
  await page.getByTestId("rider-forward").click();
  await page.waitForTimeout(600);
  const count=controls.length;
  await page.getByTestId("samples-save").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="samples-result"]')?.textContent?.includes("Zapisano w Venomie"));
  await page.waitForTimeout(500);
  check(await page.getByTestId("rider-forward").getAttribute("aria-pressed")==="true","capture cancelled continuous direction");
  check(!controls.slice(count).some(item=>["arm","disarm","stop"].includes(item.action)||item.mode==="release"),"capture altered movement ownership");
  await page.getByTestId("rider-pad-stop").click();
  await page.waitForTimeout(600);
  check(await page.getByTestId("rider-forward").getAttribute("aria-pressed")==="false","STOP failed during data collection");
  await page.getByTestId("samples-end").click();
  await page.getByRole("tab",{name:"Próbki",exact:true}).click();
  if(await page.getByRole("tab",{name:"Próbki",exact:true}).count())await page.getByRole("tab",{name:"Próbki",exact:true}).click();
  if(await page.getByTestId("samples-browser").locator(":scope > summary").count())await page.getByTestId("samples-browser").locator(":scope > summary").click();
  await page.getByTestId("samples-session").selectOption(first.session_id);
  await page.getByTestId("sample-thumbnail").first().click();
  await page.getByLabel("Scena",{exact:true}).fill("fixture floor");
  await page.getByLabel("Oczekiwana ocena drogi",{exact:true}).selectOption("clear");
  await page.getByLabel("Weryfikacja",{exact:true}).selectOption("approved");
  await page.getByTestId("sample-label-save").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="sample-editor"]')?.textContent?.includes('Wersja etykiety: 1'));
  const downloadPromise=page.waitForEvent("download");
  await page.getByTestId("samples-export").click();
  const download=await downloadPromise;
  check(!(await download.failure()),"export failed");
  await page.route("**/api/v1/rider/status*",route=>route.abort());
  await page.getByTestId("samples-refresh").click();
  await page.waitForTimeout(700);
  check(await page.getByTestId("sample-thumbnail").count()>=2,"library disappeared after lost Pi status");
  return {flow:"PASS",manualCaptureWithoutArm:true,visibleRawExact:true,continuousCaptureAndStop:true,offlineLibrary:true,versionedLabel:true,export:download.suggestedFilename(),session:first.session_id};
}
