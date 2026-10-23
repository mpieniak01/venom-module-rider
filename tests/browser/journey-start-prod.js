// All Rider endpoints are intercepted before navigation. This test never reaches a physical Pi.
// Run against /rider for device/Prod or /rider?fixtureMode=demo for the same Demo contract.
async page => {
  const base = page.url() === "about:blank" ? "http://localhost:3300" : new URL(page.url()).origin;
  const fixtureMode = new URL(page.url()==="about:blank"?base:page.url()).searchParams.get("fixtureMode")==="demo"?"demo":"device";
  const check = (value, message) => { if (!value) throw new Error(message); };
  check(["localhost", "127.0.0.1", "[::1]"].includes(new URL(base).hostname), "loopback UI required");
  const wait = async (fn, message) => { const end=Date.now()+10000; while (!await fn()) { check(Date.now()<end,message); await new Promise(resolve=>setTimeout(resolve,40)); } };
  const sha="a".repeat(64), calls=[], saves=[], scenarios=[], violations=[];
  // Exactly the writable top-level keys from services.navigation_config.DEFAULT, minus protocol_version and revision (update_from_body allowlist).
  const writable = ["behavior","mode","speed","decision_budget_ms","max_motion_ms","corridor","perception","avoidance","verified","calibration_id","model_sha256"].sort();
  const profiles = ["legacy","cautious","balanced","fluid"].map(id=>({id,profile_revision:1,status:id==="legacy"?"legacy":"experimental",parameters:{}}));
  let policy={protocol_version:1,revision:134,behavior:{version:1,profile_id:"balanced",profile_revision:1},
    mode:"observe",speed:.1,decision_budget_ms:300,max_motion_ms:150,corridor:[.35,.8,.65,.95],
    perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2},
    avoidance:{protocol_version:3,enabled:true,sweep_verified:false,mirrored:false,turn_speed:.05,turn_ms:100,max_turn_pulses:3,max_stalled_corrections:2,progress_min_delta:.03,
      geometry:{mode:"image",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45}},
    verified:false,calibration_id:"",model_sha256:""};
  const configView=()=>({...structuredClone(policy),
    behavior_catalog:{version:1,supported:true,active:policy.behavior,profiles,effective_policy_sha256:"c".repeat(64)},
    model_tuning_supported:true,perception_tuning:{version:1,active_model_id:"depth-anything-v2-indoor-small",effective:{}}});
  let observe=false,drive=false,running=false,stale=true,cameraState="stopped",journey=null,rejectSave=true,rejectedWrites=0,delaySave=false,savePending=false,rejectObservation=false;
  let state="DISARMED",owner=null,holder=null,generation="prod-0",operation="idle",serial=0,journeyPending=false,delayJourney=false;
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(key=>[key,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  const safety=()=>({state,owner,generation,arming_mode:state==="ARMED"?"journey":null,last_seq:0,operator_intervention_version:1,navigation_journey_version:1,panel_motion_enabled:state==="ARMED",ready:true,can_move:state==="ARMED",stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder},operator_control:{version:1,state:drive?"active":"paused",operation_id:operation,generation},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:configView(),status:{run_id:"prod-observe",running,stale,mode:drive?"drive":observe?"observe":"idle",operator_control_version:1,journey,decision:{state:"clear",vision_action:"go",action:drive?"go":"stop",clear_count:3}},
    goals:{observe,drive},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,navigation_journey_version:1,ground_geometry_supported:true,supported_protocol_versions:[3],temporal_navigation_v1:true,semantic_model_policy_v1:true}});
  const profile={analysis_location:"venom",local_engine:"canny",remote_transport:"mjpeg"};
  await page.goto("about:blank");
  await page.addInitScript(()=>{
    const realFetch=window.fetch.bind(window),encoder=new TextEncoder();
    window.__journeyStartProd={frames:0,paused:true,revision:134,run:"prod-observe",source:"pi-status-anchor-plus-host-elapsed"};
    window.fetch=(input,options={})=>{
      const url=typeof input==="string"?input:input instanceof URL?input.href:input.url;
      if(!new URL(url,location.href).pathname.endsWith("/api/v1/rider/camera/stream/venom"))return realFetch(input,options);
      const canvas=document.createElement("canvas");canvas.width=320;canvas.height=240;canvas.getContext("2d").fillRect(0,0,320,240);
      const jpeg=Uint8Array.from(atob(canvas.toDataURL("image/jpeg").split(",")[1]),char=>char.charCodeAt(0));
      let timer,closed=false;
      const stream=new ReadableStream({start(controller){
        const stop=()=>{if(closed)return;closed=true;clearInterval(timer);controller.close();};
        options.signal?.addEventListener("abort",stop,{once:true});
        const emit=()=>{const fixture=window.__journeyStartProd;if(closed||fixture.paused)return;
          const sequence=++fixture.frames,source={contract_version:1,camera_session:"prod-camera",transport_epoch:6,pipeline_revision:34,sequence,frame_id:"prod-camera-"+sequence,captured_monotonic_ns:Date.now()*1e6,sha256:"b".repeat(64)};
          const analysis={run_id:fixture.run,frame_id:"decision-"+sequence,sequence,config_revision:fixture.revision,state:"clear",vision_action:"go",source};
          const headers={"Content-Type":"image/jpeg","Content-Length":jpeg.length,"X-Rider-Stale":"false","X-Rider-Frame-Id":source.frame_id,"X-Rider-Frame-Age-Ms":"20","X-Rider-Frame-Age-Source":fixture.source,"X-Rider-Source":"venom","X-Rider-Source-Json":JSON.stringify(source),"X-Rider-Analysis-Json":JSON.stringify(analysis),"X-Rider-Vision-State":"clear"};
          const crlf=String.fromCharCode(13,10);
          controller.enqueue(encoder.encode("--startup"+crlf+Object.entries(headers).map(([key,value])=>key+": "+value).join(crlf)+crlf+crlf));controller.enqueue(jpeg);controller.enqueue(Uint8Array.of(13,10));
        };timer=setInterval(emit,70);emit();
      },cancel(){closed=true;clearInterval(timer);}});
      return Promise.resolve(new Response(stream,{headers:{"Content-Type":"multipart/x-mixed-replace; boundary=startup"}}));
    };
  });
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){
      const body=req.postDataJSON();calls.push({path,body});
      if(path==="/goal/s15_venom_perception"){
        check(typeof body.enabled==="boolean","observation intent invalid");
        if(body.enabled&&rejectObservation)return json({detail:{code:"fixture_observer_unavailable"}},503);
        observe=body.enabled;running=observe||drive;stale=!running;if(observe)cameraState="active";
        return json({ok:true,meta:{},data:{ok:true}});
      }
      if(path==="/navigation/config"){
        const keys=Object.keys(body.values??{}).sort();
        if(JSON.stringify(keys)!==JSON.stringify(writable)){
          violations.push(keys);return json({detail:{code:"navigation_configuration_invalid"}},422);
        }
        check(!observe&&!drive,"configuration write while loops active");
        check(body.revision===policy.revision,"configuration revision stale");
        check(body.values.verified===true&&body.values.avoidance.sweep_verified===true,"joint acceptance incomplete");
        check(body.values.model_sha256===sha&&body.values.calibration_id.startsWith("operator-"),"acceptance identity missing");
        if(rejectSave){rejectedWrites++;return json({detail:{code:"navigation_configuration_conflict"}},409);}
        savePending=true;
        if(delaySave)await new Promise(resolve=>setTimeout(resolve,1100));
        savePending=false;
        saves.push(structuredClone(body.values));policy={...body.values,revision:policy.revision+1};
        await page.evaluate(revision=>{window.__journeyStartProd.revision=revision;},policy.revision);
        return json({ok:true,meta:{},data:{ok:true,persisted:true,config:configView()}});
      }
      if(path==="/navigation/journey"){
        check(body.operator_consent===true&&body.operator_control_version===1&&body.journey_version===1,"explicit journey consent contract missing");
        check(body.config_revision===policy.revision&&body.generation===generation&&!!body.panel_id&&!!body.request_id&&!!body.operator_plan,"journey identity contract invalid");
        check(!("action" in body)&&!("seq" in body),"journey request contains control-only keys");
        const pending=body.request_id;operation=pending;journeyPending=true;
        if(delayJourney)await new Promise(resolve=>setTimeout(resolve,1100));
        journeyPending=false;
        if(operation!==pending)return json({detail:{code:"navigation_trial_cancelled"}},409);
        state="ARMED";owner="navigation";holder=body.panel_id;generation="prod-"+(++serial);drive=true;observe=true;running=true;stale=false;
        journey={version:1,id:"journey-"+serial,active:true,state:"forward",reason:"clear",elapsed_s:0,maneuver_epoch:0};
        return json({ok:true,meta:{},data:{ok:true,config:configView()}});
      }
      if(path==="/control"){
        check(body.action==="stop","unexpected control actuation "+body.action);
        operation=body.request_id;state="DISARMED";owner=null;holder=null;generation="prod-"+(++serial);drive=false;
        if(journey)journey={...journey,active:false,state:"stopped",reason:"operator_stop"};
        return json({ok:true,meta:{},data:{ok:true,confirmed:true}});
      }
      violations.push(path);return json({detail:{code:"unexpected_fixture_mutation"}},400);
    }
    if(path==="/config")return json({devices:[{id:"fixture-prod",label:"Prod fixture",mode:fixtureMode}],device_id:"fixture-prod",session:"journey-start-prod"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"journey-start-prod",device_id:"fixture-prod",mode:fixtureMode},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},health:{},telemetry:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path==="/navigation/config"||path==="/navigation/state")return json(navigation());
    // No broad /navigation/* response: a misspelt endpoint is a real fixture failure.
    if(path.startsWith("/navigation/")){violations.push("GET "+path);return json({detail:{code:"unexpected_fixture_navigation_endpoint"}},404);}
    if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,revision:34,profile,active_profile:cameraState==="active"?profile:null,state:cameraState,transport_epoch:6,camera_session:"prod-camera",capabilities:{analysis_locations:["venom","local"],local_engines:["canny"],remote_transports:["snapshots","mjpeg"]},effective_stages:{capture:cameraState==="active",hardware_mjpeg:cameraState==="active"},error:null,latest_frame:cameraState==="active"?{camera_session:"prod-camera",transport_epoch:6,pipeline_revision:34,captured_monotonic_ns:Date.now()*1e6,frame_age_ms:20}:null});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  try{
    policy.max_motion_ms=200;
    await page.goto(base+"/rider",{waitUntil:"domcontentloaded"});
    await page.getByTestId("workspace-journey").click();
    await wait(async()=>(await page.getByTestId("command-reason").innerText()).includes("150 ms"),"acceptance guidance hides the actual motion-limit block");
    check(await page.getByTestId("journey-start").isDisabled(),"invalid motion limits enabled Start");
    check(calls.length===0,"displaying a hard block changed device state");
    scenarios.push("motion-limit block takes precedence over acceptance guidance");
    policy.max_motion_ms=150;
    await page.reload({waitUntil:"domcontentloaded"});await page.getByTestId("workspace-journey").click();
    const start=page.getByTestId("journey-start"),accept=page.getByTestId("trial-accept");
    await wait(async()=>await page.getByTestId("prepare-navigation").isVisible(),"cold Prod does not expose preparation");
    check(await start.isDisabled(),"historical GO without acceptance enabled Start");
    check(calls.length===0,"opening the journey view mutated stopped Prod");
    await page.getByTestId("navigation-preparation").locator("summary").first().click();
    await wait(async()=>await accept.isVisible(),"preparation does not reveal explicit acceptance");
    check(await accept.isDisabled(),"stopped diagnostic GO enabled acceptance");
    check(calls.length===0,"opening preparation mutated robot state");
    scenarios.push("cold Prod revision134 metadata + historical GO remains read-only and exposes preparation");
    await page.getByTestId("prepare-navigation").click();
    await wait(()=>observe&&running,"explicit observation did not start");
    check(calls.every(call=>call.path==="/goal/s15_venom_perception"),"preparation issued motion or configuration");
    check(await accept.isDisabled(),"no fresh video frames still enabled acceptance");
    await page.evaluate(()=>{window.__journeyStartProd.paused=false;window.__journeyStartProd.revision=133;});
    await wait(async()=>await page.evaluate(()=>window.__journeyStartProd.frames)>=3,"fixture stream did not publish frames");
    check(await accept.isDisabled(),"previous revision frames enabled acceptance");
    await page.evaluate(()=>{window.__journeyStartProd.revision=134;window.__journeyStartProd.run="old-run";});
    await new Promise(resolve=>setTimeout(resolve,300));
    check(await accept.isDisabled(),"different run frames enabled acceptance");
    await page.evaluate(()=>{window.__journeyStartProd.run="prod-observe";});
    await wait(async()=>!await accept.isDisabled(),"fresh same-run frames never enable acceptance");
    check(!await start.isDisabled(),"fresh clear image cannot reach the primary confirm-and-start action");
    check((await start.innerText()).includes("Potwierdź"),"primary Start does not disclose image/steering confirmation");
    check(saves.length===0&&calls.every(call=>call.path!=="/control"&&call.path!=="/navigation/journey"),"observation actuated or fabricated acceptance");
    scenarios.push("only explicit observation starts camera; wrong revision/run and missing frames cannot grant acceptance");
    for(const viewport of [{width:1440,height:1000},{width:1366,height:768},{width:1280,height:720},{width:1024,height:768},{width:768,height:1024},{width:390,height:844},{width:844,height:390}]){
      await page.setViewportSize(viewport);
      await page.getByTestId("rider-page").evaluate(el=>{el.scrollTop=0;});
      const primary=await start.boundingBox(),stop=await page.getByTestId("journey-stop").boundingBox();
      check(primary&&stop&&primary.x+primary.width<=stop.x+1,"confirm/start overlaps Stop at "+viewport.width);
      const overflow=await page.getByTestId("rider-page").evaluate(el=>el.scrollWidth>el.clientWidth+1);
      check(!overflow,"confirm/start horizontally overflows at "+viewport.width);
    }
    await page.setViewportSize({width:1366,height:900});

    await start.click();
    await wait(()=>rejectedWrites===1,"fixture did not exercise rejected configuration write");
    await wait(()=>observe&&running&&!stale,"failed acceptance left observation stopped");
    check(policy.revision===134&&policy.verified===false&&policy.avoidance.sweep_verified===false,"failed acceptance changed persisted qualification");
    check(saves.length===0&&calls.every(call=>call.path!=="/control"&&call.path!=="/navigation/journey"),"failed acceptance armed or started motion");
    await wait(async()=>!await accept.isDisabled(),"fresh observation did not recover confirmation after rejected save");
    check(!await start.isDisabled(),"rejected acceptance stranded the primary retry action");
    scenarios.push("rejected acceptance keeps unverified revision134 and resumes fresh observation without ARM or journey");
    rejectObservation=true;
    await start.click();
    await wait(()=>rejectedWrites===2,"second save rejection was not exercised");
    await wait(async()=>(await page.getByTestId("command-reason").innerText()).includes("navigation_configuration_conflict"),"observer recovery failure replaced the original save failure");
    await wait(async()=>(await page.locator("body").innerText()).includes("fixture_observer_unavailable"),"observer recovery failure is hidden");
    check(!observe&&!drive&&saves.length===0,"failed recovery fabricated acceptance or motion");
    scenarios.push("save and observer-recovery failures remain separately visible without admission");
    rejectObservation=false;
    await page.getByTestId("prepare-navigation").click();
    await wait(()=>observe&&running&&!stale,"explicit observation did not recover after a rejected restart");
    await wait(async()=>!await start.isDisabled(),"fresh recovery did not permit another explicit Start");
    rejectSave=false;
    const beforeConfirmStart=calls.length;
    await start.click();
    await wait(()=>policy.revision===135,"strict Pi writer rejected acceptance; echoed read-only metadata: "+JSON.stringify(violations));
    await wait(()=>journey?.active===true,"primary confirm-and-start click did not reach journey endpoint");
    check(saves.length===1,"acceptance must save once");
    check(policy.verified&&policy.avoidance.sweep_verified&&policy.behavior.profile_id==="balanced","acceptance changed profile or omitted qualification");
    const transaction=calls.slice(beforeConfirmStart).map(call=>call.path);
    check(transaction.indexOf("/navigation/config")<transaction.indexOf("/navigation/journey"),"journey preceded successful acceptance");
    check(!calls.slice(beforeConfirmStart).some(call=>call.path==="/goal/s15_venom_perception"&&call.body.enabled),"confirm-and-start unnecessarily restarted observation before journey preparation");
    scenarios.push("one explicit primary click saves both qualifications with Pi writable keys then starts revision135");
    check(calls.filter(call=>call.path==="/navigation/journey").length===1,"Start submitted duplicate journey");
    await page.getByTestId("rider-pad-stop").click();
    await wait(()=>state==="DISARMED"&&!journey?.active,"STOP did not end journey");
    await wait(async()=>!await start.isDisabled(),"stopped journey does not allow explicit restart");
    scenarios.push("explicit Start alone posts the scoped journey contract; STOP ends it");
    delayJourney=true;
    await start.click();
    await wait(()=>journeyPending,"delayed Start not admitted");
    await page.getByTestId("rider-pad-stop").click();
    await wait(()=>!journeyPending,"cancelled Start did not settle");
    check(state==="DISARMED"&&!journey?.active&&!drive,"late Start reclaimed motion after STOP");
    check(calls.filter(call=>call.path==="/navigation/journey").length===2,"STOP triggered an extra restart");
    check(violations.length===0,"unexpected contract or endpoint: "+JSON.stringify(violations));
    scenarios.push("STOP supersedes a pending Start; late completion cannot arm or resume motion");
    policy={...policy,revision:136,mode:"observe",verified:false,calibration_id:"",model_sha256:"",avoidance:{...policy.avoidance,sweep_verified:false}};
    observe=true;drive=false;running=true;stale=false;
    await page.evaluate(()=>{window.__journeyStartProd.revision=136;});
    await wait(async()=>!await start.isDisabled()&&(await start.innerText()).includes("Potwierdź"),"revised policy does not expose primary confirmation");
    delaySave=true;
    await start.click();
    await wait(()=>savePending,"confirmation write not pending");
    const beforeStopDuringSave=calls.length;
    await page.getByTestId("rider-pad-stop").click();
    await wait(()=>!savePending,"late confirmation write never settled");
    await new Promise(resolve=>setTimeout(resolve,150));
    check(calls.filter(call=>call.path==="/navigation/journey").length===2,"late confirmation started a journey after STOP");
    check(state==="DISARMED"&&!journey?.active&&!drive,"confirmation completion reclaimed authority after STOP");
    check(!calls.slice(beforeStopDuringSave).some(call=>call.path==="/goal/s15_venom_perception"&&call.body.enabled),"late confirmation restarted observation after STOP");
    scenarios.push("STOP during acceptance write prevents journey admission and observation restart after its late completion");
    const endedCalls=calls.length;
    journey={...journey,active:false,state:"failed",reason:"decision_timeout"};
    await wait(async()=>(await page.getByTestId("command-reason").innerText()).includes("Brak aktualnej poprawnej analizy"),"ended journey hides its actual analysis-timeout reason");
    check(calls.length===endedCalls,"displaying a failed journey retried motion automatically");
    scenarios.push("analysis timeout appears by the main controls without automatically retrying or hiding in diagnostics");
    const settingsSave=page.getByTestId("settings-save");
    await settingsSave.evaluate(button=>{let node=button.parentElement;while(node){if(node.tagName==="DETAILS")node.open=true;node=node.parentElement;}});
    const saveRowHeight=await settingsSave.evaluate(button=>button.parentElement.getBoundingClientRect().height);
    check(saveRowHeight>0&&saveRowHeight<80,"settings actions inherited tall cockpit mode-row height: "+saveRowHeight);
    check(violations.length===0,"opening settings introduced a mutation");
    scenarios.push("settings action row stays compact below 80px rather than inheriting cockpit toolbar height");
    return {fixtureOnly:true,fixtureMode,passed:scenarios,revision:policy.revision,saveRowHeight,mutationPaths:calls.map(call=>call.path),strictWritableKeys:writable};
  } finally {
    await page.goto("about:blank");
    await page.unrouteAll({behavior:"wait"});
  }
}
