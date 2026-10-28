// Every Rider endpoint is intercepted before loading the UI; no real ARM or motor calls.
async page => {
  const base=new URL(page.url()).origin,check=(ok,message)=>{if(!ok)throw new Error(message);};
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const wait=async(fn,message)=>{const end=Date.now()+8000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,40));}};
  const sha="a".repeat(64),calls=[],saved=[];
  let observe=true,drive=false,motionActive=false,rejectSave=false,rejectResume=false,generation="settings-fixture";
  // Same incomplete ground calibration and saved drive mode as the deployed Pi.
  let policy={motion_limits:{version:1,max_motion_ms:500,max_speed:1,max_turn_ms:500,max_turn_speed:1},protocol_version:1,revision:53,mode:"drive",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],verified:true,calibration_id:"operator-fixture",model_sha256:sha,model_tuning_supported:true,perception:{artifact_sha256:sha,engine:"depth_plane",model_id:"depth-anything-v2-indoor-small",residual_limit:.06,outlier_limit:.005,slope_min:2},avoidance:{enabled:false,protocol_version:3,sweep_verified:false,mirrored:false,turn_speed:.05,turn_ms:100,max_turn_pulses:3,max_stalled_corrections:2,progress_min_delta:.03,geometry:{mode:"ground",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45}}};
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
    if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,supported:true,revision:1,state:"active",camera_session:"fixture-camera",transport_epoch:1,active_profile:{analysis_location:"venom",local_engine:"canny",remote_transport:"snapshots"},effective_stages:{},error:null,capabilities:{analysis_locations:["venom"],local_engines:["canny"],remote_transports:["snapshots"]},profile:{analysis_location:"venom",local_engine:"canny",remote_transport:"snapshots"}});
    if(path==="/camera/venom")return route.fulfill({status:200,contentType:"image/png",headers:{
      "X-Rider-Stale":"false","X-Rider-Frame-Age-Ms":"0","X-Rider-Frame-Id":"frame-"+policy.revision,
      "X-Rider-Capture-Key":"revision-"+policy.revision,
      "X-Rider-Analysis-Json":JSON.stringify({run_id:"fixture-run",frame_id:"frame-"+policy.revision,sequence:policy.revision,state:"clear",model_id:policy.perception.model_id,artifact_sha256:sha,config_revision:policy.revision})
    },body:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC","base64")});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  try {
    await page.goto(base+"/rider");
    const image=()=>page.locator('img[data-capture-key="revision-'+policy.revision+'"]');
    await image().waitFor({state:"visible"});
    await page.getByTestId("workspace-automatic").click();
    await page.getByTestId("automatic-speed").fill("0.11");
    check(!await page.getByTestId("settings-save").isDisabled(),"draft not editable");
    policy={...policy,revision:policy.revision+1,speed:.2};
    await wait(()=>image().isVisible(),"fresh applied-model frame rejected while an unsaved draft exists");
    check(await page.getByTestId("automatic-speed").inputValue()==="0.11","preview synchronization overwrote the unsaved draft");
    check(calls.length===0,"read-only preview recovery mutated Pi");
    return {pass:true,revision:policy.revision,draft_preserved:true,robot_contacted:false};
  } finally {
    // Stop polling before removing mocks, then wait for in-flight handlers.
    await page.goto("about:blank");
    await page.unrouteAll({behavior:"wait"});
  }
}
