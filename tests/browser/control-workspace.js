// Every Rider request is fulfilled here. No robot endpoint or live ARM is used.
async page => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const base = new URL(page.url()).origin;
  check(["localhost", "127.0.0.1", "[::1]"].includes(new URL(base).hostname), "loopback UI required");
  await page.goto("about:blank");
  // Only the producer fields exercised by this UI scenario; canonical schema
  // validation remains in tests/web/ui_contracts.test.ts.
  const settings = {
    revision: 1,
    directions: Object.fromEntries(["forward","backward","left","right"].map(direction => [direction,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),
    parameter_schema: {direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},
    control_modes: {version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500},
  };
  let armed = false, owner = null, holder = null, generation = "fixture-0", serial = 0;
  let configured = true;
  let connection = "online", reject = false, output = {lx:0, az:0}, controlMissing = false;
  const calls = [];
  const policy = {revision:1,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95],verified:false,perception:{engine:"depth_plane"}};
  const safety = () => ({state:armed?"ARMED":"DISARMED",owner,generation,panel_motion_enabled:true,ready:true,can_move:armed,stale:false,observed_at:Date.now()/1000,output,capabilities:{move:{available:true}},panel_control:{version:1,holder},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  await page.route("**/api/v1/rider/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json = (body,status=200) => route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if (req.method() !== "GET") {
      const body = req.postDataJSON(); calls.push({path,body});
      if (path === "/control") {
        if (reject) return json({detail:{code:"fixture_rejection_"+"details_".repeat(30)}},409);
        if (body.action === "arm") { armed=true; owner="manual";holder=body.panel_id;generation="fixture-"+(++serial); }
        if (["disarm","stop"].includes(body.action)) {armed=false;owner=null;holder=null;generation="fixture-"+(++serial);output={lx:0,az:0};}
        if (body.action==="move") output=body.mode==="release"?{lx:0,az:0}:{lx:.08,az:0};
        return json({ok:true,data:{ok:true}});
      }
      return json({detail:{code:"unexpected_fixture_mutation"}},409);
    }
    if (path==="/config") return json({devices:[{id:"fixture-demo",label:"Fixture",mode:"demo"}],device_id:"fixture-demo",session:"workspace-fixture"});
    if (path==="/status") return json({
      connection:{status:connection,configured,device_id:"fixture-demo",mode:"demo",session:"workspace-fixture"},
      polling:{control_ms:200},settings,errors:controlMissing?{control:"fixture_read_unavailable"}:{},
      control:controlMissing?null:{server_time:Date.now()/1000,safety:safety()},
      health:{camera:{on:false}},telemetry:{camera:{on:false}},catalog:{features:[]},logic:{},resources:{},face:{}
    });
    if (path.startsWith("/navigation/")) return json({config:policy,status:{running:false,stale:false},goals:{observe:false,drive:false},runtime:{observe:true,drive:true,known:true},detector:{configured:false}});
    if (path==="/camera/pipeline") return json({supported:false});
    return json({});
  });
  const wait = async (fn,message) => {const until=Date.now()+7000;while(!await fn()){check(Date.now()<until,message);await new Promise(r=>setTimeout(r,40));}};
  const ids = ["rider-forward","rider-left","rider-pad-stop","rider-right","rider-backward","rider-stop"];
  const boxes = async () => {
    const result = {};
    for (const id of ids) {const box=await page.getByTestId(id).boundingBox();check(box,"missing "+id);result[id]=box;}
    return result;
  };
  const same = async (before,step) => {
    const after=await boxes();
    for(const id of ids)for(const key of ["x","y","width","height"])check(Math.abs(before[id][key]-after[id][key])<=.5,step+" moved "+id+" "+key+": "+before[id][key]+" -> "+after[id][key]);
  };
  const stable = async () => {
    let previous = "", matches = 0;
    await wait(async () => {
      const current = JSON.stringify(await boxes());
      matches = current === previous ? matches + 1 : 0;
      previous = current;
      return matches >= 4;
    }, "responsive host layout did not settle");
  };
  const scenarios = [];
  try {
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(base+"/rider");
    await page.getByTestId("rider-forward").waitFor();
    await wait(async()=>!await page.getByTestId("rider-arm").isDisabled(),"fixture ARM unavailable");
    const initial=await boxes();
    check(initial["rider-backward"].y+initial["rider-backward"].height<1000,"desktop pad below viewport");
    check(await page.getByTestId("rider-forward").isDisabled(),"disarmed arrow enabled");
    check(calls.length===0,"page startup mutated robot");
    await page.getByTestId("workspace-automatic").click();
    check(await page.getByTestId("loop-start").isVisible(),"automatic task commands hidden");
    await same(initial,"automatic view");
    await page.getByTestId("workspace-manual").click();
    await same(initial,"manual view");
    check(calls.length===0,"view changed authority/resources");
    await page.getByTestId("rider-arm").click();
    await wait(async()=>!await page.getByTestId("rider-forward").isDisabled(),"fixture manual permission missing");
    await page.getByTestId("rider-messages").getByRole("status").filter({hasText:"Polecenie przyjęte"}).waitFor();
    await same(initial,"ARM and success notice");
    reject=true;
    await page.getByTestId("rider-arm").click();
    await page.getByTestId("rider-messages").getByRole("alert").waitFor();
    await same(initial,"multiline rejection");
    reject=false;
    connection="offline";controlMissing=true;
    await wait(async()=>(await page.getByTestId("operator-messages").innerText()).includes("Ostatni znany stan"),"offline notice absent");
    await same(initial,"offline notice");
    const lastMessage=page.getByTestId("operator-messages").locator("p").last();
    await lastMessage.scrollIntoViewIfNeeded();
    const messages=await lastMessage.boundingBox();
    check(messages && messages.y>=0 && messages.y+messages.height<=1000,"last offline message cannot be reached inside the viewport");
    await same(initial,"scrolling offline messages keeps controls fixed");
    check(await page.getByTestId("rider-arm").getAttribute("aria-pressed")==="true","missing read faked disarm");
    connection="online";controlMissing=false;
    await wait(async()=>!await page.getByTestId("rider-forward").isDisabled(),"manual control not restored");
    await page.getByTestId("rider-manual-mode").selectOption("continuous");
    await same(initial,"continuous selection");
    const selectCount=calls.length;
    await new Promise(r=>setTimeout(r,100));
    check(calls.length===selectCount,"mode selection started movement");
    await page.getByTestId("rider-forward").click();
    await wait(()=>calls.some(c=>c.body.action==="move"&&c.body.mode==="hold"),"continuous direction not forwarded");
    await same(initial,"continuous ACK");
    await page.getByTestId("rider-pad-stop").click();
    await wait(()=>calls.some(c=>c.body.action==="move"&&c.body.mode==="release"),"manual brake not forwarded");
    check(armed,"manual brake disarmed");
    await same(initial,"manual brake notice");
    await page.getByTestId("rider-forward").click();
    await wait(async()=>await page.getByTestId("rider-forward").getAttribute("aria-pressed")==="true","second continuous intent missing");
    const beforeModeChange=calls.length;
    await page.getByTestId("rider-manual-mode").selectOption("pulse");
    await page.getByTestId("manual-feedback").getByText("Brak",{exact:true}).waitFor();
    await wait(()=>calls.slice(beforeModeChange).some(c=>c.body.action==="move"&&c.body.mode==="release"),"mode switch did not release previous direction");
    check(armed,"input selection revoked operator ARM");
    await same(initial,"active input mode change");
    await page.getByTestId("rider-left").click();
    await wait(()=>calls.slice(beforeModeChange).some(c=>c.body.action==="move"&&c.body.mode==="pulse"),"pulse not forwarded");
    await page.getByTestId("manual-feedback").getByText("Brak",{exact:true}).waitFor();
    await page.getByTestId("rider-manual-mode").selectOption("continuous");
    check(await page.getByTestId("rider-forward").getAttribute("aria-pressed")==="false","continuous selection revived old intent");
    await same(initial,"no intent after pulse");
    await page.getByTestId("workspace-automatic").click();
    await page.getByTestId("workspace-content").evaluate(el=>{el.scrollTop=el.scrollHeight;});
    await same(initial,"scrolling automatic details");
    scenarios.push("desktop: views, ARM, success/error/offline, continuous/brake, independent scrolling");
    for(const viewport of [{width:390,height:844},{width:768,height:1024}]) {
      await page.setViewportSize(viewport);
      await page.evaluate(()=>window.scrollTo(0,0));
      await stable();
      const before=await boxes();
      const viewportContent = await page.getByTestId("workspace-content").boundingBox();
      check(viewportContent && viewportContent.height>=40 && viewportContent.y+viewportContent.height<=viewport.height, "camera/configuration scrollport clipped "+viewport.width);
      check(before["rider-backward"].y+before["rider-backward"].height<=viewport.height,"pad below viewport "+viewport.width);
      check(before["rider-forward"].x>=0&&before["rider-right"].x+before["rider-right"].width<=viewport.width,"pad outside viewport "+viewport.width);
      await page.getByTestId("workspace-manual").click();
      await same(before,"responsive manual view");
      await page.getByTestId("rider-manual-mode").selectOption("continuous");
      await same(before,"responsive input selection");
      await page.getByTestId("workspace-expand").click();
      await stable();
      const expanded=await boxes();
      check(expanded["rider-backward"].y+expanded["rider-backward"].height<=viewport.height,"expanded pad hidden");
      await page.getByTestId("workspace-automatic").click();
      await same(expanded,"expanded automatic view");
      await page.getByTestId("workspace-expand").click();
      await stable();
      scenarios.push(viewport.width+": fixed controls and expanded workspace");
    }
    await page.getByTestId("rider-stop").click();
    await wait(()=>!armed,"global stop did not disarm fixture");
    check(calls.filter(c=>c.body.action==="arm").length===1,"unexpected ARM");
    await page.setViewportSize({width:390,height:844});
    await page.reload();
    await page.getByTestId("rider-forward").waitFor();
    await stable();
    const phonePage=await page.getByTestId("rider-page").boundingBox();
    const phonePreview=await page.getByTestId("workspace-content").boundingBox();
    check(phonePage && phonePage.y===0 && phonePage.height===844,"phone did not open focused workspace");
    check(phonePreview && phonePreview.height>=150 && phonePreview.y+phonePreview.height<=844,"phone preview inaccessible");
    check(calls.filter(c=>c.body.action==="arm").length===1,"phone startup armed");
    scenarios.push("phone first load: focused workspace, accessible preview, no ARM");
    for(const viewport of [{width:1280,height:720},{width:1366,height:768}]) {
      const callsBefore=calls.length;
      await page.setViewportSize(viewport);
      await page.reload();
      await page.getByTestId("rider-forward").waitFor();
      await stable();
      const surface=await page.getByTestId("rider-page").boundingBox();
      const controls=await boxes();
      check(surface && surface.y===0 && surface.height===viewport.height,"short desktop did not open focused workspace");
      check(controls["rider-backward"].y+controls["rider-backward"].height<=viewport.height,"short desktop hid backward control");
      await page.getByTestId("operator-messages").scrollIntoViewIfNeeded();
      await same(controls,"short desktop operator messages");
      check(calls.length===callsBefore,"short desktop startup mutated robot");
      scenarios.push(viewport.width+"x"+viewport.height+": complete controls and scrollable messages, no actuation");
    }
    configured=false;
    await wait(async()=>await page.getByTestId("operator-console").count()===0,"unconfigured fallback not shown");
    check(await page.getByTestId("rider-messages").count()===1,"fallback lost operator messages");
    scenarios.push("unconfigured fallback retains messages");
    return {result:"PASS",scenarios,fixtureOnly:true,mutations:calls.length,desktopTargets:initial};
  } finally {
    await page.goto("about:blank");
    await page.unroute("**/api/v1/rider/**");
  }
}
