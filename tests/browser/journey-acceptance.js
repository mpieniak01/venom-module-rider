// Every Rider endpoint is intercepted before loading the UI; no real ARM or motor calls.
async page => {
  const base=new URL(page.url()).origin,check=(ok,message)=>{if(!ok)throw new Error(message);};
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const wait=async(fn,message)=>{const end=Date.now()+8000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,40));}};
  const sha="a".repeat(64),calls=[],saved=[];
  let observe=true,drive=false,rejectSave=false,rejectResume=false;
  // Same incomplete ground calibration and saved drive mode as the deployed Pi.
  let policy={protocol_version:1,revision:55,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],verified:true,calibration_id:"operator-fixture",model_sha256:sha,perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2},avoidance:{enabled:true,protocol_version:3,sweep_verified:false,mirrored:false,turn_speed:.05,turn_ms:100,max_turn_pulses:3,max_stalled_corrections:2,progress_min_delta:.03,geometry:{mode:"image",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45}}};
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(key=>[key,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  const safety=()=>({state:"DISARMED",owner:null,generation:"settings-fixture",navigation_journey_version:1,panel_motion_enabled:true,ready:true,can_move:false,stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder:null},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:policy,status:{run_id:"acceptance-observe",running:observe||drive,stale:false,mode:drive?"drive":"observe",decision:{state:"clear",vision_action:"go",clear_count:3}},goals:{observe,drive},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,navigation_journey_version:1,ground_geometry_supported:true,supported_protocol_versions:[3]}});
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
        check(body.values.verified===true&&body.values.avoidance.sweep_verified===true,"explicit acceptance did not atomically record sweep and zone");
        check(body.values.model_sha256===sha&&body.values.calibration_id.startsWith("operator-"),"acceptance missing model or operator identity");
        saved.push(body.values);policy={...body.values,protocol_version:1,revision:policy.revision+1};
        return json({ok:true,meta:{},data:{ok:true,persisted:true,config:policy}});
      }
      throw new Error("unexpected mutation: "+path);
    }
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"journey-settings-fixture"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"journey-settings-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},health:{},telemetry:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith("/navigation/"))return json(navigation());
    if(path==="/camera/pipeline"){
      const profile={analysis_location:"venom",local_engine:"canny",remote_transport:"mjpeg"};
      return json({contract_id:"rider.camera_pipeline",contract_version:1,revision:34,profile,active_profile:profile,state:"active",transport_epoch:6,camera_session:"acceptance-camera",capabilities:{analysis_locations:["venom","local"],local_engines:["canny"],remote_transports:["snapshots","mjpeg"]},effective_stages:{capture:true,hardware_mjpeg:true},error:null,latest_frame:{camera_session:"acceptance-camera",transport_epoch:6,pipeline_revision:34,captured_monotonic_ns:Date.now()*1e6,frame_age_ms:20}});
    }
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  const scenarios=[];
  try{
    await page.goto(base+"/rider");
    await page.evaluate(()=>{
      const realFetch=window.fetch.bind(window),encoder=new TextEncoder();
      const canvas=document.createElement("canvas");canvas.width=320;canvas.height=240;
      canvas.getContext("2d").fillRect(0,0,320,240);
      const jpeg=Uint8Array.from(atob(canvas.toDataURL("image/jpeg").split(",")[1]),c=>c.charCodeAt(0));
      const fixture={frames:0,paused:false,revision:55,source:"pi-status-anchor-plus-host-elapsed",connections:0};
      window.__journeyAcceptanceFixture=fixture;
      window.fetch=(input,options={})=>{
        const url=typeof input==="string"?input:input instanceof URL?input.href:input.url;
        if(!new URL(url,location.href).pathname.endsWith("/api/v1/rider/camera/stream/venom"))return realFetch(input,options);
        fixture.connections++;
        let timer,closed=false;
        const stream=new ReadableStream({start(controller){
          const stop=()=>{if(closed)return;closed=true;clearInterval(timer);controller.close();};
          options.signal?.addEventListener("abort",stop,{once:true});
          const emit=()=>{
            if(closed||fixture.paused)return;
            const sequence=++fixture.frames;
            const source={contract_version:1,camera_session:"acceptance-camera",transport_epoch:6,pipeline_revision:34,sequence,frame_id:"acceptance-camera-"+sequence,captured_monotonic_ns:Date.now()*1e6,sha256:"b".repeat(64)};
            const analysis={run_id:"acceptance-observe",frame_id:"decision-"+sequence,sequence,config_revision:fixture.revision,state:"clear",vision_action:"go",source};
            const headers={"Content-Type":"image/jpeg","Content-Length":jpeg.length,"X-Rider-Stale":"false","X-Rider-Frame-Id":source.frame_id,"X-Rider-Frame-Age-Ms":"20","X-Rider-Frame-Age-Source":fixture.source,"X-Rider-Source":"venom","X-Rider-Source-Json":JSON.stringify(source),"X-Rider-Analysis-Json":JSON.stringify(analysis),"X-Rider-Vision-State":"clear"};
            const crlf=String.fromCharCode(13,10);
            controller.enqueue(encoder.encode("--acceptance"+crlf+Object.entries(headers).map(([k,v])=>k+": "+v).join(crlf)+crlf+crlf));
            controller.enqueue(jpeg);controller.enqueue(Uint8Array.of(13,10));
          };timer=setInterval(emit,50);emit();
        },cancel(){closed=true;clearInterval(timer);}});
        return Promise.resolve(new Response(stream,{headers:{"Content-Type":"multipart/x-mixed-replace; boundary=acceptance"}}));
      };
    });
    await page.getByTestId("workspace-journey").click();
    const accept=page.getByTestId("trial-accept");
    await wait(async()=>!await accept.isDisabled(),"fresh native MJPEG never enabled acceptance");
    check((await accept.innerText()).includes("skręt"),"acceptance did not explicitly include steering");
    check(await page.getByTestId("journey-start").isDisabled(),"missing sweep enabled journey");
    check(calls.length===0,"fresh preview fabricated operator acceptance");
    scenarios.push("already accepted zone plus missing sweep still displays one explicit joint acceptance button; native frame enables it");
    await page.evaluate(()=>{window.__journeyAcceptanceFixture.revision=54;});
    await wait(async()=>await accept.isDisabled(),"previous configuration frame allowed acceptance");
    check((await page.getByTestId("calibration-preview-block").innerText()).includes("rewizji"),"revision mismatch unexplained");
    await page.evaluate(()=>{window.__journeyAcceptanceFixture.revision=55;window.__journeyAcceptanceFixture.source="host-received";});
    await wait(async()=>(await page.getByTestId("calibration-preview-block").innerText()).includes("świeżą"),"untrusted age source allowed acceptance");
    await page.evaluate(()=>{window.__journeyAcceptanceFixture.source="pi-status-anchor-plus-host-elapsed";});
    await wait(async()=>!await accept.isDisabled(),"fresh native frame did not recover acceptance");
    scenarios.push("wrong revision and host-only age source remain blocked; valid native source recovers");
    await page.evaluate(()=>{window.__journeyAcceptanceFixture.paused=true;});
    await wait(async()=>await accept.isDisabled(),"expired native preview allowed acceptance");
    await page.evaluate(()=>{window.__journeyAcceptanceFixture.paused=false;});
    await wait(async()=>!await accept.isDisabled(),"fresh native preview did not recover");
    scenarios.push("stale preview blocks acceptance without extending display or motion deadlines");
    await accept.click();
    await wait(()=>policy.revision===56,"explicit joint acceptance did not save");
    await wait(async()=>!await page.getByTestId("journey-start").isDisabled(),"saved joint acceptance never enabled journey start");
    check(policy.verified&&policy.avoidance.sweep_verified,"acceptance incomplete");
    check((await page.getByTestId("journey-acceptance-saved").innerText()).includes("56"),"confirmed acceptance revision missing in journey view");
    check(saved.length===1,"duplicate acceptance writes");
    check(calls.map(call=>call.path).join(",")==="/goal/s15_venom_perception,/navigation/config,/goal/s15_venom_perception","unexpected joint acceptance lifecycle");
    check(calls.every(call=>call.path!=="/control"&&call.path!=="/navigation/journey"),"acceptance actuated robot");
    scenarios.push("one explicit operator click saves both acceptances in one Pi write, resumes observation and enables Start without issuing ARM or journey");
    return {passed:scenarios,fixtureOnly:true,revision:policy.revision,savedPolicies:saved,mutations:calls.map(call=>({path:call.path,enabled:call.body.enabled}))};
  }finally{await page.goto("about:blank");await page.unrouteAll({behavior:"wait"});}
}
