// Every Rider endpoint is intercepted before loading the UI; no real ARM or motor calls.
async page => {
  const base=new URL(page.url()).origin,check=(ok,message)=>{if(!ok)throw new Error(message);};
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const wait=async(fn,message)=>{const end=Date.now()+8000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,40));}};
  const sha="a".repeat(64),calls=[],saved=[];
  let observe=true,drive=false,motionActive=false,rejectSave=false,rejectResume=false,generation="settings-fixture";
  // Same incomplete ground calibration and saved drive mode as the deployed Pi.
  let policy={motion_limits:{version:1,max_motion_ms:500,max_speed:1,max_turn_ms:500,max_turn_speed:1},protocol_version:1,revision:53,mode:"drive",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],verified:true,calibration_id:"operator-fixture",model_sha256:sha,model_tuning_supported:true,perception:{engine:"depth_plane",model_id:"depth-anything-v2-indoor-small",residual_limit:.06,outlier_limit:.005,slope_min:2},avoidance:{enabled:false,protocol_version:3,sweep_verified:false,mirrored:false,turn_speed:.05,turn_ms:100,max_turn_pulses:3,max_stalled_corrections:2,progress_min_delta:.03,geometry:{mode:"ground",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45}}};
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(key=>[key,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  settings.values={linear_speed:.6,turn_speed:.6,pulse_duration:.3,speed_limit:.6,linear_rate:.5,linear_deceleration:1,turn_rate:4,turn_deceleration:4,reversal_pause:0,ramp_mode:0};
  for(const [key,row] of Object.entries(settings.directions))Object.assign(row,{speed:.6,maximum:.6,duration:key==="forward"?.25:.3});
  settings.parameter_schema={direction_fields:Object.fromEntries(["speed","minimum","maximum","step","duration"].map(key=>[key,{type:"number",minimum:key==="duration"?.01:0,maximum:key==="duration"?.5:1,display_factor:key==="duration"?1000:100,display_unit:key==="duration"?"ms":"%",display_precision:2}])),value_fields:Object.fromEntries(["linear_rate","linear_deceleration","turn_rate","turn_deceleration","reversal_pause"].map(key=>[key,{minimum:key==="reversal_pause"?0:.001,maximum:key==="reversal_pause"?.25:100,display_factor:key==="reversal_pause"?1000:100,display_unit:key==="reversal_pause"?"ms":"%/s"}])),ramp_modes:[{value:0,id:"linear"},{value:1,id:"smooth_s"},{value:2,id:"logarithmic"}]};
  const modelCatalog=()=>({models:[{id:"depth-anything-v2-indoor-small",label:"Depth V2",engine:"depth_plane",installation:"ready",installed:true,selectable:true,drive_supported:true,sha256:sha},{id:"depth-fixture-second",label:"Depth fixture second",engine:"depth_plane",installation:"ready",installed:true,selectable:true,drive_supported:true,sha256:"b".repeat(64)}],selection:{model_id:policy.perception.model_id,artifact_sha256:sha,revision:policy.revision},provider:"venom",pipeline_revision:1,providers:[{id:"venom",available:true}],local_models:[],diagnostics:{},switching:false});
  const safety=()=>({state:motionActive?"ARMED":"DISARMED",owner:motionActive?"navigation":null,moving:motionActive,operator_control:{version:1,state:motionActive?"active":"idle"},generation,navigation_journey_version:1,panel_motion_enabled:true,ready:true,can_move:false,stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder:null},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:policy,status:{running:observe||drive,stale:false,mode:drive?"drive":"observe",decision:{state:"clear",vision_action:"go",clear_count:3}},goals:{observe,drive},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,navigation_journey_version:1,ground_geometry_supported:true,supported_protocol_versions:[3]}});
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){
      const body=req.postDataJSON();calls.push({path,body});
      if(path==="/control"){check(body.action==="stop","unexpected actuation");motionActive=false;drive=false;generation="stopped-"+calls.length;return json({ok:true,data:{status:"executed",state:"DISARMED",generation,safety:safety()}});}
      if(path==="/settings"){check(!observe&&!drive&&!motionActive,"manual settings written before idle");check(body.revision===settings.revision,"stale manual revision");Object.assign(settings.values,body.values);settings.directions=body.directions;settings.revision++;return json({ok:true,data:{...settings,persisted:true}});}
      if(path==="/perception/selection"){check(!observe&&!drive,"model changed while observing");check(body.revision===policy.revision,"model revision mismatch");policy={...policy,revision:policy.revision+1,verified:false,mode:"observe",perception:{...policy.perception,model_id:body.model_id}};return json({config:policy});}
      if(path==="/goal/s14_venom_navigation"){check(body.enabled===false,"save restarted navigation");drive=false;return json({ok:true,data:{ok:true}});}
      if(path==="/goal/s15_venom_perception"){
        if(body.enabled&&rejectResume)return json({detail:{code:"fixture_analysis_restart_failed"}},503);
        observe=body.enabled;return json({ok:true,meta:{},data:{ok:true}});
      }
      if(path==="/navigation/config"){
        check(!observe&&!drive,"saved before observation stopped");
        if(rejectSave)return json({detail:{code:"navigation_configuration_conflict"}},409);
        check(body.revision===policy.revision,"stale revision");
        check(body.values.mode==="observe"&&body.values.verified===false,"changed drive policy did not revoke drive mode");
        saved.push(body.values);policy={...body.values,motion_limits:{version:1,max_motion_ms:500,max_speed:1,max_turn_ms:500,max_turn_speed:1},protocol_version:1,revision:policy.revision+1};
        return json({ok:true,meta:{},data:{ok:true,persisted:true,config:policy}});
      }
      throw new Error("unexpected mutation: "+path);
    }
    if(path==="/perception/models")return json(modelCatalog());
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"journey-settings-fixture"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"journey-settings-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},health:{},telemetry:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith("/navigation/"))return json(navigation());
    if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,supported:true,revision:1,state:"stopped",transport_epoch:1,active_profile:null,effective_stages:{},error:null,capabilities:{analysis_locations:["venom"],local_engines:["canny"],remote_transports:["snapshots"]},profile:{analysis_location:"venom",local_engine:"canny",remote_transport:"snapshots"}});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  const scenarios=[];
  try{
    await page.setViewportSize({width:1366,height:768});await page.goto(base+"/rider");
    await page.getByTestId("operator-tuning").waitFor();
    check(await page.getByTestId("operator-manual-speed").inputValue()==="0.6","current manual value changed");
    check(await page.getByTestId("operator-manual-duration").inputValue()==="250","current manual pulse changed");
    check(await page.getByTestId("operator-linear_rate").inputValue()==="0.5","ramp unavailable or changed");
    check(await page.getByTestId("settings-save").isDisabled(),"clean form writable");
    await page.getByTestId("workspace-automatic").click();
    check(await page.getByTestId("automatic-speed").isVisible(),"motion hidden behind disclosure");
    check(await page.getByTestId("automatic-pulse").getAttribute("max")==="500","pulse capability lost");
    check(await page.getByTestId("automatic-speed").locator("xpath=ancestor::details").count()===0,"speed remains in nested accordion");
    await page.getByTestId("tuning-tab-model").click();
    await page.getByTestId("tuning-residual_limit").fill("0.065");
    check(!await page.getByTestId("settings-save").isDisabled(),"model draft blocked by active observation");
    await page.getByTestId("tuning-tab-motion").click();
    await page.getByTestId("automatic-speed").fill("0.11");
    check(!await page.getByTestId("settings-save").isDisabled(),"motion draft blocked model draft");
    check((await page.getByTestId("loop-start").innerText()).includes("Zastosuj"),"Start still hides unsaved draft");
    const baseline=calls.length;
    await page.getByRole("tab",{name:"Kalibracja",exact:true}).click();
    await page.getByRole("tab",{name:"Sterowanie",exact:true}).click();
    await wait(async()=>await page.getByTestId("automatic-speed").inputValue()==="0.11","tab switch discarded speed");
    await page.getByTestId("tuning-tab-model").click();
    check(await page.getByTestId("tuning-residual_limit").inputValue()==="0.065","tab switch discarded model draft");
    check(calls.length===baseline,"editing/view switching mutated Pi");
    await page.getByTestId("settings-save").click();
    await wait(()=>policy.revision===54&&observe,"combined settings did not save/resume observation");
    check(policy.speed===.11&&policy.perception.residual_limit===.065,"one shared draft was lost");
    check(calls.slice(baseline).map(x=>x.path).join(",")==="/goal/s15_venom_perception,/navigation/config,/goal/s15_venom_perception","apply requires manual stop or starts movement");
    scenarios.push("flat controls, current values, shared drafts, tab persistence, pause/save/observe");
    await page.getByTestId("tuning-tab-motion").click();
    await page.getByTestId("operator-linear_rate").fill("0.6");
    await page.getByTestId("automatic-speed").fill("0.12");
    const mixed=calls.length;
    await page.getByTestId("settings-save").click();
    await wait(()=>settings.revision===2&&policy.revision===55&&observe,"manual+navigation apply failed");
    check(settings.values.linear_rate===.6,"shared ramp was not persisted");
    check(calls.slice(mixed).map(x=>x.path).join(",")==="/goal/s15_venom_perception,/settings,/navigation/config,/goal/s15_venom_perception","mixed domain ordering wrong");
    scenarios.push("manual ramps and navigation use the same Apply transaction");
    observe=false;drive=true;motionActive=true;
    await page.getByTestId("tuning-tab-model").click();
    await page.getByTestId("tuning-residual_limit").fill("0.07");
    await wait(async()=>(await page.getByTestId("settings-save").innerText()).includes("Zatrzymaj"),"active movement did not expose stop-and-apply");
    const active=calls.length;await page.getByTestId("settings-save").click();
    await wait(()=>policy.revision===56&&observe&&!drive&&!motionActive,"stop-and-apply did not finish");
    check(calls[active].path==="/control"&&calls[active].body.action==="stop","write preceded STOP");
    check(!calls.slice(active).some(x=>x.path==="/goal/s14_venom_navigation"&&x.body.enabled),"apply resumed movement");
    scenarios.push("active motion stops before write; only observation may return");
    await page.getByTestId("perception-model-choice").selectOption("depth-fixture-second");
    await wait(async()=>!await page.getByTestId("settings-save").isDisabled(),"model selection not included in Apply");
    const choice=calls.length;await page.getByTestId("settings-save").click();
    try{await wait(()=>policy.perception.model_id==="depth-fixture-second"&&observe,"shared model selection failed");}catch(error){throw new Error(String(error)+JSON.stringify({id:policy.perception.model_id,observe,calls:calls.slice(-8),error:await page.getByTestId("settings-save-error").textContent().catch(()=>null),modelError:await page.getByTestId("perception-operation-error").textContent().catch(()=>null)}));}
    check(calls.slice(choice).some(x=>x.path==="/perception/selection"),"model selection endpoint not used");
    check(policy.speed===.12,"changing model discarded saved speed");
    scenarios.push("installed model selection uses the shared settings action");
    rejectSave=true;
    await page.getByTestId("tuning-residual_limit").fill("0.08");
    await page.getByTestId("settings-save").click();
    await page.getByTestId("settings-save-error").waitFor();
    check(await page.getByTestId("tuning-residual_limit").inputValue()==="0.08","failed write discarded draft");
    check(!await page.getByTestId("settings-save").isDisabled(),"failure made valid draft inaccessible");
    scenarios.push("failed apply retains editable draft");
    rejectSave=false;
    await page.getByTestId("operator-discard").click();
    check(await page.getByTestId("tuning-residual_limit").inputValue()===String(policy.perception.residual_limit),"Discard did not restore persisted thresholds");
    await page.getByTestId("perception-model-choice").selectOption("depth-anything-v2-indoor-small");
    await wait(async()=>!await page.getByTestId("settings-save").isDisabled(),"model choice not dirty");
    const beforeDiscard=calls.length;await page.getByTestId("operator-discard").click();
    await wait(async()=>await page.getByTestId("settings-save").isDisabled()&&await page.getByTestId("perception-model-choice").inputValue()===policy.perception.model_id,"Discard did not restore selected model");
    check(calls.length===beforeDiscard,"Discard wrote settings");
    scenarios.push("Discard restores thresholds and installed model selection without device writes");
    await page.getByTestId("tuning-tab-motion").click();
    await page.getByTestId("automatic-speed").fill("0");
    await page.getByTestId("operator-linear_rate").fill("0.7");
    const calibrationStart=calls.length,navRevision=policy.revision,manualRevision=settings.revision;
    await page.getByRole("tab",{name:"Kalibracja",exact:true}).click();
    check(await page.getByTestId("calibration-save").isEnabled(),"invalid unrelated navigation draft blocked manual save");
    await page.getByTestId("calibration-save").click();
    await wait(()=>settings.revision===manualRevision+1,"manual-only calibration save failed");
    check(policy.revision===navRevision&&!calls.slice(calibrationStart).some(call=>call.path==='/navigation/config'),"calibration wrote unrelated navigation draft");
    check(settings.values.linear_rate===.7,"calibration did not save its own draft");
    await wait(async()=>await page.getByTestId("calibration-save").isDisabled(),"saved calibration remains dirty");
    const ramp=page.getByRole('spinbutton',{name:/Przyspieszanie jazdy/});
    await ramp.fill('80');await ramp.fill('70');
    check(await page.getByTestId('calibration-save').isDisabled(),"unchanged non-null calibration draft can still be saved");
    await page.getByRole("tab",{name:"Sterowanie",exact:true}).click();
    check(await page.getByTestId("automatic-speed").inputValue()==='0',"manual save discarded unrelated navigation draft");
    await page.getByTestId("operator-discard").click();
    scenarios.push("calibration saves only manual settings despite invalid navigation draft, preserves it and disables unchanged save");
    check(calls.every(x=>x.path!=="/control"||x.body.action==="stop"),"test actuated hardware path");
    await page.getByTestId("tuning-tab-motion").click();
    await page.getByTestId("automatic-speed").scrollIntoViewIfNeeded();
    const camera=await page.getByTestId("workspace-content").boundingBox();
    check(camera&&camera.y>=0&&camera.y<768,"scrolling tuning loses camera");
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"laptop overflow");
    await page.screenshot({path:"test-results/browser-regressions/operator-tuning-laptop.png"});
    await page.setViewportSize({width:390,height:844});
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"mobile overflow");
    return {passed:scenarios,fixtureOnly:true,mutations:calls.map(x=>({path:x.path,action:x.body.action}))};
  }finally{await page.goto("about:blank");await page.unrouteAll({behavior:"wait"});}
}
