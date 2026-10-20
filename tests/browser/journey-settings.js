// Every Rider endpoint is intercepted before loading the UI; no real ARM or motor calls.
async page => {
  const base=new URL(page.url()).origin,check=(ok,message)=>{if(!ok)throw new Error(message);};
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const wait=async(fn,message)=>{const end=Date.now()+8000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,40));}};
  const sha="a".repeat(64),calls=[],saved=[];
  let observe=true,drive=false,rejectSave=true,rejectResume=false;
  // Same incomplete ground calibration and saved drive mode as the deployed Pi.
  let policy={protocol_version:1,revision:53,mode:"drive",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],verified:true,calibration_id:"operator-fixture",model_sha256:sha,perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2},avoidance:{enabled:false,protocol_version:3,sweep_verified:false,mirrored:false,turn_speed:.05,turn_ms:100,max_turn_pulses:3,max_stalled_corrections:2,progress_min_delta:.03,geometry:{mode:"ground",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45}}};
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(key=>[key,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  const safety=()=>({state:"DISARMED",owner:null,generation:"settings-fixture",navigation_journey_version:1,panel_motion_enabled:true,ready:true,can_move:false,stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder:null},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:policy,status:{running:observe||drive,stale:false,mode:drive?"drive":"observe",decision:{state:"clear",vision_action:"go",clear_count:3}},goals:{observe,drive},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,navigation_journey_version:1,ground_geometry_supported:true,supported_protocol_versions:[3]}});
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){
      const body=req.postDataJSON();calls.push({path,body});
      if(path==="/goal/s15_venom_perception"){
        if(body.enabled&&rejectResume)return json({detail:{code:"fixture_analysis_restart_failed"}},503);
        observe=body.enabled;return json({ok:true,meta:{},data:{ok:true}});
      }
      if(path==="/navigation/config"){
        check(!observe&&!drive,"saved before observation stopped");
        if(rejectSave)return json({detail:{code:"navigation_configuration_conflict"}},409);
        check(body.revision===policy.revision,"stale revision");
        check(body.values.mode==="observe"&&body.values.verified===false,"changed drive policy did not revoke drive mode");
        saved.push(body.values);policy={...body.values,protocol_version:1,revision:policy.revision+1};
        return json({ok:true,meta:{},data:{ok:true,persisted:true,config:policy}});
      }
      throw new Error("unexpected mutation: "+path);
    }
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"journey-settings-fixture"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"journey-settings-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},health:{},telemetry:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith("/navigation/"))return json(navigation());
    if(path==="/camera/pipeline")return json({supported:false});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  const scenarios=[];
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(base+"/rider");
    await page.getByTestId("workspace-journey").click();
    await page.getByRole("button",{name:"Otwórz ustawienia podróży"}).click();
    const geometry=page.getByTestId("geometry-mode"),save=page.getByTestId("settings-save");
    check(!await geometry.isDisabled(),"observation locks settings");
    check(await save.isDisabled(),"unchanged settings can be saved");
    check((await page.getByTestId("settings-save-help").innerText()).includes("Brak niezapisanych"),"disabled save reason missing");
    check((await page.getByTestId("geometry-mode-help").innerText()).includes("zmierzonych"),"calibration origin missing");
    check(calls.length===0,"opening settings mutated robot");
    await geometry.selectOption("image");check(!await save.isDisabled(),"observation prevents save after editing");
    await save.click();await page.getByTestId("settings-save-error").waitFor();
    check((await page.getByTestId("settings-save-error").innerText()).includes("navigation_configuration_conflict"),"save rejection hidden in Automat2");
    check(!calls.some(call=>call.body.enabled===true),"failed save resumed observation");
    scenarios.push("observation permits editing; disabled-save reason, geometry explanation and Pi rejection are visible");
    rejectSave=false;await save.click();await page.getByTestId("settings-save-success").waitFor();
    check(policy.mode==="observe"&&!policy.verified&&policy.revision===54,"saved policy incorrect");
    check((await page.getByTestId("settings-save-success").innerText()).includes("54"),"saved revision not shown");
    check(await save.isDisabled(),"saved settings still dirty");
    check(!await page.getByTestId("journey-preview").isDisabled(),"no way to start analysis for acceptance");
    check(await page.getByTestId("journey-start").isDisabled(),"save fabricated acceptance");
    scenarios.push("retry saves unaccepted observation revision 54 and exposes analysis action");
    await page.getByTestId("journey-preview").click();await wait(()=>observe,"preview not started");
    await wait(async()=>!await geometry.isDisabled(),"preview locks settings");
    await page.getByRole("checkbox",{name:"Omijaj przeszkody w tej próbie",exact:true}).check();
    const offset=calls.length;await save.click();await wait(()=>policy.revision===55&&observe,"save did not resume observation");
    await wait(async()=>await save.isDisabled(),"save stayed dirty");
    check(calls.slice(offset).map(call=>call.path).join(",")==="/goal/s15_venom_perception,/navigation/config,/goal/s15_venom_perception","wrong observation save sequence");
    scenarios.push("save serializes observation OFF, policy write, observation ON without ARM or journey");
    await page.getByRole("spinbutton",{name:"Prędkość skrętu · SDK",exact:true}).fill("0.04");
    rejectResume=true;await save.click();await page.getByTestId("settings-save-error").waitFor();
    check((await page.getByTestId("settings-save-error").innerText()).includes("zapisano na Pi, ale"),"saved policy misreported as failed on analysis restart");
    check(policy.revision===56&&!observe,"partial success not preserved");
    check(await page.getByTestId("settings-save-success").isVisible(),"confirmed revision lost on restart failure");
    scenarios.push("confirmed save remains visible when analysis restart fails");
    await wait(async()=>!await page.getByTestId("journey-preview").isDisabled(),"preview retry unavailable");
    await page.getByTestId("journey-preview").click();
    await page.getByTestId("navigation-request-error").waitFor();
    check((await page.getByTestId("navigation-request-error").innerText()).includes("fixture_analysis_restart_failed"),"preview error hidden in journey view");
    scenarios.push("failed observation start is visible in Automat2 without a false journey-start message");
    drive=true;await wait(async()=>await geometry.isDisabled(),"active driving did not lock settings");
    check(await save.isDisabled(),"active driving permits save");
    check((await page.getByTestId("settings-save-help").innerText()).includes("Zatrzymaj jazdę"),"drive lock not explained");
    check(calls.every(call=>call.path!=="/control"&&call.path!=="/navigation/journey"),"settings sent actuation");
    scenarios.push("active drive still locks editing and saving; no ARM, move or journey requests");
    return {passed:scenarios,fixtureOnly:true,savedPolicies:saved,mutations:calls.map(call=>({path:call.path,enabled:call.body.enabled,revision:call.body.revision}))};
  }finally{await page.goto("about:blank");await page.unrouteAll({behavior:"wait"});}
}
