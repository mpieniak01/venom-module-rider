// Every Rider request is intercepted. All ARM/STOP acknowledgements are inert fixtures.
async page => {
  const check=(value,message)=>{if(!value)throw new Error(message);};
  const base=new URL(page.url()).origin;
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const calls=[],sha="a".repeat(64);
  let armed=true,owner="manual",holder="previous-panel",generation="fixture-0",serial=0,seq=0,reject=false;
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(direction=>[direction,{speed:.2,duration:.25}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  const policy={revision:1,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95],perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2},verified:false,calibration_id:"",model_sha256:""};
  const safety=()=>({state:armed?"ARMED":"DISARMED",owner,generation,last_seq:seq,panel_motion_enabled:true,can_move:armed,ready:true,stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder},operator_control:{version:1,state:armed?"paused":"idle",operation_id:"old-operation",generation},operator_intervention_version:1,navigation_journey_version:1,remote_decision_deadline_supported:true,scoped_stop_generation_supported:true,output:{lx:0,az:0}});
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
      }else throw new Error("unexpected fixture actuator "+body.action);
      return json({ok:true,data:{ok:true,confirmed:true,server_time:Date.now()/1000,safety:safety(),ack:{request_id:body.request_id,action:body.action,source:"manual",seq:body.seq,command_generation:body.generation??old,generation,accepted:true,status:"executed"}}});
    }
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"operator-workspace-fixture"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"operator-workspace-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},telemetry:{},health:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith("/navigation/"))return json(nav());
    if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,revision:1,profile:{analysis_location:"venom",local_engine:"canny",remote_transport:"mjpeg"},state:"stopped",transport_epoch:1,camera_session:null,capabilities:{analysis_locations:["venom","local"],local_engines:["canny"],remote_transports:["snapshots","mjpeg"]},effective_stages:{},latest_frame:null,error:null});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  const results=[],targets=["rider-forward","rider-left","rider-pad-stop","rider-right","rider-backward"];
  const scrollTop=()=>page.getByTestId("rider-page").evaluate(el=>{el.scrollTop=0;});
  const bounds=async()=>Object.fromEntries(await Promise.all(targets.map(async id=>[id,await page.getByTestId(id).boundingBox()])));
  try{
    for(const viewport of [{width:1440,height:1000},{width:1366,height:768},{width:1280,height:720},{width:1024,height:768},{width:768,height:1024},{width:390,height:844},{width:844,height:390}]){
      await page.setViewportSize(viewport);await page.goto(base+"/rider");
      await page.getByTestId("rider-forward").waitFor();
      await wait(async()=>!await page.getByTestId("takeover-manual").isDisabled(),"permission read missing");
      await scrollTop();
      const initial=await bounds(),camera=await page.getByTestId("camera-surface").boundingBox();
      const cameraNode=await page.getByTestId("camera-surface").elementHandle();
      check(camera.width>=180&&camera.height>=200,"unreadable camera "+JSON.stringify({viewport,camera}));
      for(const id of targets)check(initial[id].height>=44&&initial[id].width>=44,"small control "+id);
      for(const mode of ["automatic","journey","manual"]){
        await page.getByTestId("workspace-"+mode).click();await scrollTop();
        check(await cameraNode.evaluate(el=>el===document.querySelector('[data-testid="camera-surface"]')),"camera remounted on mode change");
        const after=await bounds(),image=await page.getByTestId("camera-surface").boundingBox();
        for(const key of ["x","y","width","height"])check(Math.abs(camera[key]-image[key])<=1,"mode moved camera "+viewport.width+" "+mode+" "+key);
        for(const id of targets)for(const key of ["x","y","width","height"])check(Math.abs(initial[id][key]-after[id][key])<=1,"mode moved manual target "+id);
      }
      const hierarchy=await page.getByTestId("workspace-secondary").evaluate(el=>{
        const surface=document.querySelector('[data-testid="driving-surface"]').getBoundingClientRect();
        return [...el.children].map(panel=>({below:panel.getBoundingClientRect().top>=surface.bottom-1,position:getComputedStyle(panel).position,open:panel.open??false}));
      });
      check(hierarchy.every(row=>row.below&&!row.open&&row.position!=="absolute"&&row.position!=="fixed"),"settings cover primary controls");
      // Opening settings in normal flow cannot resize or reflow the camera/keypad.
      await page.getByTestId("workspace-secondary").evaluate(el=>{for(const panel of el.children)if(panel instanceof HTMLDetailsElement)panel.open=true;});
      await scrollTop();
      const opened=await bounds(),cameraOpen=await page.getByTestId("camera-surface").boundingBox();
      check(JSON.stringify(initial)===JSON.stringify(opened),"settings moved keypad");
      check(JSON.stringify(camera)===JSON.stringify(cameraOpen),"settings shrank camera");
      const horizontal=await page.getByTestId("rider-page").evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
      check(horizontal.scroll<=horizontal.width+1,"horizontal overflow "+JSON.stringify({viewport,horizontal}));
      const scrollbars=await page.getByTestId("driving-surface").evaluate(el=>[...el.querySelectorAll("*")].filter(node=>node.scrollHeight>node.clientHeight+2&&["scroll","auto"].includes(getComputedStyle(node).overflowY)).map(node=>node.className));
      check(scrollbars.length===0,"nested primary scrollbars "+scrollbars);
      results.push({viewport,camera,control:initial["rider-forward"],below:hierarchy.length});
    }
    check(calls.length===0,"layout or mode switches mutated robot");
    await page.setViewportSize({width:1366,height:900});await page.goto(base+"/rider");await page.getByTestId("workspace-automatic").click();
    await wait(async()=>await page.getByTestId("prepare-navigation").isVisible(),"calibration has no actionable entry point");
    await page.getByTestId("prepare-navigation").click();
    check(await page.getByTestId("navigation-preparation").getAttribute("open")!==null,"preparation did not open");
    check(await page.getByTestId("trial-accept").isVisible(),"acceptance hidden");
    check(await page.getByTestId("trial-accept").isDisabled(),"missing current frame allowed acceptance");
    check(await page.getByTestId("loop-start").isDisabled(),"unaccepted policy allowed driving");
    check(calls.length===0,"opening preparation started motion or camera");
    await page.getByTestId("workspace-manual").click();await scrollTop();
    await page.getByTestId("takeover-manual").click();
    await wait(()=>holder!=="previous-panel"&&owner==="manual"&&armed,"explicit takeover did not establish current panel");
    check(calls.map(call=>call.body.action).join(",")==="disarm,arm","foreign panel sent a holder-only pause or renewed old authority");
    const ownCalls=calls.length;await page.getByTestId("takeover-manual").click();
    check(calls.length===ownCalls,"already-owned paused session performed redundant handoff");
    holder="other-panel";advance();
    await new Promise(resolve=>setTimeout(resolve,650));
    const beforeStop=calls.length;await page.getByTestId("rider-pad-stop").click();await wait(()=>!armed,"foreign-panel STOP failed");
    check(calls[beforeStop].body.action==="stop","foreign-panel STOP sent rejected pause/release");
    armed=true;owner="manual";holder="previous-panel";advance();reject=true;
    await new Promise(resolve=>setTimeout(resolve,650));await scrollTop();
    const beforeError=await bounds();await page.getByTestId("takeover-manual").click();
    await wait(async()=>(await page.getByTestId("operator-messages").innerText()).includes("innej sesji"),"owner error is raw/unactionable");
    const afterError=await bounds();
    check(JSON.stringify(beforeError)===JSON.stringify(afterError),"error moved steering targets");
    const messages=await page.getByTestId("operator-messages").evaluate(el=>({position:getComputedStyle(el).position,scroll:el.scrollHeight,height:el.clientHeight}));
    check(!["absolute","fixed"].includes(messages.position),"operator message floats over controls");
    return {result:"PASS",viewports:results,mutations:calls.map(call=>call.body.action),preparation:"explicit preview/acceptance; no automatic action",ownership:"foreign takeover and STOP; current holder idempotent",errors:"stable inline message",fixtureOnly:true};
  } finally {await page.goto("about:blank");await page.unroute("**/api/v1/rider/**");}
}
