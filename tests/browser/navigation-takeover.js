// Every Rider request is intercepted. No request can reach a physical robot.
async (page) => {
  await page.setViewportSize({width:1600,height:1400});
  const check=(ok,message)=>{if(!ok)throw new Error(message);};
  const wait=async(fn,message)=>{const end=Date.now()+6000;while(!await fn()){if(Date.now()>end)throw new Error(message);await new Promise(r=>setTimeout(r,30));}};
  const run=async(mode)=>{
    await page.goto("about:blank");await page.unroute("**/api/v1/rider/**");
    const sha="a".repeat(64),calls=[];
    let policy={revision:1,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95],perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2},verified:true,calibration_id:"operator-fixture",model_sha256:sha};
    if(mode==="steering"||mode==="ground")policy.avoidance={protocol_version:3,enabled:true,sweep_verified:true,mirrored:false,turn_speed:.05,turn_ms:100,max_turn_pulses:3};
    if(mode==="ground")policy.avoidance={...policy.avoidance,max_stalled_corrections:2,progress_min_delta:.03,geometry:{mode:"ground",fx:.5,fy:.5,cx:.5,cy:.5,radius_ratio:.35,lookahead_ratio:1.5,max_heading_deg:45}};
    let observing=mode!=="standalone",driving=false,driveRequested=false,armed=false,owner=null,generation="fixture-initial";
    let previewReads=0,previewBusyReplies=0,previewBusyReads=0;
    let obstacle=false,mediaFailure=false,starting=false,cancelled=false,terminal=null,armCount=0,cameraOn=true;
    const detector={configured:true,loaded:true,sha256:sha,purpose:"relative_ground_geometry",drive_supported:true,clear_frames_required:3,supported_protocol_versions:[1,2,3],ground_geometry_supported:true};
 const settings={"schema_version": 2, "revision": 0, "values": {"linear_speed": 0.2, "turn_speed": 0.35, "pulse_duration": 0.25, "speed_limit": 1.0, "linear_rate": 3.0, "turn_rate": 4.0, "linear_deceleration": 3.0, "turn_deceleration": 4.0, "reversal_pause": 0.0, "ramp_mode": 0}, "directions": {"forward": {"minimum": 0.0, "maximum": 1.0, "step": 0.01, "speed": 0.2, "duration": 0.25}, "backward": {"minimum": 0.0, "maximum": 1.0, "step": 0.01, "speed": 0.2, "duration": 0.25}, "left": {"minimum": 0.0, "maximum": 1.0, "step": 0.01, "speed": 0.35, "duration": 0.25}, "right": {"minimum": 0.0, "maximum": 1.0, "step": 0.01, "speed": 0.35, "duration": 0.25}}, "profiles": {}, "active_profile": null, "contract_id": "rider.manual_settings", "contract_version": 2, "limits": {"linear_speed": 1.0, "turn_speed": 1.0, "pulse_duration": 1.0, "speed_limit": 1.0, "linear_rate": 100.0, "turn_rate": 100.0, "linear_deceleration": 100.0, "turn_deceleration": 100.0, "reversal_pause": 0.25, "ramp_mode": 2}, "actuation": false, "units": "normalized_command", "legacy_units_scope": "command_magnitude_only; use parameter_schema for individual fields", "parameter_schema": {"version": 1, "actuator_fields": {"height": {"type": "integer", "unit": "sdk_height_command", "display_unit": "SDK", "display_factor": 1, "display_precision": 0, "minimum": 75, "maximum": 115, "step": 1, "meaning": "height_command_not_physical_measurement"}, "balance": {"type": "boolean", "unit": "enabled", "display_unit": "", "meaning": "roll_compensation_not_full_balance_shutdown"}}, "ramp_modes": [{"value": 0, "id": "linear", "label_pl": "Liniowy", "label_en": "Linear"}, {"value": 1, "id": "smooth_s", "label_pl": "\u0141agodny S", "label_en": "Smooth S"}, {"value": 2, "id": "logarithmic", "label_pl": "Logarytmiczny", "label_en": "Logarithmic"}], "direction_fields": {"speed": {"type": "number", "unit": "normalized_command", "display_unit": "%", "display_factor": 100, "minimum": 0, "maximum": 1, "display_precision": 1, "meaning": "command_magnitude_not_physical_speed"}, "minimum": {"type": "number", "unit": "normalized_command", "display_unit": "%", "display_factor": 100, "minimum": 0, "maximum": 1, "display_precision": 1, "meaning": "operator_defined_minimum_nonzero_command"}, "maximum": {"type": "number", "unit": "normalized_command", "display_unit": "%", "display_factor": 100, "minimum": 0, "maximum": 1, "display_precision": 1, "meaning": "profile_command_ceiling"}, "step": {"type": "number", "unit": "normalized_command", "display_unit": "%", "display_factor": 100, "minimum": 0, "maximum": 1, "display_precision": 3, "meaning": "manual_adjustment_step", "exclusive_minimum": true}, "duration": {"type": "number", "unit": "s", "display_unit": "ms", "display_factor": 1000, "minimum": 0.01, "maximum": 1.0, "display_precision": 0, "meaning": "whole_click_pulse"}}, "value_fields": {"linear_rate": {"type": "number", "unit": "normalized_command/s", "display_unit": "%/s", "display_factor": 100, "minimum": 0.001, "maximum": 100.0, "display_precision": 1, "meaning": "linear_rate"}, "turn_rate": {"type": "number", "unit": "normalized_command/s", "display_unit": "%/s", "display_factor": 100, "minimum": 0.001, "maximum": 100.0, "display_precision": 1, "meaning": "turn_rate"}, "linear_deceleration": {"type": "number", "unit": "normalized_command/s", "display_unit": "%/s", "display_factor": 100, "minimum": 0.001, "maximum": 100.0, "display_precision": 1, "meaning": "linear_deceleration"}, "turn_deceleration": {"type": "number", "unit": "normalized_command/s", "display_unit": "%/s", "display_factor": 100, "minimum": 0.001, "maximum": 100.0, "display_precision": 1, "meaning": "turn_deceleration"}, "reversal_pause": {"type": "number", "unit": "s", "display_unit": "ms", "display_factor": 1000, "minimum": 0, "maximum": 0.25, "display_precision": 0, "meaning": "pause_before_reversal"}}, "constraints": [{"id": "direction_range", "rule": "minimum <= maximum"}, {"id": "direction_speed", "rule": "speed == 0 or minimum <= speed <= maximum"}, {"id": "direction_step", "rule": "step > 0; zero-width range permits a disabled slider"}, {"id": "pulse_minimum", "rule": "minimum <= duration / (1/acceleration + 1/deceleration)"}]}, "control_modes": {"version": 1, "supported": ["pulse", "hold", "release"], "default": "pulse", "directions": ["forward", "backward", "left", "right"], "hold_renewal_ms": 150, "hold_timeout_ms": 500, "click_threshold_ms": 180, "request_ttl_ms": 500, "replacement": "latest_valid_sequence_wins", "release": "decelerate_keep_armed", "release_timeout_ms": 500, "stop": "immediate_disarm", "hold_permission": "operator_manual_panel", "settings_change": "idle_only", "command": {"endpoint": "/api/control", "action": "move", "fields": ["direction", "mode", "device_settings", "settings_revision"]}}, "authority": "device", "profile_modified": false, "legacy_aliases": {"linear_speed": "directions.forward.speed", "turn_speed": "directions.right.speed", "pulse_duration": "maximum_direction_duration"}};
    const state=()=>{
      const run_id=driving?"fixture-drive":"fixture-observe";
      const decision={run_id,frame_id:"fixture-capture",sequence:4,config_revision:policy.revision,state:obstacle?"obstacle":"clear",clear_count:obstacle?0:4,vision_action:obstacle?"stop":"go",action:driving&&!obstacle?"go":"stop",reason:obstacle?"depth_ground_deviation":"depth_plane_candidate",motion_reason:obstacle?"depth_ground_deviation":driving?"clear_frames_ready":"observe_only",perception_engine:"depth_plane",valid_for_ms:150};
      if(mode==="steering"&&driving)Object.assign(decision,{protocol_version:3,action:"steer_right",state:"obstacle",motion_reason:"avoidance_steer",sectors:{right:{state:"clear",coverage_complete:true}},paths:{right:{state:"clear",coverage_complete:true},guard:{state:"clear",coverage_complete:true}}});
      const status=mode==="standalone"?{running:false,stale:true,reason:"navigation_status_unavailable",decision:null}:terminal??{running:observing||driving,stale:false,latched:false,paused:driving&&obstacle,mode:driving?"drive":"observe",run_id,config_revision:policy.revision,decision,permit_remaining_ms:150};
      return {goals:{observe:observing,drive:driveRequested},status:{...status,last_trial:terminal},detector,config:policy};
    };
    await page.route("**/api/v1/rider/**",async route=>{
      const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
      const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
      if(req.method()!=="GET"){
        const body=req.postDataJSON();calls.push({path,body});
        if(path==="/navigation/config"){
          check(!observing&&!driving&&!driveRequested,"configuration saved while a loop was active");
          check(body.revision===policy.revision,"configuration revision mismatch");
          policy={...policy,...body.values,revision:policy.revision+1};
          return json({ok:true,data:{ok:true,persisted:true,config:policy}});
        }
        if(path==="/navigation/trial"){
          check(body.generation===generation&&body.config_revision===policy.revision,"trial identity mismatch");
          check(body.operator_consent===true&&body.operator_plan&&body.request_id&&body.source==="manual"&&body.execution_context==="panel","operator intent missing");
          check(!("seq" in body)&&Number.isInteger(body.ttl_ms)&&Number.isFinite(body.ts),"trial admission is not the endpoint contract");
          starting=true;
          if(mode==="cancel")await new Promise(r=>setTimeout(r,800));
          if(cancelled)return json({detail:{code:"navigation_trial_cancelled"}},409);
          cameraOn=true;observing=false;driving=true;driveRequested=true;armed=true;owner="navigation";generation="fixture-navigation";armCount++;policy={...policy,mode:"drive",revision:policy.revision+1};
          return json({ok:true,data:{ok:true,config:policy,status:state().status,authority:{generation}}});
        }
        if(path==="/control"){
          check(body.action==="stop","browser must not ARM or move directly");
          cancelled=true;armed=false;owner=null;driving=false;generation="fixture-stop";
          return json({ok:true,data:{ok:true}});
        }
        if(path==="/goal/s2_camera_preview"){check(mode==="standalone"&&body.enabled===true,"unexpected camera goal");cameraOn=true;return json({ok:true,data:{ok:true}});}
        if(path==="/goal/s14_venom_navigation"){driveRequested=body.enabled;driving=body.enabled;return json({ok:true,data:{ok:true}});}
        if(path==="/goal/s15_venom_perception"){observing=body.enabled;if(observing)cameraOn=true;return json({ok:true,data:{ok:true}});}
        if(path==="/resource/camera"){
          check(body.action==="stop"&&!observing&&!driveRequested,"camera released before loop producers stopped");
          cameraOn=false;
          return json({camera:{ok:true},feed:{ok:true}});
        }
        await route.abort();throw new Error("unexpected mutation "+path);
      }
      if(path==="/config")return json({devices:[{id:"fixture-demo",label:"Fixture",mode:"demo"}],device_id:"fixture-demo",session:"fixture-session"});
      if(path==="/navigation/config")return json({...state(),config:policy,runtime:{observe:true,drive:true,known:true}});
      if(path==="/navigation/state")return json(state());
      if(path==="/status")return json({connection:{status:"online",configured:true,device_id:"fixture-demo",mode:"demo",session:"fixture-session"},polling:{control_ms:200},errors:{},settings,control:{server_time:Date.now()/1000,safety:{state:armed?"ARMED":"DISARMED",owner,generation,observed_at:Date.now()/1000,stale:false,can_move:armed,moving:driving&&!obstacle,ready:true,capabilities:{move:{available:true}},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true}},telemetry:{camera:{on:cameraOn}},health:{camera:{on:cameraOn}},catalog:{features:[{name:"s14_venom_navigation",can_move:true,control_owner:"navigation"},{name:"s15_venom_perception",can_move:false}]},logic:{},resources:{},face:{}});
      if(path.startsWith("/camera/")){
        if(!cameraOn)return json({detail:{code:"camera_off"}},404);
        if(mediaFailure)return route.abort("failed");
        if(previewBusyReplies>0){previewBusyReplies--;previewBusyReads++;return json({detail:{code:"vision_busy"}},429);}
        previewReads++;
        const analysis=mode==="standalone"?{run_id:"standalone-fixture",frame_id:"host-frame-"+previewReads,sequence:previewReads,config_revision:policy.revision,state:"clear",reason:"depth_plane_candidate",vision_action:"stop",action:"stop",age_source:"host-received"}:state().status.decision;
        return route.fulfill({body:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN2kAAAAASUVORK5CYII=","base64"),contentType:"image/png",headers:{"X-Rider-Stale":"false","X-Rider-Frame-Id":analysis.frame_id,"X-Rider-File-Observed-At":String(Date.now()/1000),"X-Rider-Vision-State":analysis.state,"X-Rider-Vision-Reason":analysis.reason,"X-Rider-Vision-Policy":"device_policy","X-Rider-Source":"fixture","X-Rider-Frame-Age-Ms":"50","X-Rider-Frame-Age-Source":mode==="standalone"?"host-received":"pi-reported-plus-host-elapsed","X-Rider-Analysis-Json":JSON.stringify(analysis)}});
      }
      return json({});
    });
    await page.goto("http://localhost:3300/rider");await page.bringToFront();
    await page.getByTestId("loop-start").waitFor();
    if(mode==="standalone"){
      // Production incident: Pi camera is ON outside Venom; no navigation
      // producer runs. The host returns a fresh diagnostic preview with STOP.
      await page.getByRole("button",{name:"Wizja Venom",exact:true}).click();
      await wait(()=>previewReads>=3,"standalone preview did not keep fetching frames");
      await wait(async()=>await page.locator("img").evaluateAll(images=>images.some(image=>image.complete&&image.naturalWidth>0)),"preview did not decode");
      check(await page.getByTestId("vision-preview-paused").count()===0,"fresh standalone preview incorrectly labelled stopped");
      const beforeBusy=await page.locator("img").first().getAttribute("src");
      const busyResponse=page.waitForResponse(response=>response.url().includes("/camera/venom")&&response.status()===429);
      previewBusyReplies=1;
      await busyResponse;
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      check(await page.getByTestId("vision-preview-paused").count()===0,"transient busy falsely expired a still-current preview");
      check(await page.locator("img").first().getAttribute("src")===beforeBusy,"busy reply replaced or redated the original image");
      previewBusyReplies=6;
      await wait(()=>previewBusyReads>=3,"long busy sequence was not exercised");
      await wait(async()=>await page.getByTestId("vision-preview-paused").count()===1,"repeated busy replies renewed an expired frame");
      previewBusyReplies=0;
      await wait(async()=>await page.getByTestId("vision-preview-paused").count()===0,"new image did not recover after busy expiry");
      mediaFailure=true;
      await wait(async()=>await page.getByTestId("vision-preview-paused").count()===1,"lost preview retained a live label");
      mediaFailure=false;
      await wait(async()=>await page.getByTestId("vision-preview-paused").count()===0,"fresh recovery retained stale banner");
      check(!observing&&!driving&&!driveRequested&&!armed&&armCount===0,"preview activated a navigation or motion owner");
      check(!calls.some(c=>c.path==="/control"||c.path==="/navigation/trial"),"preview sent an actuator request");
      return {mode,result:"PASS",previewReads,sourceAge:"host-received",navigationRunning:false,actuatorPosts:0,previewBusyReads};
    }
    await wait(async()=>!await page.getByTestId("loop-start").isDisabled(),"start button unavailable");
    if(mode==="ground"){
      await page.getByTestId("loop-stop").click();
      await wait(()=>!observing&&!driveRequested&&!cameraOn,"ground edit STOP incomplete");
      await wait(async()=>!await page.getByTestId("loop-stop").isDisabled(),"ground edit still busy");
      const details=page.locator("details").filter({has:page.getByTestId("ground-geometry-settings")});
      await details.locator("summary").click();
      const fields=page.getByTestId("ground-geometry-settings").locator("input");
      check(await fields.count()===7,"projection/footprint controls missing");
      check(await details.getByRole("textbox",{name:"Strefa zatrzymania: x0,y0,x1,y1 (0–1)",exact:true}).count()===0,"unused image ROI shown in ground mode");
      await fields.nth(0).fill("");
      await details.getByRole("button").filter({hasText:"Zapisz"}).click();
      await wait(()=>policy.avoidance.geometry.fx===null,"empty focal was not persisted as uncalibrated");
      check(await page.getByTestId("loop-start").isDisabled(),"uncalibrated ground enabled trial");
      check(!policy.verified&&!policy.avoidance.sweep_verified,"geometry edit reused acceptance");
      await fields.nth(0).fill("0.55");
      await fields.nth(4).fill("0.4");
      await fields.nth(5).fill("1.6");
      await details.getByRole("button").filter({hasText:"Zapisz"}).click();
      await wait(()=>policy.avoidance.geometry.fx===.55&&policy.avoidance.geometry.radius_ratio===.4&&policy.avoidance.geometry.lookahead_ratio===1.6,"projection edits not persisted");
      check(armCount===0&&!armed&&!driving,"ground calibration edit actuated");
      return {mode,result:"PASS",geometry:policy.avoidance.geometry};
    }
    if(mode==="editor"){
      const original=JSON.stringify(policy.corridor),revision=policy.revision;
      await page.getByTestId("zone-edit").click();
      const overlay=page.getByTestId("corridor-editor");
      await overlay.waitFor();
      await overlay.scrollIntoViewIfNeeded();
      const box=await overlay.locator('rect[data-handle="move"]').boundingBox();
      check(box&&box.width>0&&box.height>0,"editor image coordinates unavailable");
      await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
      await page.mouse.down();
      await page.mouse.move(box.x+box.width/2+20,box.y+box.height/2-10,{steps:5});
      await page.mouse.up();
      check(JSON.stringify(policy.corridor)===original,"draft mutated Pi before Save");
      await page.getByTestId("zone-save").click();
      await wait(()=>policy.revision>revision&&observing,"editor save did not resume analysis");
      check(JSON.stringify(policy.corridor)!==original,"drag was not persisted");
      check(armCount===0&&!armed&&!driving,"editor save actuated");
      return {mode,result:"PASS",revision:policy.revision};
    }
    if(mode==="remote"){
      // Another panel changes the Pi-owned policy; this card must follow it.
      policy={...policy,revision:policy.revision+1,corridor:[.35,.8,.65,.95],verified:false,calibration_id:"",model_sha256:""};
      await wait(async()=>await page.getByTestId("loop-zone").inputValue()==="near","remote zone revision not synchronized");
      check(await page.getByTestId("loop-start").isDisabled(),"old receipt still enables start after remote change");
      check(!armed&&armCount===0,"remote configuration armed");
      return {mode,result:"PASS"};
    }
    if(mode==="zone"){
      await page.getByTestId("loop-zone").selectOption("near");
      await wait(()=>policy.corridor[1]===.8&&observing,"near zone not saved and observation not resumed");
      check(JSON.stringify(policy.corridor)===JSON.stringify([.35,.8,.65,.95]),"near zone changed path width or nearest edge");
      check(!policy.verified&&!armed&&armCount===0,"zone change reused receipt or armed");
      check(policy.speed===.1&&policy.max_motion_ms===150,"zone change increased motion settings");
      await wait(async()=>!await page.getByTestId("trial-accept").isDisabled(),"fresh near-zone acceptance unavailable");
      await page.getByTestId("trial-accept").click();
      await wait(()=>policy.verified&&observing,"near-zone receipt not saved");
      await wait(async()=>!await page.getByTestId("loop-start").isDisabled(),"accepted near-zone trial unavailable");
    }
    await page.getByTestId("loop-start").click();
    if(mode==="cancel"){
      await wait(()=>starting,"trial did not reach endpoint");
      await page.getByTestId("loop-stop").click();
      await wait(()=>cancelled,"STOP not sent during start");
      await new Promise(r=>setTimeout(r,1000));
      check(armCount===0&&!armed&&!driving,"cancelled start armed the robot");
      await wait(()=>!observing&&!driveRequested&&!cameraOn,"cancelled start left camera or loop active");
      await wait(async()=>!await page.getByTestId("loop-stop").isDisabled(),"cancelled STOP did not finish");
      return {mode,result:"PASS",trialRequests:calls.filter(c=>c.path==="/navigation/trial").length};
    }
    await wait(()=>driving&&armed,"single endpoint did not start trial");
    check(armCount===1&&calls.filter(c=>c.path==="/navigation/trial").length===1,"duplicate start/ARM");
    check(await page.getByTestId("loop-zone").isDisabled(),"zone editable during active driving");
    if(mode==="steering"){
      await wait(async()=>await page.getByTestId("pi-reply").textContent()==="KOREKTA W PRAWO","v3 curve shown as STOP or missing");
      const old=state().status;driving=false;armed=false;owner=null;generation="fixture-exhausted";
      terminal={...old,running:false,latched:true,paused:false,stop_reason:"avoidance_no_progress",observed_at:Date.now()/1000};
      await page.getByTestId("trial-stop-reason").waitFor();
      await wait(async()=>(await page.getByTestId("trial-stop-reason").textContent()).includes("Korekty nie otworzyły przejazdu"),"no-progress STOP reason missing");
      await wait(()=>observing&&!driveRequested,"terminal no-progress kept driving");
      check(armCount===1&&!armed,"no-progress automatically rearmed");
      return {mode,result:"PASS"};
    }
    if(mode==="terminal"){
      const old=state().status;driving=false;armed=false;owner=null;generation="fixture-fault";
      terminal={...old,running:false,latched:true,paused:false,stop_reason:"decision_timeout",observed_at:Date.now()/1000};
      await page.getByTestId("trial-stop-reason").waitFor();
      await wait(()=>observing&&!driveRequested,"terminal fault did not recover observation");
      check(armCount===1,"fault recovery rearmed");
      return {mode,result:"PASS"};
    }
    obstacle=true;
    await page.getByTestId("trial-visual-pause").waitFor();
    check(armed&&armCount===1&&driveRequested,"visual pause ended trial or rearmed");
    obstacle=false;
    await wait(async()=>!await page.getByTestId("trial-visual-pause").count(),"fresh clear did not resume UI");
    check(armCount===1,"resumption rearmed");
    mediaFailure=true;await new Promise(r=>setTimeout(r,1200));
    check(await page.getByText("Połączono",{exact:true}).isVisible(),"media failure marked robot offline");
    mediaFailure=false;
    await page.getByTestId("loop-stop").click();
    await wait(()=>!driving&&!armed&&!driveRequested&&!observing&&!cameraOn,"operator stop did not release camera and producers");
    await wait(async()=>!await page.getByTestId("loop-stop").isDisabled(),"operator STOP did not finish");
    check(await page.locator("img").count()===0,"stopped preview still mounted");
    check(calls.filter(c=>c.path==="/resource/camera").length===1,"camera STOP missing or duplicated");
    check(!calls.some(c=>c.path==="/control"&&c.body.action!=="stop"),"browser issued actuators directly");
    return {mode,result:"PASS",trialRequests:calls.filter(c=>c.path==="/navigation/trial").length,armCount};
  };
  return {result:"PASS",scenarios:[await run("standalone"),await run("ground"),await run("editor"),await run("remote"),await run("zone"),await run("positive"),await run("steering"),await run("cancel"),await run("terminal")]};
}
