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
  const results = [];
  try {
    for (const viewport of [{width:1440,height:1000},{width:390,height:844},{width:768,height:1024}]) {
      await page.setViewportSize(viewport);
      await page.goto(base+"/rider");
      await page.getByTestId("rider-forward").waitFor();
      await wait(async()=>!await page.getByTestId("rider-refresh").isDisabled(),"fixture not ready");
      const inspect = async () => {
        const buttons = await page.getByTestId("rider-page").locator("button[data-size]:visible").evaluateAll(nodes => nodes.map(node => ({size:node.dataset.size,height:node.getBoundingClientRect().height,radius:getComputedStyle(node).borderRadius,text:node.textContent,overflow:node.scrollWidth>node.clientWidth+1})));
        for (const button of buttons) {
          check(button.height===(button.size==="large"?44:36),"unexpected action size: "+JSON.stringify(button));
          check(button.radius==="10px","inconsistent action shape");
          check(!button.overflow,"action label clipped: "+button.text);
        }
        check(await page.getByTestId("rider-stop").isEnabled(),"STOP disabled");
        return buttons.length;
      };
      let count=await inspect();
      const stop=page.getByTestId("rider-stop");
      const before=await stop.boundingBox();
      await stop.hover();
      await page.getByRole("tooltip").waitFor();
      const tooltip=await page.getByRole("tooltip").boundingBox();
      check(tooltip&&tooltip.x>=0&&tooltip.y>=0&&tooltip.x+tooltip.width<=viewport.width&&tooltip.y+tooltip.height<=viewport.height,"tooltip outside viewport");
      check(JSON.stringify(await stop.boundingBox())===JSON.stringify(before),"tooltip moved STOP");
      check(tooltip.y>=before.y+before.height||tooltip.y+tooltip.height<=before.y||tooltip.x>=before.x+before.width||tooltip.x+tooltip.width<=before.x,"tooltip obscures emergency STOP");
      check((await stop.getAttribute("aria-describedby")).includes(await page.getByRole("tooltip").getAttribute("id")),"tooltip not associated");
      await page.mouse.move(0,0);
      await stop.focus();
      await page.getByRole("tooltip").waitFor();
      await page.getByTestId("rider-refresh").focus();
      await page.getByTestId("workspace-automatic").click();
      count+=await inspect();
      await page.getByTestId("workspace-manual").click();
      await page.getByTestId("rider-refresh").focus();
      await page.getByTestId("workspace-content").focus();
      await page.mouse.move(0,0);
      await page.screenshot({path:"ux-"+viewport.width+".png"});
      if(viewport.width===1440) {
        await page.evaluate(()=>{document.documentElement.dataset.theme="venom-light";document.documentElement.style.colorScheme="light";});
        await inspect();
        await stop.focus();
        await page.getByRole("tooltip").waitFor();
        await page.screenshot({path:"ux-light-tooltip.png"});
        await page.evaluate(()=>{document.documentElement.dataset.theme="venom-dark";document.documentElement.style.colorScheme="dark";});
        await page.getByTestId("workspace-content").focus();
      }
      results.push({viewport,buttonsChecked:count,tooltip:"hover/focus/viewport/description PASS"});
    }
    check(calls.length===0,"visual interactions mutated robot");
    await page.setViewportSize({width:1440,height:1000});
    await page.reload();
    await page.getByTestId("rider-forward").waitFor();
    for (const name of ["Pomiary","Kalibracja","Cele i zasoby","Face Studio","Percepcja"]) {
      await page.getByRole("tab",{name,exact:true}).click();
      await page.screenshot({path:"ux-tab-"+name.replaceAll(" ","-")+".png"});
      const sizes=await page.getByTestId("rider-page").locator("button[data-size]:visible").evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().height));
      check(sizes.every(height=>[36,44].includes(height)),"mixed sizes in "+name);
      results.push({tab:name,buttonsChecked:sizes.length});
    }
    await page.getByRole("tab",{name:"Sterowanie",exact:true}).click();
    const stop=page.getByTestId("rider-stop");
    await stop.focus();
    await page.getByRole("tooltip").waitFor();
    check(calls.length===0,"browsing mutated robot");
    await page.keyboard.press("Escape");
    await wait(()=>calls.some(call=>call.body.action==="stop"),"tooltip intercepted emergency Escape");
    check(await page.getByRole("tooltip").count()===0,"Escape did not dismiss tip");
    check(calls.length===1,"unexpected mutation beyond fixture STOP");
    return {result:"PASS",fixtureOnly:true,results,escapeStopPreserved:true};
  } finally {
    await page.goto("about:blank");
    await page.unroute("**/api/v1/rider/**");
  }
}
