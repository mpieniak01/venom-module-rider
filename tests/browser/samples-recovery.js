// A lost HTTP result cannot silently capture the next camera frame.
async page => {
  const origin=new URL(page.url());
  if(!["localhost","127.0.0.1","[::1]"].includes(origin.hostname))throw new Error("loopback required");
  const base=origin.origin+"/api/v1/rider";
  const config=await (await page.request.get(base+"/config")).json();
  if(!config.devices?.length||config.devices.some(item=>item.mode!=="demo"))throw new Error("fixture-only allowlist required");
  await page.unroute("**/api/v1/rider/status*");
  await page.reload();
  await page.getByTestId("camera-preview-toggle").click();
  await page.getByTestId("samples-start").click();
  await page.waitForFunction(()=>!document.querySelector('[data-testid="samples-save"]')?.disabled);
  let intent;
  let posts=0;
  await page.route("**/api/v1/rider/samples/capture",async route=>{
    intent=route.request().postDataJSON();posts++;
    await route.fetch(); // real admission/durable writer; only the response is lost
    await route.abort();
  });
  await page.getByTestId("samples-save").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="samples-result"]')?.textContent?.includes("Wynik nieznany"));
  if(!(await page.getByTestId("samples-save").isDisabled()))throw new Error("unknown result allowed another capture");
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('[data-testid="samples-result"]')?.textContent?.includes("Zapisano w Venomie"));
  const result=await page.getByTestId("samples-result").innerText();
  if(!result.includes(intent.capture_request_id)||posts!==1)throw new Error("reload replaced unknown request");
  const stored=await (await page.request.get(base+"/samples/sessions/"+intent.session_id)).json();
  if(stored.total!==1)throw new Error("duplicate or missing sample after lost result");
  await page.unroute("**/api/v1/rider/samples/capture");
  return {flow:"PASS",lostResultRecovered:true,sameRequestId:true,sourceCount:stored.total,posts};
}
