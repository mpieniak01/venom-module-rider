// Every Rider request is intercepted. All ARM/STOP acknowledgements are inert fixtures.
async page => {
  const check=(value,message)=>{if(!value)throw new Error(message);};
  const base=new URL(page.url()).origin;
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const calls=[],sha="a".repeat(64);
  let armed=true,owner="manual",holder="previous-panel",generation="fixture-0",serial=0,seq=0,reject=false;
  let sampleMode="fresh",charge=.62,postureHeight=null,balanceEnabled=null;
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(direction=>[direction,{speed:.2,duration:.25}])),parameter_schema:{actuator_fields:{height:{type:"integer",minimum:75,maximum:115,step:1,display_unit:"SDK"},balance:{type:"boolean"}},direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  const policy={revision:1,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95],perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2},verified:false,calibration_id:"",model_sha256:""};
  const safety=()=>({state:armed?"ARMED":"DISARMED",owner,generation,last_seq:seq,panel_motion_enabled:true,can_move:armed,ready:true,stale:false,observed_at:Date.now()/1000,actuators:{height:{value:postureHeight},balance:{enabled:balanceEnabled}},capabilities:{move:{available:true},height:{available:true},balance:{available:true}},panel_control:{version:1,holder},operator_control:{version:1,state:armed?"paused":"idle",operation_id:"old-operation",generation},operator_intervention_version:1,navigation_journey_version:1,remote_decision_deadline_supported:true,scoped_stop_generation_supported:true,output:{lx:0,az:0}});
  const nav=()=>({config:policy,status:{running:false,stale:false,mode:"observe",run_id:"",decision:{}},goals:{drive:false,observe:false},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,navigation_journey_version:1,supported_protocol_versions:[3],clear_frames_required:3}});
  const advance=()=>{generation="fixture-"+(++serial);seq=0;};
  const wait=async(fn,message)=>{const end=Date.now()+7000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,40));}};
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){
      const body=req.postDataJSON();calls.push({path,body});
      check(path==="/control","unexpected mutation "+path);
      if(reject)return json({detail:{code:"panel_owner_mismatch"}},409);
      const old=generation;
      if(["disarm","stop"].includes(body.action)){armed=false;owner=null;holder=null;advance();}
      else if(body.action==="arm"){
        if(armed&&holder!==body.panel_id)return json({detail:{code:"panel_owner_mismatch"}},409);
        armed=true;owner="manual";holder=body.panel_id;advance();
      }else if(body.action==="height"){check(armed&&holder===body.panel_id,"height missing panel authority");postureHeight=body.height;}
      else if(body.action==="balance"){check(armed&&holder===body.panel_id,"balance missing panel authority");balanceEnabled=body.enabled;}
      else throw new Error("unexpected fixture actuator "+body.action);
      return json({ok:true,data:{ok:true,confirmed:true,server_time:Date.now()/1000,safety:safety(),ack:{request_id:body.request_id,action:body.action,source:"manual",seq:body.seq,command_generation:body.generation??old,generation,accepted:true,status:"executed"}}});
    }
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"operator-workspace-fixture"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"operator-workspace-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},telemetry:{server_time:Date.now()/1000,devices:{xgo:{sensor_quality:{sensors:Object.fromEntries([["battery",charge,"ratio"],["roll",2.5,"sdk_raw"],["pitch",-43.8,"sdk_raw"],["yaw",-925.9,"sdk_raw"]].map(([name,value,unit])=>[name,{value:sampleMode==="missing"?null:value,unit,ready:true,stale:sampleMode==="stale",observed_at:Date.now()/1000-(sampleMode==="expired"?20:0),max_age_s:2.5}]))}}}},health:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith("/navigation/"))return json(nav());
    if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,revision:1,profile:{analysis_location:"venom",local_engine:"canny",remote_transport:"mjpeg"},state:"stopped",transport_epoch:1,camera_session:null,capabilities:{analysis_locations:["venom","local"],local_engines:["canny"],remote_transports:["snapshots","mjpeg"]},effective_stages:{},latest_frame:null,error:null});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  const geometry=[];
  try {
    await page.setViewportSize({width:1440,height:1000});await page.goto(base+"/rider");
    await page.getByTestId("operator-status").waitFor();
    await wait(async()=>(await page.getByTestId("operator-battery").innerText()).includes("62"),"fresh battery missing");
    check((await page.getByTestId("operator-axes").innerText()).includes("-925,9"),"SDK axis was converted or wrapped");
    check((await page.getByTestId("operator-axes").innerText()).split("SDK").length===2,"axis unit must be shared once");
    check(await page.getByTestId("operator-height").inputValue()==="","unknown height invented a target");
    check(await page.getByTestId("operator-height-apply").isDisabled(),"unknown target allowed apply");
    check((await page.getByTestId("operator-balance-state").innerText())==="Nieznany","unknown balance shown as off");
    check(await page.getByText("Strzałka przejmuje sterowanie ręczne jednym gestem.",{exact:true}).count()===0,"repeated instruction remains");
    check(await page.getByText("Postawa, pomiary i historia",{exact:true}).count()===0,"duplicate bottom section remains");
    check(!(await page.getByTestId("manual-feedback").innerText()).includes("Zgłoszone ZERO"),"executor detail still in primary controls");
    check(calls.length===0,"page startup changed device");
    for (const mode of ["stale","expired","missing"]) {
      sampleMode=mode;
      await wait(async()=>(await page.getByTestId("operator-battery").innerText())==="—",mode+" sample shown as current battery");
      const text=await page.getByTestId("operator-axes").innerText();
      check(!text.includes("-925,9"),mode+" sample retained current axis");
      sampleMode="fresh";charge=.43;
      await wait(async()=>(await page.getByTestId("operator-battery").innerText()).includes("43"),"fresh recovery missing");
    }
    charge=0;
    await wait(async()=>(await page.getByTestId("operator-battery").innerText()).includes("0 %"),"measured zero hidden");
    check(calls.length===0,"data rendering sent actuator command");
    await page.getByTestId("takeover-manual").click();
    await wait(async()=>!await page.getByTestId("operator-height").isDisabled()&&holder!=="previous-panel","fixture takeover failed");
    const start=calls.length;
    for(const invalid of ["74","116","75.5",""]) {
      await page.getByTestId("operator-height").fill(invalid);
      check(await page.getByTestId("operator-height-apply").isDisabled(),"invalid target allowed: "+invalid);
    }
    await page.getByTestId("operator-height").fill("90");
    check(calls.length===start,"editing height sent command");
    check(!await page.getByTestId("operator-height-apply").isDisabled(),"valid target blocked");
    await page.getByTestId("operator-height-apply").click();
    await wait(()=>postureHeight===90,"explicit height command missing");
    await wait(async()=>!await page.getByTestId("operator-balance").isDisabled(),"balance remained busy");
    await page.getByTestId("operator-balance").click();
    await wait(async()=>(await page.getByTestId("operator-balance-state").innerText())==="Włączony","balance enable not synchronized");
    await wait(async()=>!await page.getByTestId("operator-balance").isDisabled(),"balance remained busy");
    await page.getByTestId("operator-balance").click();
    await wait(async()=>(await page.getByTestId("operator-balance-state").innerText())==="Wyłączony","balance disable not synchronized");
    check(calls.slice(start).map(call=>call.body.action).join(",")==="height,balance,balance","unexpected actuator mutations");
    postureHeight=95;
    await wait(async()=>await page.getByTestId("operator-height").inputValue()==="95","external target not synchronized after apply");
    for(const viewport of [{width:1440,height:1000},{width:1366,height:768},{width:768,height:1024},{width:390,height:844},{width:844,height:390}]) {
      await page.setViewportSize(viewport);await page.getByTestId("rider-page").evaluate(el=>{el.scrollTop=0;});
      const info=await page.getByTestId("operator-status").boundingBox(),pad=await page.getByTestId("manual-pad").locator('[role="group"]').boundingBox();
      check(info.y>=pad.y+pad.height-1,"status overlaps arrow pad");
      const layout=await page.getByTestId("rider-page").evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
      check(layout.scroll<=layout.width+1,"horizontal overflow");
      const targets=await page.getByTestId("operator-status").locator('button,input').evaluateAll(nodes=>nodes.map(node=>{const r=node.getBoundingClientRect();return {height:r.height,width:r.width};}));
      check(targets.every(box=>box.height>=36&&box.width>=36),"unusable posture target");
      geometry.push({viewport,info,pad,targets});
    }
    return {result:"PASS",fixtureOnly:true,quality:["fresh","stale","expired","missing","zero","recovery"],controls:"unknown values, bounded explicit height, balance synchronization, external update",mutations:calls.map(call=>call.body.action),geometry};
  } finally {await page.goto("about:blank");await page.unroute("**/api/v1/rider/**");}
}
