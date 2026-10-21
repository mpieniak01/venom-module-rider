// Fixture-only LAN-origin mount, UUID fallback and recovery; all Rider API is intercepted.
async page => {
  const origin = new URL(page.url()).origin;
  const errors = [], writes = [], requests = [];
  const context = page.context();
  let online = true, refreshes = 0, generation = 1;
  const config = {devices:[{id:"http-lan-fixture",label:"HTTP LAN fixture",mode:"device",transport_profile:"trusted_lan"}],device_id:"http-lan-fixture",session:"http-lan-fixture-session"};
  const status = () => ({connection:{status:online?"online":"offline",configured:true,device_id:config.device_id,label:"HTTP LAN fixture",mode:"device",session:config.session,last_seen:1,error:online?null:"pi_name_unresolved",transport:{profile:"trusted_lan",state:online?"online":"backoff",generation,identity_verified:online,error:online?null:"pi_name_unresolved",retry_in_ms:online?0:800}},polling:{overview_ms:2000,control_ms:500,calibration_ms:1000,resources_ms:5000,face_ms:5000,compute_ms:5000,vision_ms:500},control:null,settings:null,telemetry:null,health:null,system:null,catalog:null,logic:null,history:null,resources:{},face:{library:null,runtime:null},errors:online?{}:{control:"pi_name_unresolved"}});
  const listener = error => errors.push(error.message);
  page.on("pageerror", listener);
  await page.evaluate(()=>sessionStorage.setItem("rider-http-lan-fixture","1"));
  await page.addInitScript(() => { if(sessionStorage.getItem("rider-http-lan-fixture")==="1")Object.defineProperty(globalThis.crypto,"randomUUID",{configurable:true,value:undefined}); });
  const handler = async route => {
    const request=route.request(), url=new URL(request.url()), path=url.pathname;
    requests.push(url.origin);
    if(request.method()!=="GET")writes.push(path);
    if(path.endsWith("/connection/refresh")){
      refreshes++; await new Promise(resolve=>setTimeout(resolve,50)); online=true;generation++;
      return route.fulfill({json:{connection:status().connection,recovery:{attempted:true,state:"recovered",transport_profile:"trusted_lan"}}});
    }
    if(request.method()!=="GET")throw new Error("Unexpected fixture mutation: "+path);
    if(path.endsWith("/config"))return route.fulfill({json:config});
    if(path.endsWith("/status"))return route.fulfill({json:status()});
    return route.fulfill({status:503,json:{detail:{code:"fixture_passive_unavailable"}}});
  };
  await page.route("**/api/v1/rider/**",handler);
  let phone;
  try {
    await page.reload();
    await page.getByTestId("rider-refresh").waitFor();
    if(!await page.evaluate(()=>typeof crypto.randomUUID==="undefined"&&typeof crypto.getRandomValues==="function"))throw new Error("UUID HTTP fallback fixture not active");
    phone=await context.newPage();phone.on("pageerror",listener);await phone.setViewportSize({width:390,height:844});
    await phone.addInitScript(()=>Object.defineProperty(globalThis.crypto,"randomUUID",{configurable:true,value:undefined}));
    await phone.route("**/api/v1/rider/**",handler);
    await phone.goto(page.url());await phone.getByTestId("rider-refresh").waitFor();
    online=false;
    await page.waitForFunction(()=>document.querySelector('[data-testid="rider-transport-state"]')?.textContent?.includes("ponown")||document.querySelector('[data-testid="rider-transport-state"]')?.textContent?.includes("retry"));
    await page.getByTestId("rider-refresh").click();
    await page.waitForFunction(()=>document.querySelector('[data-testid="rider-transport-state"]')?.textContent?.includes("potwierdzona")||document.querySelector('[data-testid="rider-transport-state"]')?.textContent?.includes("verified"));
    if(refreshes!==1||writes.some(path=>!path.endsWith("/connection/refresh")))throw new Error("Recovery replayed an operation");
    if(requests.some(value=>value!==origin))throw new Error("Rider API escaped browser origin");
    if(errors.length)throw new Error(errors.join(" | "));
    return {flow:"PASS",uuidFallback:true,twoBrowsers:true,phoneWidth:390,refreshes,noReplay:true,sameOrigin:true};
  } finally {
    if(phone)await phone.close();
    page.off("pageerror",listener);
    await page.unroute("**/api/v1/rider/**",handler);
    await page.evaluate(()=>{sessionStorage.removeItem("rider-http-lan-fixture");delete globalThis.crypto.randomUUID;});
  }
}
