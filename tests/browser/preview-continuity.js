// Backend-projected production-shaped metadata; every Rider request is intercepted.
// This test never connects to Pi or sends a camera/actuator mutation.
async (page, fixture) => {
  const check=(ok,message)=>{if(!ok)throw new Error(message);};
  check(fixture?.preview?.preview_contract_version===1,"backend preview contract required");
  fixture={...fixture,preview:{...fixture.preview,artifact_sha256:"a".repeat(64)}};
  const base=page.url()==="about:blank"?"http://localhost:3300":new URL(page.url()).origin;
  const wait=async(fn,message)=>{const end=Date.now()+8000;while(!await fn()){check(Date.now()<end,message);await new Promise(r=>setTimeout(r,40));}};
  await page.goto("about:blank");
  const mutations=[],preparations=[];
  const profile={analysis_location:"venom",local_engine:"canny",remote_transport:"mjpeg"};
  await page.addInitScript(fixture=>{
    const original=window.fetch.bind(window),encoder=new TextEncoder();
    window.__previewContinuity={frames:0,paused:false,recover:false,connections:0};
    window.fetch=(input,options={})=>{
      const url=typeof input==="string"?input:input instanceof URL?input.href:input.url;
      const path=new URL(url,location.href).pathname;
      if(!/\/api\/v1\/rider\/camera\/stream\/(raw|venom)$/.test(path))return original(input,options);
      const kind=path.endsWith("/raw")?"raw":"venom";
      window.__previewContinuity.connections++;
      const canvas=document.createElement("canvas");canvas.width=320;canvas.height=240;
      const ctx=canvas.getContext("2d");ctx.fillStyle="#008040";ctx.fillRect(0,0,320,240);
      const jpeg=Uint8Array.from(atob(canvas.toDataURL("image/jpeg").split(",")[1]),c=>c.charCodeAt(0));
      let timer,closed=false;
      const stream=new ReadableStream({start(controller){
        const stop=()=>{if(closed)return;closed=true;clearInterval(timer);controller.close();};
        options.signal?.addEventListener("abort",stop,{once:true});
        const emit=()=>{
          const state=window.__previewContinuity;
          if(closed||state.paused)return;
          const sequence=++state.frames;
          const source={...fixture.preview.source,contract_version:1,sequence,frame_id:"preview-"+sequence,
            camera_session:"continuity-camera",pipeline_revision:34,transport_epoch:9,captured_monotonic_ns:Date.now()*1e6};
          const analysis={...fixture.preview,source,sequence,frame_id:"job-"+sequence,
            ...(state.recover?{state:"unknown",reason:"navigation_frame_gap",vision_action:"stop"}:{})};
          state.recover=false;
          const headers={"Content-Type":"image/jpeg","Content-Length":jpeg.length,
            "X-Rider-Stale":sequence%11===0?"true":"false","X-Rider-Source":kind,"X-Rider-Source-Json":JSON.stringify(source),
            "X-Rider-Frame-Id":source.frame_id,"X-Rider-Frame-Age-Ms":sequence%11===0?"550":"160",
            "X-Rider-Frame-Age-Source":"pi-status-anchor-plus-host-elapsed",
            "X-Rider-Analysis-Json":JSON.stringify(analysis),"X-Rider-Vision-State":analysis.state};
          const crlf=String.fromCharCode(13,10);
          controller.enqueue(encoder.encode("--continuity"+crlf+Object.entries(headers).map(([k,v])=>k+": "+v).join(crlf)+crlf+crlf));
          controller.enqueue(jpeg);controller.enqueue(Uint8Array.of(13,10));
        };
        timer=setInterval(emit,80);emit();
      },cancel(){closed=true;clearInterval(timer);}});
      return Promise.resolve(new Response(stream,{headers:{"Content-Type":"multipart/x-mixed-replace; boundary=continuity"}}));
    };
  },fixture);
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=body=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(body)});
    if(path==="/perception/prepare"&&req.method()==="POST"){preparations.push(path);return json({ok:true});}
    if(req.method()!=="GET"){mutations.push(path);return route.abort();}
    if(path==="/config")return json({devices:[{id:"fixture",label:"Prod fixture",mode:"device"}],device_id:"fixture",session:"continuity-session"});
    if(path==="/status")return json({connection:{configured:true,status:"online",device_id:"fixture",mode:"device",session:"continuity-session"},polling:{control_ms:200},errors:{},settings:{revision:1,directions:{}},control:{server_time:Date.now()/1000,safety:{state:"DISARMED",generation:"fixture",can_move:false,moving:false,ready:true,stale:false}},telemetry:{},health:{},catalog:{features:[]},logic:{},face:{},resources:{}});
    if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,revision:34,profile,active_profile:profile,state:"active",transport_epoch:9,camera_session:"continuity-camera",capabilities:{analysis_locations:["venom","local"],local_engines:["canny"],remote_transports:["snapshots","mjpeg"]},effective_stages:{capture:true},latest_frame:{camera_session:"continuity-camera",transport_epoch:9,pipeline_revision:34,captured_monotonic_ns:Date.now()*1e6,frame_age_ms:20},error:null});
    if(path==="/navigation/config")await new Promise(resolve=>setTimeout(resolve,1400));
    if(path==="/navigation/config"||path==="/navigation/state")return json({config:{revision:153,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],perception:{engine:"depth_plane",model_id:fixture.preview.model_id,artifact_sha256:fixture.preview.artifact_sha256},verified:false},status:{running:true,stale:false,mode:"observe",run_id:fixture.preview.run_id,config_revision:153,decision:fixture.preview},goals:{observe:true,drive:false},runtime:{observe:true,drive:true,known:true},detector:{configured:true,loaded:true,sha256:fixture.preview.model_sha256}});
    return json({});
  });
  try{
    await page.goto(base+"/rider",{waitUntil:"domcontentloaded"});
    await page.getByTestId("camera-power-toggle").waitFor();
    const analysis=page.getByRole("button",{name:/^(Analiza|Wizja Venom)$/});
    await analysis.click();
    const center=page.getByTestId("sector-center"),notice=page.getByTestId("vision-preview-paused");
    await wait(async()=>await center.getAttribute("data-sector-state")==="clear"&&!await notice.count(),"initial current corridor not visible");
    const stability=await page.evaluate(async()=>{
      const events=[],painted=[];let last="",lastPaint="",active=true;
      const read=()=>({notice:!!document.querySelector('[data-testid="vision-preview-paused"]'),
        states:[...document.querySelectorAll('[data-sector-state]')].map(x=>x.dataset.sectorState).join(",")});
      const record=()=>{const row=read(),key=JSON.stringify(row);if(last!==key){events.push(row);last=key;}};
      const paint=()=>{if(!active)return;const row=read(),key=JSON.stringify(row);if(lastPaint!==key){painted.push(row);lastPaint=key;}requestAnimationFrame(paint);};
      const observer=new MutationObserver(record);
      observer.observe(document.querySelector('[data-testid="camera-surface"]').parentElement,{attributes:true,childList:true,subtree:true,characterData:true});
      record();requestAnimationFrame(paint);
      const frameStart=window.__previewContinuity.frames;
      let pause,resume;
      try{
        pause=setTimeout(()=>{window.__previewContinuity.paused=true;},2000);
        resume=setTimeout(()=>{window.__previewContinuity.recover=true;window.__previewContinuity.paused=false;},2350);
        await new Promise(r=>setTimeout(r,10000));
        return {durationMs:10000,frames:window.__previewContinuity.frames-frameStart,events,painted};
      }finally{active=false;observer.disconnect();clearTimeout(pause);clearTimeout(resume);}
    });
    check(stability.frames>=80,"input stream did not progress");
    for(const row of [...stability.events,...stability.painted])
      check(!row.notice&&row.states==="obstacle,clear,clear","short gap or frame replacement flashed stale/unknown: "+JSON.stringify(row));
    await page.evaluate(()=>{window.__previewContinuity.paused=true;});
    await wait(async()=>(await notice.count()>0||await page.getByTestId("camera-surface").locator("img").count()===0)&&await center.getAttribute("data-sector-state")==="unknown","real source loss did not expire the original frame");
    await page.evaluate(()=>{window.__previewContinuity.paused=false;});
    await wait(async()=>!await notice.count()&&await center.getAttribute("data-sector-state")==="clear","current source did not recover");
    check(preparations.length===1,"explicit Analysis must prepare once; passive polling must not prepare");
    await analysis.click();
    await wait(async()=>preparations.length===2,"a new explicit Analysis click did not prepare again");
    const imageFit=[];
    for(const viewport of [{width:1282,height:913},{width:1440,height:1000},{width:390,height:844}]){
      await page.setViewportSize(viewport);
      const expand=page.getByRole("button",{name:"Rozwiń pulpit sterowania",exact:true});
      if(await expand.count())await expand.click();
      let rawSurface=null;
      for(const name of ["Obraz surowy","Wizja Venom"]){
        await page.getByRole("button",{name,exact:true}).click();
        await wait(async()=>await page.getByRole("button",{name,exact:true}).getAttribute("aria-pressed")==="true"&&await page.getByTestId("camera-surface").locator("img").count()>0,"requested source not visible");
        const fit=await page.getByTestId("camera-surface").evaluate(surface=>{
          const image=surface.querySelector("img"),box=surface.getBoundingClientRect(),img=image.getBoundingClientRect();
          return {surface:[box.x,box.y,box.width,box.height],image:[img.x,img.y,img.width,img.height],natural:[image.naturalWidth,image.naturalHeight],top:Math.max(0,box.top-img.top),bottom:Math.max(0,img.bottom-box.bottom),left:Math.max(0,box.left-img.left),right:Math.max(0,img.right-box.right),fit:getComputedStyle(image).objectFit};
        });
        check(fit.natural.join(",")==="320,240"&&fit.fit==="contain","source dimensions or fit changed");
        check(Math.max(fit.top,fit.bottom,fit.left,fit.right)<=1,"camera image clipped: "+JSON.stringify({viewport,name,fit}));
        if(name==="Obraz surowy")rawSurface=fit.surface;
        else check(fit.surface.every((value,index)=>Math.abs(value-rawSurface[index])<=1),"RAW/analysis frame moved or resized: "+JSON.stringify({viewport,rawSurface,analysis:fit.surface}));
        imageFit.push({viewport,name,...fit});
      }
    }
    check(mutations.length===0,"fixture attempted a hardware mutation");
    return {result:"PASS",fixtureOnly:true,stability,realExpiryAndRecovery:true,preparations,imageFit,mutations};
  }finally{await page.goto("about:blank");await page.unrouteAll({behavior:"wait"});}
}
