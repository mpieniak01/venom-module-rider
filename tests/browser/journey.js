// All Rider routes, including ARM/STOP/journey, are intercepted before opening the panel.
async page => {
  const base=new URL(page.url()).origin;
  const check=(ok,message)=>{if(!ok)throw new Error(message);};
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const wait=async(fn,message)=>{const end=Date.now()+9000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,35));}};
  const calls=[],sha="a".repeat(64);
  let capability=false,armed=false,owner=null,holder=null,arming=null,serial=0,generation="fixture-0",operator="idle",journey=null,stale=false;
  let rejectStop=false;
  const advance=()=>{generation="fixture-"+(++serial);};
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(direction=>[direction,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  const policy={revision:1,mode:"drive",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95],perception:{engine:"depth_plane"},verified:true,calibration_id:"operator-fixture",model_sha256:sha,avoidance:{enabled:false,protocol_version:3,sweep_verified:true,turn_speed:.05,turn_ms:100,max_turn_pulses:3,geometry:{mode:"image"}}};
  const safety=()=>({state:armed?"ARMED":"DISARMED",owner,arming_mode:arming,generation,navigation_journey_version:capability?1:undefined,panel_motion_enabled:true,ready:true,can_move:armed,stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder},operator_control:{version:1,state:operator},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:policy,status:{running:!!journey?.active,stale,mode:"drive",run_id:"fixture-run",operator_control_version:1,journey,decision:{state:"clear",clear_count:3,action:journey?.active?"go":"stop",vision_action:"go"}},goals:{drive:!!journey?.active,observe:false},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,clear_frames_required:3,navigation_journey_version:capability?1:undefined,ground_geometry_supported:true,supported_protocol_versions:[3]}});
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){
      const body=req.postDataJSON();calls.push({path,body});
      if(path==="/connection/refresh")return json({connection:{status:"online"},recovery:{attempted:false,state:"connected"}});
      if(path==="/navigation/journey"){
        check(body.journey_version===1&&body.operator_control_version===1&&typeof body.panel_id==="string"&&body.operator_consent===true,"journey consent/version missing");
        check(body.config_revision===policy.revision&&body.generation===generation,"journey stale policy or generation");
        check(body.action===undefined,"journey sent direct ARM action");
        armed=true;owner="navigation";arming="journey";holder=body.panel_id;operator="active";advance();
        journey={version:1,id:"journey-"+serial,active:true,state:"forward",reason:"clear",elapsed_s:35,maneuver_epoch:2};
        return json({ok:true,meta:{},data:{ok:true,config:policy}});
      }
      check(path==="/control","unexpected lifecycle mutation: "+path);
      if(body.action==="stop"){
        if(rejectStop)return json({detail:{code:"fixture_stop_rejected"}},409);
        armed=false;owner=null;arming=null;operator="idle";if(journey)journey={...journey,active:false,state:"stopped",reason:"operator_stop"};advance();
      }else if(body.action==="arm"){
        check(!journey?.active,"manual ARM before confirmed journey STOP");
        armed=true;owner="manual";arming="operator";holder=body.panel_id;advance();
      }else throw new Error("unexpected action: "+body.action);
      return json({ok:true,data:{ok:true,confirmed:true}});
    }
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"journey-fixture"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"journey-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},telemetry:{},health:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith("/navigation/"))return json(navigation());
    if(path==="/camera/pipeline")return json({supported:false});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  const scenarios=[];
  try{
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(base+"/rider");
    await page.getByTestId("workspace-journey").click();
    await page.getByTestId("journey-start").waitFor();
    check(await page.getByTestId("journey-start").isDisabled(),"missing capability enabled journey");
    check((await page.getByTestId("journey-blocks").innerText()).includes("obsługi podróży"),"capability gap not explained");
    check(calls.length===0,"startup or view change mutated robot");
    scenarios.push("capability mismatch is visible; startup and view changes do not ARM");
    capability=true;
    await wait(async()=>!(await page.getByTestId("journey-blocks").innerText()).includes("obsługi podróży"),"capability not refreshed");
    check(await page.getByTestId("journey-start").isDisabled(),"disabled avoidance allowed journey");
    check((await page.getByTestId("journey-blocks").innerText()).includes("Włącz omijanie"),"avoidance gap not explained");
    policy.avoidance.enabled=true;policy.revision+=1;
    await wait(async()=>!await page.getByTestId("journey-start").isDisabled(),"valid existing image policy blocked journey");
    await page.getByTestId("journey-start").click();
    await wait(()=>journey?.active,"journey did not start");
    await wait(async()=>(await page.getByTestId("journey-state").innerText()).includes("35"),"elapsed time beyond 10 s not displayed");
    check(calls.length===1&&calls[0].path==="/navigation/journey","one-click journey added lifecycle commands");
    for(const name of ["forward","left","right","backward"])check(await page.getByTestId("rider-"+name).isDisabled(),"journey allowed implicit manual "+name);
    await page.getByTestId("workspace-manual").click();
    await page.getByTestId("workspace-automatic").click();
    check(await page.getByTestId("loop-start").isDisabled(),"bounded trial can overlap journey");
    await page.getByTestId("workspace-journey").click();
    check(calls.length===1,"view switching restarted or paused journey");
    const refreshStart=calls.length;
    await page.getByTestId("rider-refresh").click();
    await wait(async()=>!await page.getByTestId("rider-refresh").isDisabled(),"journey refresh stayed busy");
    check(calls.slice(refreshStart).length===1&&calls[refreshStart].path==="/connection/refresh","refresh restarted or paused journey");
    check(journey.active&&await page.getByTestId("rider-forward").isDisabled(),"refresh changed journey ownership");
    scenarios.push("one start intent, existing accepted image geometry, elapsed 35 s, view/refresh never restart; no overlapping trial or arrow intervention");
    stale=true;
    await wait(async()=>(await page.getByTestId("journey-state").innerText()).includes("niepotwierdzony"),"stale journey shown as live motion");
    stale=false;
    await page.getByTestId("journey-stop").click();
    await wait(()=>!journey.active,"journey STOP did not complete");
    check(calls.at(-1).body.action==="stop","journey stop used warm navigation pause");
    check(calls.every(call=>call.path!=="/goal/s2_camera_preview"),"journey STOP switched camera");
    scenarios.push("stale status is unknown; STOP ends journey and preserves camera configuration");
    await wait(async()=>!await page.getByTestId("journey-start").isDisabled(),"new journey unavailable after explicit stop");
    await page.getByTestId("journey-start").click();
    await wait(()=>journey.active,"second journey did not start");
    await wait(async()=>await page.getByTestId("rider-forward").isDisabled(),"journey authority not observed");
    rejectStop=true;const rejected=calls.length;
    await page.getByTestId("takeover-manual").click();
    await wait(()=>calls.length>rejected,"takeover did not issue STOP");
    await wait(async()=>(await page.getByTestId("operator-messages").innerText()).includes("fixture_stop_rejected"),"rejected STOP not exposed");
    check(!calls.slice(rejected).some(call=>call.body.action==="arm"),"rejected STOP still armed manual");
    rejectStop=false;const takeover=calls.length;
    await page.getByTestId("takeover-manual").click();
    await wait(()=>owner==="manual","manual takeover not completed");
    await wait(async()=>!await page.getByTestId("rider-forward").isDisabled(),"manual arrows unavailable after confirmed takeover");
    check(calls.slice(takeover).map(call=>call.body.action).join(",")==="stop,arm","takeover did not serialize STOP then manual ARM");
    check(calls.every(call=>!["navigation_pause","navigation_resume"].includes(call.body.action)),"journey used trial warm authority");
    scenarios.push("manual takeover requires successful STOP, fresh state, then explicit manual ARM; rejected STOP never arms");
    return {passed:scenarios,mutations:calls.map(call=>({path:call.path,action:call.body.action??(call.path==="/navigation/journey"?"journey":"refresh")})),fixtureOnly:true};
  }finally{await page.goto("about:blank");await page.unrouteAll({behavior:"wait"});}
}
