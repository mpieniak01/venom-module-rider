// All Rider traffic is mocked before mount. No physical device or socket is used.
async page=>{
 const check=(v,m)=>{if(!v)throw new Error(m);};
 const origin=new URL(page.url()).origin;await page.goto("about:blank");
 let selected="prod",session="offline-session",generation="g0",serial=0,owner=null,holder=null,operation=null,output={lx:0,az:0},x=0,height=95,balance=false,collection=null;
 let stopBarrier=null,figureBarrier=null,escapeArrived=null;
 const calls=[],devices=[{id:"demo",mode:"demo",label:"Demo"},{id:"prod",mode:"device",label:"Prod"}];
 const config=()=>({devices,device_id:selected,session});
 const settings={revision:45,values:{linear_rate:3,linear_deceleration:4,turn_rate:4,turn_deceleration:4,ramp_mode:0,reversal_pause:0},
 directions:Object.fromEntries(["forward","backward","left","right"].map(d=>[d,{speed:.5,duration:.25,minimum:0,maximum:1,step:.01}])),
 parameter_schema:{actuator_fields:{height:{type:"integer",minimum:75,maximum:115,step:1,display_unit:"SDK"},balance:{type:"boolean"}},direction_fields:{speed:{display_factor:100,display_unit:"%"}}},
 control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
 const safety=()=>({state:owner?"ARMED":"DISARMED",owner,generation,last_seq:0,ready:true,stale:false,observed_at:Date.now()/1000,operator_intervention_version:1,operator_height_intervention_version:1,operator_posture_intervention_version:1,
 figures:{version:1},panel_control:{version:1,holder},operator_control:{version:1,state:"paused",operation_id:operation,generation},capabilities:{move:{available:true},height:{available:true},balance:{available:true}},
 actuators:{height:{value:height},balance:{enabled:balance}},output,simulated:true,simulation:{x,y:0,heading:0}});
 await page.route("**/api/v1/rider/**",async route=>{
  const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length),reply=value=>route.fulfill({contentType:"application/json",body:JSON.stringify(value)});
  if(req.method()!=="GET"){
   const body=req.postDataJSON();calls.push({path,body,selected});
   if(path==="/config"){check(body.device_id==="demo","unexpected selection");selected="demo";session="demo-session";return reply(config());}
   check(selected==="demo","actuator reached offline physical target");
   if(path==="/control"){
    if(body.action==="stop"&&stopBarrier){const barrier=stopBarrier;stopBarrier=null;barrier.entered();await barrier.wait;}
    else if(body.action==="stop"&&escapeArrived){escapeArrived();escapeArrived=null;}
    const old=generation;generation="g"+(++serial);
    if(body.action==="stop"){owner=null;holder=null;output={lx:0,az:0};}
    else {check(body.operator_consent===true&&body.operator_intervention_version===1,"gesture requires complete consent");owner="manual";holder=body.panel_id;operation=body.request_id;
     if(body.action==="move"){x+=.1;output={lx:.5,az:0};}
     else if(body.action==="height"){height=body.height;output={lx:0,az:0};}
     else if(body.action==="balance"){balance=body.enabled;output={lx:0,az:0};}
     else throw new Error("unexpected control action");
    }
    return reply({ok:true,confirmed:true,server_time:Date.now()/1000,safety:safety(),ack:{request_id:body.request_id,action:body.action,source:"manual",status:"executed",accepted:true,command_generation:old,generation}});
   }
   if(path==="/motion/figures/runs"){
    if(figureBarrier){const barrier=figureBarrier;figureBarrier=null;barrier.entered();await barrier.wait;}
    return reply({ok:true,confirmed:true});
   }
   if(path==="/samples/sessions"){collection={session_id:"a".repeat(32),state:"collecting",source_device_id:"demo",policy:body.policy};return reply(collection);}
   if(path.endsWith("/end")){collection={...collection,state:"completed"};return reply(collection);}
   throw new Error("Unexpected fixture mutation "+path);
  }
  if(path==="/motion/figures")return reply({version:1,implementation:"own_sequence",figures:[{id:"sway",version:1,implementation:"own_sequence",qualification:"manual_trial",physical_completion:"unverified",duration_ms:4000,available:true,maximum_speed:.25,intensity_factor:.3}]});
  if(path==="/config")return reply(config());
  if(path==="/status")return reply({connection:{configured:true,status:selected==="demo"?"online":"offline",device_id:selected,mode:selected==="demo"?"demo":"device",session},polling:{control_ms:100},control:{server_time:Date.now()/1000,safety:safety()},settings,errors:selected==="demo"?{}:{control:"pi_unreachable"},health:{},telemetry:{},resources:{},catalog:{features:[]},logic:{},face:{}});
  if(path.startsWith("/samples/sessions/"))return reply({session:collection});
  if(path==="/samples/sessions")return reply({sessions:collection?[collection]:[]});
  if(path.startsWith("/navigation/"))return reply({config:{revision:1,mode:"observe",speed:.1,max_motion_ms:150,corridor:[.35,.8,.65,.95],perception:{engine:"depth_plane"}},status:{running:false,stale:false},goals:{},runtime:{known:true},detector:{configured:false}});
  return reply({});
 });
 try{
  await page.goto(origin+"/rider");await page.getByTestId("rider-environment").waitFor();
  await page.getByTestId("rider-environment").selectOption("demo");
  await page.getByTestId("simulation-preview").waitFor();
  check(await page.getByTestId("rider-arm").count()===0,"redundant ARM remains");
  check(await page.getByTestId("rider-stop").count()===0,"duplicate header STOP remains");
  check(await page.getByTestId("rider-toolbar").getByTestId("study-record").count()===1,"record is not in toolbar");
  check(await page.getByTestId("study-record").count()===1&&await page.getByTestId("study-end").count()===1,"recording controls duplicated");
  await page.getByTestId("study-record").click();await page.getByTestId("study-end").waitFor({state:"visible"});
  await page.waitForFunction(()=>!document.querySelector('[data-testid="study-end"]').disabled);
  check(calls.every(row=>row.path!=="/control"),"recording armed or moved");
  await page.getByTestId("study-end").click();
  await page.waitForFunction(()=>!document.querySelector('[data-testid="rider-forward"]').disabled);
  await page.getByTestId("rider-forward").focus();await page.getByTestId("rider-forward").press("Enter");
  await page.waitForFunction(()=>Number(document.querySelector('[data-testid="simulation-robot"]').getAttribute("data-x"))>0);
  await page.getByTestId("rider-pad-stop").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="manual-feedback"]').textContent.includes("rozbrojony"));
  check(calls.filter(row=>row.path==="/control").map(row=>row.body.action).join(",")==="move,stop","pad STOP is not full STOP");
  await page.getByTestId("figure-start").waitFor({state:"visible"});
  let releaseStop,markEntered;
  const entered=new Promise(resolve=>{markEntered=resolve;}),wait=new Promise(resolve=>{releaseStop=resolve;});
  stopBarrier={entered:markEntered,wait};
  await page.getByTestId("figure-start").click();
  await Promise.race([entered,new Promise((_,reject)=>setTimeout(()=>reject(new Error("preliminary STOP missing")),3000))]);
  const escaped=new Promise(resolve=>{escapeArrived=resolve;});
  await page.keyboard.press("Escape");
  await Promise.race([escaped,new Promise((_,reject)=>setTimeout(()=>reject(new Error("Escape STOP missing")),3000))]);
  releaseStop();
  await page.waitForTimeout(350);
  check(!calls.some(row=>row.path==="/motion/figures/runs"),"Escape allowed a delayed figure start");
  {
   let release,mark;const entered=new Promise(resolve=>{mark=resolve;}),wait=new Promise(resolve=>{release=resolve;});
   stopBarrier={entered:mark,wait};
   try{
    await page.waitForFunction(()=>!document.querySelector('[data-testid="figure-start"]').disabled);
    await page.getByTestId("figure-start").click();
    await Promise.race([entered,new Promise((_,reject)=>setTimeout(()=>reject(new Error("hidden-page STOP missing")),3000))]);
    await page.evaluate(()=>{Object.defineProperty(document,"hidden",{value:true,configurable:true});document.dispatchEvent(new Event("visibilitychange"));});
   }finally{release();}
   await page.waitForTimeout(250);
   check(!calls.some(row=>row.path==="/motion/figures/runs"),"hidden page allowed a delayed figure start");
   await page.evaluate(()=>{Object.defineProperty(document,"hidden",{value:false,configurable:true});document.dispatchEvent(new Event("visibilitychange"));});
  }
  {
   let release,mark;const entered=new Promise(resolve=>{mark=resolve;}),wait=new Promise(resolve=>{release=resolve;});
   stopBarrier={entered:mark,wait};
   try{
    await page.waitForFunction(()=>!document.querySelector('[data-testid="figure-start"]').disabled);
    await page.getByTestId("figure-start").click();
    await Promise.race([entered,new Promise((_,reject)=>setTimeout(()=>reject(new Error("departure STOP missing")),3000))]);
    await page.getByRole("tab",{name:"Pomiary"}).click();
   }finally{release();}
   await page.waitForTimeout(250);
   check(!calls.some(row=>row.path==="/motion/figures/runs"),"leaving panel allowed a delayed figure start");
  }
  await page.getByRole("tab",{name:"Sterowanie"}).click();
  await page.getByTestId("figure-start").waitFor({state:"visible"});
  {
   let release,mark;const entered=new Promise(resolve=>{mark=resolve;}),wait=new Promise(resolve=>{release=resolve;});
   figureBarrier={entered:mark,wait};
   try{
    await page.waitForFunction(()=>!document.querySelector('[data-testid="figure-start"]').disabled);
    await page.getByTestId("figure-start").click();
    await Promise.race([entered,new Promise((_,reject)=>setTimeout(()=>reject(new Error("figure dispatch missing")),3000))]);
    const stopped=new Promise(resolve=>{escapeArrived=resolve;});
    await page.getByRole("tab",{name:"Pomiary"}).click();
    await Promise.race([stopped,new Promise((_,reject)=>setTimeout(()=>reject(new Error("leaving panel did not stop dispatched figure")),3000))]);
   }finally{release();}
   await page.waitForTimeout(250);
   check(calls.filter(row=>row.path==="/motion/figures/runs").length===1,"leaving panel renewed a cancelled figure");
  }
  await page.getByRole("tab",{name:"Sterowanie"}).click();
  const slider=page.getByTestId("operator-height");await slider.focus();await slider.press("End");await page.getByTestId("operator-height-apply").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="operator-height-value"]').textContent==="115");
  await page.waitForFunction(()=>!document.querySelector('[data-testid="operator-balance"]').disabled);
  await page.getByTestId("operator-balance").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="operator-balance-state"]').textContent==="Włączony");
  for(const size of [{width:1440,height:1000},{width:390,height:844}]){
   await page.setViewportSize(size);check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"operator header overflow");
   const aligned=await page.evaluate(()=>{const a=document.querySelector('[data-testid="study-record"]').getBoundingClientRect(),b=document.querySelector('[data-testid="study-end"]').getBoundingClientRect();return Math.abs(a.y-b.y)<2;});
   check(aligned,"recording actions do not share one row");
  }
  return {result:"PASS",fixtureOnly:true,cases:["offline Prod to Demo","inert recording","single toolbar actions","no ARM","one full STOP","Escape cancels pending figure","hidden page","leaving panel before/after dispatch","simulated movement","height","balance","desktop/mobile"]};
 }finally{await page.goto("about:blank");await page.unroute("**/api/v1/rider/**");}
}
