// All Rider requests are intercepted; this scenario never contacts a robot.
async page => {
  const base = new URL(page.url()).origin;
  if (!["localhost", "127.0.0.1", "[::1]"].includes(new URL(base).hostname)) throw new Error("loopback UI required");
  await page.goto("about:blank");
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const wait = async (fn, message) => {
    const end = Date.now() + 7000;
    while (!await fn()) { check(Date.now() < end, message); await new Promise(resolve => setTimeout(resolve, 40)); }
  };
  const calls = [];
  let cameraOn = true, refuseStop = false, reads = 0, pipelineUnavailable = true;
  const profile = {analysis_location:"venom", local_engine:"canny", remote_transport:"snapshots"};
  const pipeline = () => ({contract_id:"rider.camera_pipeline",contract_version:1,revision:1,profile,active_profile:cameraOn?profile:null,state:cameraOn?"active":"stopped",transport_epoch:1,camera_session:cameraOn?"fixture-camera":null,capabilities:{analysis_locations:["venom","local"],local_engines:["canny"],remote_transports:["snapshots"]},effective_stages:{capture:cameraOn},error:null});
  await page.route("**/api/v1/rider/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.slice("/api/v1/rider".length);
    const json = (body,status=200) => route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if (request.method() !== "GET") {
      const body = request.postDataJSON(); calls.push({path,body});
      check(path === "/camera/pipeline", "unexpected mutation " + path);
      check(["start","stop"].includes(body.action), "unexpected camera action");
      if (body.action === "stop" && refuseStop) return json({detail:{code:"resource_in_use"}},409);
      cameraOn = body.action === "start";
      return json({ok:true,meta:{},data:pipeline()});
    }
    if (path === "/config") return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"camera-fixture"});
    if (path === "/status") return json({connection:{configured:true,status:"online",session:"camera-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:200},errors:{},settings:{revision:1},control:{server_time:Date.now()/1000,safety:{state:"DISARMED",generation:"fixture",owner:null}},health:{},telemetry:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if (path === "/camera/pipeline") return pipelineUnavailable ? json({detail:{code:"fixture_unavailable"}},503) : json(pipeline());
    if (path.startsWith("/navigation/")) return json({config:{revision:1,mode:"observe"},status:{running:true,mode:"observe",stale:false},goals:{observe:true,drive:false},runtime:{known:true,observe:true,drive:true},detector:{configured:false}});
    if (path.startsWith("/camera/")) {
      reads++;
      return route.fulfill({contentType:"image/png",body:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN2kAAAAASUVORK5CYII=","base64"),headers:{"X-Rider-Stale":"false","X-Rider-Frame-Age-Ms":"10","X-Rider-Frame-Id":"fixture-"+reads}});
    }
    return json({});
  });
  try {
    await page.goto(base + "/rider");
    const power = page.getByTestId("camera-power-toggle"), preview = page.getByTestId("camera-preview-toggle");
    await power.waitFor();
    check(await power.isVisible() && await power.isDisabled(), "missing pipeline hid or enabled camera control");
    check((await power.innerText()).includes("?"), "missing pipeline invented a known power state");
    await wait(async () => (await power.getAttribute("title") || "").includes("odczytać"), "read failure explanation absent");
    pipelineUnavailable = false;
    await wait(async () => !await power.isDisabled(), "camera control did not recover");
    await page.getByRole("tab",{name:"Pomiary",exact:true}).click();
    pipelineUnavailable = true;
    await page.getByRole("tab",{name:"Sterowanie",exact:true}).click();
    check(await power.isVisible(), "returning to control hid camera power");
    await wait(async () => await power.isDisabled(), "stale camera action remained enabled");
    pipelineUnavailable = false;
    await wait(async () => !await power.isDisabled(), "camera control did not recover after tab change");
    check(calls.length === 0, "page startup changed resources");
    await page.getByRole("button",{name:"Obraz surowy",exact:true}).click();
    await wait(() => reads > 0, "raw preview not read");
    await page.getByRole("button",{name:"Wizja Venom",exact:true}).click();
    await preview.click();
    check(cameraOn && calls.length === 0, "presentation stopped camera or analysis");
    refuseStop = true;
    await power.click();
    await wait(() => calls.length === 1, "OFF not submitted");
    await wait(async () => !await power.isDisabled(), "failed OFF stuck pending");
    check(cameraOn && (await page.getByTestId("camera-power-state").innerText()).includes("Aktywna"), "foreign consumer falsely confirmed OFF");
    refuseStop = false;
    await power.click();
    await wait(async () => (await power.innerText()).includes("Włącz"), "OFF not confirmed");
    check(!cameraOn, "camera remained active after confirmed OFF");
    await power.click();
    await wait(() => cameraOn, "camera ON not submitted");
    check(calls.every(call => call.path === "/camera/pipeline"), "camera ON armed or resumed robot");
    await wait(async () => (await power.innerText()).includes("Wyłącz"), "camera ON status not confirmed");
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:"output/playwright/camera-lifecycle-desktop.png",fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:"output/playwright/camera-lifecycle-mobile.png",fullPage:true});
    return {scenarios:["OFF stays visible during unavailable pipeline and tab changes", "view and hide preserve capture", "foreign consumer remains visibly ON", "explicit OFF confirmed", "ON never arms or resumes"],calls};
  } finally { await page.unroute("**/api/v1/rider/**"); }
}
