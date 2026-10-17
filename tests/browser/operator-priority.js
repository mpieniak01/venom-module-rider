// Every request is fulfilled by this fixture, including all motion acknowledgements.
async page => {
  const base = new URL(page.url()).origin;
  const check = (ok,message) => { if (!ok) throw new Error(message); };
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const wait = async (fn,message) => {const end=Date.now()+9000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,35));}};
  const calls=[], sha="a".repeat(64);
  let modelReady=false,armed=false,owner=null,holder=null,generation="fixture-0",serial=0,seq=0,operator="idle",producer=false;
  let resumePending=false,lateIntervention=false,releaseLate=null,resumeId=null,resumeOldGeneration=null,operatorId="fixture",pauseDelay=350;
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(direction=>[direction,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  const policy={revision:1,mode:"drive",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95],perception:{engine:"depth_plane"},verified:true,calibration_id:"operator-fixture",model_sha256:sha};
  const safety=()=>({state:armed?"ARMED":"DISARMED",owner,generation,last_seq:seq,panel_motion_enabled:true,can_move:armed,ready:true,stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder},operator_control:{version:1,state:operator,operation_id:operatorId,generation},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:policy,status:{running:producer,stale:false,mode:"drive",run_id:"fixture-persistent",operator_control_version:1,decision:{state:"clear",clear_count:3,action:operator==="active"?"go":"stop",vision_action:"go",reason:"clear_frames_ready"}},goals:{drive:producer,observe:false},runtime:{drive:true,observe:true,known:true},detector:{configured:modelReady,loaded:modelReady,sha256:sha,drive_supported:true,clear_frames_required:3}});
  const advance=()=>{generation="fixture-"+(++serial);seq=0;};
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){
      const body=req.postDataJSON();calls.push({path,body});
      if(path==="/navigation/trial"){
        check(body.operator_control_version===1&&body.panel_id===holder,"trial opt-in missing");
        producer=true;owner="navigation";operator="active";operatorId=body.request_id;advance();
        return json({ok:true,meta:{},data:{ok:true,config:policy}});
      }
      check(path==="/control","normal control changed service/camera lifecycle: "+path);
      const old=generation;
      operatorId=body.request_id;
      if(body.action==="arm"){armed=true;owner="manual";holder=body.panel_id;advance();}
      else if(body.action==="stop"){armed=false;owner=null;operator="idle";advance();}
      else {
        const supersedes = resumePending && body.supersedes_operation_id === resumeId && body.generation === resumeOldGeneration && (body.intervene === true || body.action === "navigation_pause");
        check(body.panel_id===holder&&(body.generation===generation||supersedes),"control identity mismatch "+JSON.stringify({body,holder,generation,owner,operator}));
        if(body.action==="navigation_resume"){
          check(armed&&!body.operator_consent&&!body.operator_plan,"warm resume renewed consent");
          resumeId=body.request_id;resumeOldGeneration=body.generation;
          owner="navigation";operator="qualifying";advance();resumePending=true;
          const resumeGeneration=generation;
          await new Promise(resolve=>setTimeout(resolve,750));resumePending=false;
          if(generation!==resumeGeneration)return json({detail:{code:"navigation_trial_cancelled"}},409);
          operator="active";
        }else if(body.action==="navigation_pause") {owner="manual";operator="paused";advance();}
        else if(body.action==="move"){
          if(owner==="navigation"){check(body.intervene===true,"navigation move lacks explicit intervention");owner="manual";operator="paused";advance();}
          else check(body.intervene!==true,"manual renewal reused takeover");
          seq=body.seq;
        }else throw new Error("unexpected action "+body.action);
      }
      const response={ok:true,confirmed:true,server_time:Date.now()/1000,ack:{request_id:body.request_id,action:body.action,source:"manual",seq:body.seq,command_generation:body.generation??old,generation,accepted:true,status:"executed"},safety:safety()};
      if (body.action === "navigation_pause" && pauseDelay) await new Promise(resolve=>setTimeout(resolve,pauseDelay));
      if(lateIntervention&&body.action==="navigation_pause"){lateIntervention=false;await new Promise(resolve=>{releaseLate=resolve;});}
      return json({ok:true,meta:{},data:response});
    }
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"priority-fixture"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"priority-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},telemetry:{},health:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith("/navigation/"))return json(navigation());
    if(path==="/camera/pipeline")return json({supported:false});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  const forward=page.getByTestId("rider-forward"), backward=page.getByTestId("rider-backward");
  try{
    await page.goto(base+"/rider");
    await forward.waitFor();check(calls.length===0,"startup caused mutation");
    await wait(async()=>!await page.getByTestId("rider-arm").isDisabled(),"ARM unavailable");
    // Explicit manual consent also works before any model or navigation session.
    await page.getByTestId("takeover-manual").click();
    await wait(async()=>!await forward.isDisabled(),"manual permission unavailable");
    modelReady=true;
    await page.getByTestId("workspace-automatic").click();
    await wait(async()=>!await page.getByTestId("loop-start").isDisabled(),"trial unavailable");
    await page.getByTestId("loop-start").click();
    await wait(()=>owner==="navigation","trial did not transfer authority");
    await wait(async()=>!await forward.isDisabled(),"automatic owner blocked manual arrows");
    const start=calls.length;
    const box=await forward.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
    await wait(()=>calls.slice(start).filter(call=>call.body.action==="move").length>=3,"hold did not renew across own generation handoff");
    await page.mouse.up();
    await wait(()=>calls.slice(start).some(call=>call.body.mode==="release"),"hold release missing");
    const moves=calls.slice(start).filter(call=>call.body.action==="move");
    check(calls[start].body.action === "navigation_pause", "pointerdown did not immediately pause automation");
    check(moves.every(call=>!call.body.intervene),"confirmed takeover repeated intervention");
    check(operator==="paused"&&producer,"correction stopped perception or resumed automation");
    owner="navigation";operator="active";operatorId="short-click-op";advance();
    await wait(async()=>(await page.getByTestId("loop-state").innerText()).includes("Automat steruje"),"short click phase not observed");
    const shortStart=calls.length;
    await forward.click();
    await wait(()=>calls.slice(shortStart).some(call=>call.body.mode==="pulse"),"short click before 350 ms ACK was lost");
    check(calls.slice(shortStart).filter(call=>call.body.mode==="pulse").length===1,"short click replayed");
    pauseDelay=0;
    await wait(async()=>!await page.getByTestId("loop-start").isDisabled(),"warm resume unavailable");
    await page.getByTestId("loop-start").click();
    await wait(()=>resumePending,"warm resume not pending");
    await wait(async()=>!await backward.isDisabled(),"pending resume blocked intervention");
    await backward.click();
    await wait(()=>operator==="paused","correction during resume did not pause");
    await wait(()=>!resumePending,"delayed resume not completed");
    check(owner==="manual"&&operator==="paused","late resume reclaimed control");
    // Stop while an old intervention response remains in flight.
    owner="navigation";operator="active";operatorId="late-pause-op";advance();
    await wait(async()=>(await page.getByTestId("loop-state").innerText()).includes("Automat steruje"),"second automatic observation unavailable");
    await wait(async()=>!await forward.isDisabled(),"second automatic phase unavailable");
    lateIntervention=true;
    await forward.click();
    await wait(()=>!!releaseLate,"delayed ACK not captured");
    await page.getByTestId("rider-stop").click();
    await wait(()=>!armed,"global STOP delayed by movement request");
    releaseLate();
    await wait(async()=>await forward.isDisabled(),"late ACK restored manual permission after STOP");
    await new Promise(resolve=>setTimeout(resolve,300));
    check(!armed&&owner===null,"late ACK restored authority");
    check(calls.filter(call=>call.body.action==="arm").length===1,"warm path re-armed robot");
    return {scenarios:["automatic arrows available", "hold survives confirmed takeover", "resume interruption wins", "late ACK cannot undo STOP", "persistent producer without lifecycle"],calls};
  }catch(error){throw new Error(error.message+" | "+JSON.stringify({owner,operator,generation,calls}));}finally{if(releaseLate)releaseLate();await page.unroute("**/api/v1/rider/**");}
}
