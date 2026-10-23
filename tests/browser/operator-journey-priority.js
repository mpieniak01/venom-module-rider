// All Rider APIs are mocked before /rider loads, including every motor/ARM route.
async page => {
  const base=page.url()==="about:blank"?"http://localhost:3300":new URL(page.url()).origin,check=(ok,message)=>{if(!ok)throw new Error(message);};
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const imageBase64=await page.evaluate(()=>{const canvas=document.createElement("canvas");canvas.width=64;canvas.height=48;const context=canvas.getContext("2d");context.fillStyle="#395539";context.fillRect(0,0,64,48);return canvas.toDataURL("image/png").split(",")[1];});
  const wait=async(fn,message)=>{const end=Date.now()+8000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,30));}};
  const calls=[],scenarios=[],sha="a".repeat(64),cancelled=new Set(),arrived=[];
  let version=true,online=true,controlStale=false,state="DISARMED",owner=null,holder=null,arming=null,generation="fixture-0",serial=0,operation="idle",seq=0;
  let statusDelay=0,slowReads=0,statusPending=0,frameSequence=0;
  let journey=null,journeyPending=null,journeyDelay=0,moveDelay=false,delayAfterExecute=false,generationRace=false,releaseMove=null;
  const advance=()=>{generation="fixture-"+(++serial);seq=0;};
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(direction=>[direction,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  const policy={revision:1,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],perception:{engine:"depth_plane"},verified:true,calibration_id:"operator-fixture",model_sha256:sha,avoidance:{enabled:true,protocol_version:3,sweep_verified:true,turn_speed:.05,turn_ms:100,max_turn_pulses:3,geometry:{mode:"image"}}};
  const safety=()=>({state,owner,arming_mode:arming,generation,last_seq:seq,operator_intervention_version:version?1:undefined,navigation_journey_version:1,panel_motion_enabled:state==="ARMED",ready:true,can_move:state==="ARMED",stale:controlStale,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder},operator_control:{version:1,state:owner==="navigation"?"active":"paused",operation_id:operation,generation},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:policy,status:{running:true,mode:journey?.active?"drive":"observe",stale:false,run_id:"warm-source",operator_control_version:1,journey,decision:{state:"clear",clear_count:3,vision_action:"go",action:journey?.active?"go":"stop"}},goals:{drive:!!journey?.active,observe:!journey?.active},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,navigation_journey_version:1,supported_protocol_versions:[3]}});
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){
      const body=req.postDataJSON();calls.push({path,body});
      if(path==="/navigation/journey"){
        journeyPending=body.request_id;operation=body.request_id;
        const thisOperation=operation;
        if(journeyDelay)await new Promise(resolve=>setTimeout(resolve,journeyDelay));
        journeyPending=null;
        if(operation!==thisOperation)return json({detail:{code:"navigation_trial_cancelled"}},409);
        state="ARMED";owner="navigation";arming="journey";holder=body.panel_id;advance();
        journey={version:1,id:"journey-"+serial,active:true,state:"forward",reason:"clear",elapsed_s:2,maneuver_epoch:0};
        return json({ok:true,meta:{},data:{ok:true,config:policy}});
      }
      check(path==="/control","unexpected lifecycle mutation "+path);
      if(body.action==="stop"){
        state="DISARMED";owner=null;arming=null;operation=body.request_id;advance();
        if(journey)journey={...journey,active:false,state:"stopped",reason:"operator_stop"};
        return json({ok:true,meta:{},data:{ok:true,confirmed:true}});
      }
      check(body.action==="move","gesture required extra "+body.action);
      if(body.mode==="release"&&body.operator_intervention_version===1){
        check(body.intervene===true&&body.operator_consent===undefined&&!!body.supersedes_operation_id,"invalid cancellation shape");
        cancelled.add(body.supersedes_operation_id);
        if(body.supersedes_navigation_operation_id===operation&&body.generation===generation){
          state="DISARMED";owner=null;arming=null;operation=body.request_id;advance();
          if(journey)journey={...journey,active:false,state:"stopped",reason:"manual_intervention_cancelled"};
        }
        return json({ok:true,meta:{},data:{ok:true,confirmed:true}});
      }
      if(body.intervene===true){
        check(version&&body.operator_intervention_version===1&&body.operator_consent===true&&body.owner==="manual"&&body.arming_mode==="operator"&&!!body.operator_plan,"missing fresh manual consent");
        if(journeyPending)check(body.supersedes_operation_id===journeyPending,"preparing operation not superseded");
        if(moveDelay){
          if(generationRace){state="ARMED";owner="navigation";arming="journey";holder=body.panel_id;operation=body.supersedes_operation_id;advance();}
          await new Promise(resolve=>{releaseMove=resolve;});
        }
        arrived.push(body.request_id);
        if(cancelled.has(body.request_id))return json({detail:{code:"operator_intervention_cancelled"}},409);
        state="ARMED";owner="manual";arming="operator";holder=body.panel_id;operation=body.request_id;advance();
        if(journey)journey={...journey,active:false,state:"stopped",reason:"manual_intervention"};
      }else check(owner==="manual"&&body.generation===generation,"manual renewal lost ownership");
      seq=body.seq;
      const response={ok:true,meta:{},data:{ok:true,confirmed:true,server_time:Date.now()/1000,ack:{request_id:body.request_id,action:"move",source:"manual",accepted:true,status:"executed",command_generation:body.generation,generation},safety:safety()}};
      if(delayAfterExecute&&body.intervene===true)await new Promise(resolve=>{releaseMove=resolve;});
      return json(response);
    }
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"operator-journey-fixture"});
    if(path==="/status"){
      if(statusDelay){statusPending++;await new Promise(resolve=>setTimeout(resolve,statusDelay));slowReads++;statusPending--;}
      return json({connection:{configured:true,status:online?"online":"offline",session:"operator-journey-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},telemetry:{},health:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    }
    if(path.startsWith("/navigation/"))return json(navigation());
    if(path==="/camera/pipeline")return json({supported:false});
    if(path.startsWith("/camera/")){
      const sequence=++frameSequence,source={contract_version:1,camera_session:"priority-camera",transport_epoch:1,pipeline_revision:1,sequence,frame_id:"priority-frame-"+sequence,captured_monotonic_ns:Date.now()*1e6,sha256:"b".repeat(64)};
      const analysis={run_id:"warm-source",frame_id:"decision-"+sequence,sequence,config_revision:policy.revision,state:"clear",vision_action:"go",source};
      return route.fulfill({contentType:"image/png",body:Buffer.from(imageBase64,"base64"),headers:{"X-Rider-Stale":"false","X-Rider-Frame-Age-Ms":"20","X-Rider-Frame-Age-Source":"pi-status-anchor-plus-host-elapsed","X-Rider-Frame-Id":source.frame_id,"X-Rider-Analysis-Json":JSON.stringify(analysis),"X-Rider-Vision-State":"clear","X-Rider-Source":"venom"}});
    }
    return json({});
  });
  const forward=page.getByTestId("rider-forward"),left=page.getByTestId("rider-left");
  const enterJourney=async()=>{await page.getByTestId("workspace-journey").click();await wait(async()=>!await page.getByTestId("journey-start").isDisabled(),"journey Start unavailable");};
  try{
    await page.goto(base+"/rider");await forward.waitFor();
    await wait(async()=>!await forward.isDisabled(),"normal disarmed gesture unavailable");
    check(calls.length===0,"startup implicitly armed or moved");
    await forward.click();await wait(()=>owner==="manual","one disarmed click did not take manual authority");
    check(calls.length===1&&calls[0].body.mode==="pulse"&&calls[0].body.operator_consent===true,"disarmed click queued ARM/poll/pause");
    scenarios.push("normal DISARMED accepts one explicit direction; startup never grants consent");
    const manualPanel=holder;
    state="DISARMED";owner=null;holder=null;arming=null;advance();await enterJourney();
    statusDelay=800;const earlyStopAt=calls.length,readsBefore=slowReads;
    await page.getByTestId("journey-start").click();await wait(()=>statusPending>0,"initial preparation read not delayed");
    await page.getByTestId("rider-pad-stop").click();
    await wait(()=>calls.slice(earlyStopAt).some(c=>c.body.action==="stop"),"pad STOP failed during initial preparation read");
    statusDelay=0;await wait(()=>slowReads>readsBefore,"initial read never completed");await new Promise(r=>setTimeout(r,120));
    check(!calls.slice(earlyStopAt).some(c=>c.path==="/navigation/journey"),"stopped initial read submitted delayed Start");
    check(state==="DISARMED"&&owner===null,"early pad STOP did not preserve DISARMED");
    scenarios.push("pad STOP during the initial control read cancels preparation before a Start request is admitted");
    for(const before of ["DISARMED","manual"]){
      state=before==="manual"?"ARMED":"DISARMED";owner=before==="manual"?"manual":null;
      holder=before==="manual"?manualPanel:null;arming=before==="manual"?"operator":null;advance();
      await enterJourney();journeyDelay=1100;
      const stopAt=calls.length;await page.getByTestId("journey-start").click();
      await wait(()=>!!journeyPending,"pending Start absent for pad STOP test");
      await page.getByTestId("rider-pad-stop").click();
      await wait(()=>calls.slice(stopAt).some(c=>c.path==="/control"&&c.body.action==="stop"),"pad STOP failed to cancel preparing Start from "+before);
      await wait(()=>journeyPending===null,"cancelled Start still pending");
      check(state==="DISARMED"&&owner===null&&!journey?.active,"Start armed after pad STOP from "+before);
      scenarios.push("pad STOP revokes pending Start before navigation ownership appears from "+before);
    }
    state="DISARMED";owner=null;holder=null;arming=null;advance();await enterJourney();journeyDelay=1100;
    const prepareStart=calls.length;await page.getByTestId("journey-start").click();
    await wait(()=>!!journeyPending,"preparation did not start");
    await wait(async()=>!await left.isDisabled(),"preparation disabled intervention");await left.click();
    await wait(()=>owner==="manual","arrow did not supersede preparation");
    await wait(()=>journeyPending===null,"old Start did not finish");
    check(owner==="manual"&&!journey?.active,"late Start reclaimed authority");
    check(calls.slice(prepareStart).map(call=>call.path).join(",")==="/navigation/journey,/control","intervention restarted camera or queued ARM/pause");
    check(await page.getByRole("button",{name:"Wizja Venom",exact:true}).getAttribute("aria-pressed")==="true","manual intervention changed chosen preview");
    scenarios.push("arrow during preparation wins over late Start ACK and preserves selected preview");
    state="DISARMED";owner=null;holder=null;arming=null;advance();await enterJourney();
    moveDelay=true;generationRace=true;await page.getByTestId("journey-start").click();await wait(()=>!!journeyPending,"racing preparation absent");
    await wait(async()=>!await left.isDisabled(),"racing intervention unavailable");await left.click();await wait(()=>!!releaseMove,"racing handoff absent");
    const racingRequest=calls.at(-1).body;
    await new Promise(resolve=>setTimeout(resolve,120));
    check(!cancelled.has(racingRequest.request_id),"own intermediate preparation generation canceled fresh gesture");
    moveDelay=false;generationRace=false;releaseMove();releaseMove=null;
    await wait(()=>owner==="manual","exact preparing generation handoff was not adopted");await wait(()=>journeyPending===null,"racing old Start still pending");
    check(owner==="manual","late racing Start reclaimed authority");
    scenarios.push("own intermediate navigation generation does not cancel the exact pending gesture; foreign generations remain covered by unit guards");
    journeyDelay=0;await enterJourney();await page.getByTestId("journey-start").click();await wait(()=>journey?.active,"journey did not become active");
    await wait(async()=>!await forward.isDisabled(),"active journey disabled arrows");await forward.click();
    await wait(()=>owner==="manual"&&!journey.active,"one click did not end active journey");
    scenarios.push("active journey ends on a single pulse with no separate takeover or ARM");
    state="DISARMED";owner=null;holder=null;arming=null;advance();await enterJourney();await page.getByTestId("journey-start").click();await wait(()=>journey?.active,"next journey unavailable");
    await wait(async()=>!await forward.isDisabled(),"hold intervention unavailable");moveDelay=true;
    const pressForward=async()=>{const box=await forward.boundingBox();check(!!box,"forward arrow not laid out");await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();};
    await pressForward();
    await wait(()=>!!releaseMove,"delayed takeover not captured");const pending=calls.at(-1).body;
    await page.mouse.up();await wait(()=>cancelled.has(pending.request_id),"release queued cancellation behind ACK");
    check(!arrived.includes(pending.request_id),"test did not exercise cancellation before device arrival");
    check(calls.at(-1).body.supersedes_navigation_operation_id===pending.supersedes_operation_id,"release lost original navigation scope");
    moveDelay=false;releaseMove();releaseMove=null;await new Promise(resolve=>setTimeout(resolve,300));
    check(owner===null&&!journey.active,"cancelled delayed takeover resumed old automation");
    scenarios.push("release before original device arrival sends exact navigation scope, ends automation, tombstones delayed manual request and never actuates it");
    await enterJourney();await page.getByTestId("journey-start").click();await wait(()=>journey?.active,"journey after cancellation unavailable");
    await wait(async()=>!await forward.isDisabled(),"next intervention unavailable");
    delayAfterExecute=true;const stopStart=calls.length;
    await pressForward();
    await wait(()=>!!releaseMove&&owner==="manual","executed delayed ACK not captured");
    await page.getByTestId("rider-stop").focus();await page.keyboard.press("Enter");await wait(()=>state==="DISARMED","STOP queued behind takeover ACK");
    delayAfterExecute=false;releaseMove();releaseMove=null;await page.mouse.up();
    await new Promise(resolve=>setTimeout(resolve,250));
    check(state==="DISARMED"&&owner===null,"late executed ACK restored authority after STOP");
    check(calls.slice(stopStart).filter(call=>call.body.mode==="hold").length===1,"late ACK renewed motion after STOP");
    scenarios.push("global STOP runs while an executed manual ACK is delayed; late ACK cannot restore authority or renew");
    await wait(async()=>!await forward.isDisabled(),"DISARMED could not recover after explicit STOP");
    await page.getByTestId("rider-manual-mode").selectOption("continuous");moveDelay=true;
    const blurStart=calls.length;await left.click();await wait(()=>!!releaseMove,"continuous pending intervention absent");
    const continuousRequest=calls.at(-1).body;
    await page.evaluate(()=>window.dispatchEvent(new Event("blur")));
    await wait(()=>cancelled.has(continuousRequest.request_id),"blur failed to cancel pending continuous intervention");
    moveDelay=false;releaseMove();releaseMove=null;await new Promise(resolve=>setTimeout(resolve,200));
    check(calls.slice(blurStart).filter(call=>call.body.mode==="hold").length===1,"blur replayed continuous renewal");
    await page.getByTestId("rider-manual-mode").selectOption("pulse");
    scenarios.push("global blur cancels pending continuous gesture with a scoped independent request and no queued renewal");
    await wait(async()=>await page.getByRole("img",{name:"Klatka kamery Rider"}).isVisible(),"camera image not rendered during jitter test");
    const firstFrame=frameSequence,slowReadsAtStart=slowReads;statusDelay=650;await wait(()=>slowReads>=slowReadsAtStart+2,"slow control reads not exercised");
    check(frameSequence>firstFrame,"camera did not keep refreshing during slow diagnostics");
    check(!await forward.isDisabled(),"diagnostic RTT jitter hid the explicit manual gesture");
    const jitterStart=calls.length;await forward.click();await wait(()=>calls.length>jitterStart,"fresh gesture was queued behind slow polling");
    check(calls.length===jitterStart+1&&calls.at(-1).body.action==="move"&&calls.at(-1).body.operator_consent===true&&calls.at(-1).body.ttl_ms===500,"jitter gesture added a poll/ARM/pause workflow");
    statusDelay=0;
    scenarios.push("camera keeps rendering while two 650 ms diagnostic reads cannot update the clock; one explicit gesture still submits one bounded move without queueing");
    controlStale=true;await wait(async()=>await forward.isDisabled(),"Pi stale input enabled arrows");
    controlStale=false;state="ESTOP";advance();await wait(async()=>await forward.isDisabled(),"ESTOP enabled arrows");
    state="DISARMED";owner=null;arming=null;advance();online=false;await wait(async()=>await forward.isDisabled(),"offline enabled arrows");
    online=true;state="DISARMED";owner=null;arming=null;advance();
    await wait(async()=>!await forward.isDisabled(),"normal connection did not recover");
    version=false;await wait(async()=>await forward.isDisabled(),"unsupported Pi granted fresh DISARMED consent");
    state="ARMED";owner="navigation";arming="journey";advance();
    await wait(async()=>(await page.getByTestId("operator-messages").innerText()).includes("Pi wymaga aktualizacji"),"unsupported journey intervention not explained");
    check(calls.every(call=>!['arm','navigation_pause','navigation_resume'].includes(call.body.action)),"one gesture required extra lifecycle mutation");
    scenarios.push("ESTOP, offline and missing contract remain blocked with explicit compatibility guidance");
    return {fixtureOnly:true,passed:scenarios,mutations:calls.map(call=>({path:call.path,action:call.body.action,mode:call.body.mode,cancel:call.body.mode==='release'&&call.body.operator_intervention_version===1}))};
  }catch(error){throw new Error(error.message+" | "+JSON.stringify({state,owner,generation,journey,journeyPending,calls}));}
  finally{if(releaseMove)releaseMove();await page.goto("about:blank");await page.unrouteAll({behavior:"wait"});}
}
