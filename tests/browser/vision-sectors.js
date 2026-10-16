// Playwright CLI: call this function with renderer-produced fixture images.
// Every Rider request is intercepted. No request reaches the robot.
async (page, fixtures) => {
  if (!fixtures?.left?.body || !fixtures?.ground?.body) throw new Error("renderer fixtures required");
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const wait = async (predicate, message) => {
    const until=Date.now()+8000;
    while (!await predicate()) { if(Date.now()>until)throw new Error(message); await page.waitForTimeout(40); }
  };
  await page.goto("about:blank");
  await page.unroute("**/api/v1/rider/**");
  let selected="left", nativeIdentity=false, failMedia=false, reads=0;
  const mutations=[], scenarios=[];
  const detector={configured:true,loaded:true,sha256:"a".repeat(64),purpose:"relative_ground_geometry",drive_supported:true,supported_protocol_versions:[1,2,3]};
  const policy={revision:39,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],verified:true,calibration_id:"fixture",model_sha256:"a".repeat(64),perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2}};
  const state=()=>({config:policy,detector,runtime:{observe:true,drive:true,known:true},goals:{observe:true,drive:false},
    status:{running:true,stale:false,run_id:"fixture-observe",mode:"observe",config_revision:39,decision:{
      frame_id:"newer-loop-poll",run_id:"fixture-observe",sequence:999,config_revision:39,state:"clear",clear_count:3,vision_action:"go",action:"stop",
      sectors:{left:{state:"clear",coverage_complete:true},center:{state:"clear",coverage_complete:true},right:{state:"obstacle",coverage_complete:true}}}}});
  await page.route("**/api/v1/rider/**",async route=>{
    const request=route.request(),path=new URL(request.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(request.method()!=="GET"){mutations.push(path);return route.abort("blockedbyclient");}
    if(path==="/config")return json({devices:[{id:"fixture-demo",label:"Sector fixture",mode:"demo"}],device_id:"fixture-demo",session:"fixture-session"});
    if(path==="/navigation/config"||path==="/navigation/state")return json(state());
    if(path==="/status")return json({connection:{configured:true,status:"online",device_id:"fixture-demo",mode:"demo",session:"fixture-session"},polling:{control_ms:500},errors:{},
      control:{server_time:Date.now()/1000,safety:{state:"DISARMED",owner:null,generation:"fixture",observed_at:Date.now()/1000,stale:false,can_move:false,ready:true}},
      settings:{revision:1,directions:{},values:{},parameter_schema:{}},telemetry:{camera:{on:true}},health:{camera:{on:true}},catalog:{features:[]},logic:{},resources:{},face:{}});
    if(path.startsWith("/camera/")){
      if(failMedia)return route.abort("failed");
      reads++;
      const f=fixtures[selected],capture="fixture-capture-"+reads;
      const analysis={run_id:"fixture-observe",frame_id:nativeIdentity?"job-"+reads:capture,sequence:reads,config_revision:39,state:"clear",vision_action:"stop",reason:"depth_plane_candidate",sector_space:f.space,sectors:f.sectors};
      if(nativeIdentity)analysis.source={frame_id:capture,sequence:1000+reads,camera_session:"fixture-camera"};
      return route.fulfill({contentType:"image/jpeg",body:Buffer.from(f.body,"base64"),headers:{
        "X-Rider-Frame-Id":capture,"X-Rider-Frame-Age-Ms":"20","X-Rider-Frame-Age-Source":"pi-reported-plus-host-elapsed","X-Rider-Stale":"false",
        "X-Rider-Vision-State":"clear","X-Rider-Vision-Reason":"depth_plane_candidate","X-Rider-Source":"fixture","X-Rider-Analysis-Json":JSON.stringify(analysis)}});
    }
    // Unsupported optional fixture capabilities stay unavailable, never proxied.
    return json({detail:{code:"fixture_unavailable"}},404);
  });
  await page.goto("http://localhost:3300/rider");
  const sector = side => page.getByTestId("sector-"+side);
  const states = () => Promise.all(["left","center","right"].map(side=>sector(side).getAttribute("data-sector-state")));
  await wait(async()=>await sector("left").count()===1 && (await states()).join()==="obstacle,clear,clear","decoded image sectors missing");
  check((await states()).join()==="obstacle,clear,clear","cards borrowed the deliberately conflicting newer loop state");
  // Observe the intermediate DOM commits as well as painted frames: final-state
  // assertions alone missed the unavailable/unknown flash on every JPEG load.
  const stability = await page.evaluate(async () => {
    const root=document.querySelector('[data-testid="directional-results"]');
    const image=document.querySelector('[data-testid="camera-preview-toggle"]').closest("section").querySelector("img");
    const events=[],painted=[];let last="",lastPaint="",running=true,loads=0;
    const read=()=>({space:root.dataset.sectorSpace,states:[...root.querySelectorAll('[data-sector-state]')].map(e=>e.dataset.sectorState).join(","),height:Math.round(root.getBoundingClientRect().height)});
    const capture=()=>{const row=read(),key=JSON.stringify(row);if(key!==last){events.push(row);last=key;}};
    const paint=()=>{if(!running)return;const row=read(),key=JSON.stringify(row);if(key!==lastPaint){painted.push(row);lastPaint=key;}requestAnimationFrame(paint);};
    const observer=new MutationObserver(capture);
    observer.observe(root,{attributes:true,childList:true,subtree:true,characterData:true});capture();requestAnimationFrame(paint);
    let loaded,deadline;
    try {
      await new Promise((resolve,reject)=>{
        loaded=()=>{if(++loads>=8)resolve();};
        image.addEventListener("load",loaded);
        deadline=setTimeout(()=>reject(new Error("eight fresh JPEG loads required")),8000);
      });
      await new Promise(requestAnimationFrame);capture();
      return {loads,events,painted};
    } finally {running=false;observer.disconnect();image.removeEventListener("load",loaded);clearTimeout(deadline);}
  });
  for(const row of [...stability.events,...stability.painted])
    check(row.space==="image"&&row.states==="obstacle,clear,clear","fresh image replacement flashed unknown sector cards");
  check(new Set(stability.events.map(row=>row.height)).size===1,"sector block jumped during fresh image replacement");
  scenarios.push({freshReplacementStability:stability});
  for(const width of [390,768,1600]){
    await page.setViewportSize({width,height:1100});
    await page.getByTestId("directional-results").scrollIntoViewIfNeeded();
    const boxes=await Promise.all(["left","center","right"].map(side=>sector(side).boundingBox()));
    check(boxes.every(Boolean)&&boxes[0].x<boxes[1].x&&boxes[1].x<boxes[2].x,"sector order lost at "+width);
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
    check(!overflow,"horizontal overflow at "+width);
    scenarios.push({width,space:"image",states:await states()});
    if(width===390)await page.screenshot({path:"output/playwright/vision-sectors-mobile.png",fullPage:true});
  }
  await page.getByTestId("zone-edit").click();
  await page.getByTestId("sector-draft-hint").waitFor({state:"visible"});
  const draft=page.getByTestId("corridor-editor").locator('[data-handle="move"]');
  await draft.hover();
  const original=await draft.getAttribute("x"),box=await draft.boundingBox();
  check(box,"draft rectangle missing");
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  await page.mouse.down();await page.mouse.move(box.x+box.width/2+20,box.y+box.height/2,{steps:3});await page.mouse.up();
  await wait(async()=>await draft.getAttribute("x")!==original,"ROI draft did not move");
  check((await states()).join()==="obstacle,clear,clear","unsaved draft relabelled the analysed frame");
  check((await page.getByTestId("frame-identity").innerText()).includes("r39"),"analysis revision missing");
  scenarios.push({draftMoved:true,frameRevision:39,states:await states()});
  await page.getByTestId("zone-edit").click();
  nativeIdentity=true;selected="right";
  await wait(async()=>(await states()).join()==="clear,clear,obstacle","native source identity rejected or prior frame retained");
  scenarios.push({identity:"camera-source-differs-from-job",states:await states()});
  selected="ground";
  await wait(async()=>await page.getByTestId("directional-results").getAttribute("data-sector-space")==="projected_paths","ground projection legend missing");
  check((await page.getByTestId("sector-legend").innerText()).toLowerCase().includes("rzuty"),"image-third legend used for ground paths");
  scenarios.push({space:"projected_paths",states:await states()});
  failMedia=true;
  await page.getByTestId("vision-preview-paused").waitFor({state:"visible"});
  await wait(async()=>(await states()).every(state=>state==="unknown"),"expired frame still claims clear sides");
  scenarios.push({expired:true,states:await states()});
  failMedia=false;selected="left";
  await wait(async()=>(await states()).join()==="obstacle,clear,clear","fresh frame did not recover");
  await page.setViewportSize({width:1600,height:1100});
  await page.getByTestId("directional-results").scrollIntoViewIfNeeded();
  await page.screenshot({path:"output/playwright/vision-sectors-desktop.png",fullPage:true});
  check(mutations.length===0,"observation attempted hardware mutation");
  return {scope:"isolated renderer/JPEG fixture; native frame-vs-job correlation; no physical robot",scenarios,reads,mutations};
}
