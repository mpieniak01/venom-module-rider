// Every Rider endpoint is intercepted. Native streams are generated in this page.
// Recovery reconnects only GET media; it must never start goals, ARM or move Pi.
async page => {
  const base=page.url()==="about:blank"?"http://localhost:3300":new URL(page.url()).origin;
  const check=(value,message)=>{if(!value)throw new Error(message);};
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  const wait=async(fn,message,timeout=8500)=>{const end=Date.now()+timeout;while(!await fn()){check(Date.now()<end,message);await new Promise(r=>setTimeout(r,50));}};
  const mutations=[],scenarios=[];
  const profile={analysis_location:"venom",local_engine:"canny",remote_transport:"mjpeg"};
  await page.goto("about:blank");
  await page.addInitScript(()=>{
    const realFetch=window.fetch.bind(window),encoder=new TextEncoder();
    window.__nativeRecovery={connections:0,sequence:0,failure:"none",target:0};
    window.fetch=(input,options={})=>{
      const url=typeof input==="string"?input:input instanceof URL?input.href:input.url;
      if(!new URL(url,location.href).pathname.endsWith("/api/v1/rider/camera/stream/venom"))return realFetch(input,options);
      const connection=++window.__nativeRecovery.connections;
      const canvas=document.createElement("canvas");canvas.width=320;canvas.height=240;
      canvas.getContext("2d").fillStyle="#00aa70";canvas.getContext("2d").fillRect(0,0,320,240);
      const jpeg=Uint8Array.from(atob(canvas.toDataURL("image/jpeg").split(",")[1]),c=>c.charCodeAt(0));
      let timer,closed=false;
      const stream=new ReadableStream({start(controller){
        const stop=()=>{if(closed)return;closed=true;clearInterval(timer);controller.close();};
        options.signal?.addEventListener("abort",stop,{once:true});
        const emit=()=>{
          if(closed)return;
          const state=window.__nativeRecovery,bad=state.failure!=="none"&&state.target===connection;
          const sequence=++state.sequence;
          const source={contract_version:1,camera_session:"recovery-camera",transport_epoch:9,pipeline_revision:34,sequence,frame_id:"recovery-"+sequence,captured_monotonic_ns:Date.now()*1e6,sha256:"b".repeat(64)};
          const analysis={run_id:"native-preview-recovery",frame_id:source.frame_id,sequence,config_revision:153,state:"clear",vision_action:"go",action:"stop",preview_only:true,source:bad&&state.failure==="mismatch"?{...source,sequence:sequence-1}:source};
          const payload=bad&&state.failure==="decode"?Uint8Array.of(255,216,0,1,255,217):jpeg;
          const headers={"Content-Type":"image/jpeg","Content-Length":payload.length,"X-Rider-Stale":"false","X-Rider-Frame-Id":source.frame_id,"X-Rider-Frame-Age-Ms":bad&&state.failure==="expired"?"5000":"20","X-Rider-Frame-Age-Source":"pi-status-anchor-plus-host-elapsed","X-Rider-Source":"venom","X-Rider-Source-Json":JSON.stringify(source),"X-Rider-Analysis-Json":JSON.stringify(analysis),"X-Rider-Vision-State":"clear"};
          const crlf=String.fromCharCode(13,10);
          controller.enqueue(encoder.encode("--recovery"+crlf+Object.entries(headers).map(([k,v])=>k+": "+v).join(crlf)+crlf+crlf));controller.enqueue(payload);controller.enqueue(Uint8Array.of(13,10));
        };
        timer=setInterval(emit,80);emit();
      },cancel(){closed=true;clearInterval(timer);}});
      return Promise.resolve(new Response(stream,{headers:{"Content-Type":"multipart/x-mixed-replace; boundary=recovery"}}));
    };
  });
  await page.route("**/api/v1/rider/**",route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=body=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){mutations.push(path);return route.abort();}
    if(path==="/config")return json({devices:[{id:"fixture",label:"Prod fixture",mode:"device"}],device_id:"fixture",session:"native-recovery"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"native-recovery",device_id:"fixture",mode:"device"},polling:{control_ms:200},errors:{},settings:{revision:1,directions:{}},control:{server_time:Date.now()/1000,safety:{state:"DISARMED",generation:"recovery-generation",owner:null,can_move:false,ready:true,stale:false}},health:{},telemetry:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,revision:34,profile,active_profile:profile,state:"active",transport_epoch:9,camera_session:"recovery-camera",capabilities:{analysis_locations:["venom","local"],local_engines:["canny"],remote_transports:["snapshots","mjpeg"]},effective_stages:{capture:true,hardware_mjpeg:true},latest_frame:{camera_session:"recovery-camera",transport_epoch:9,pipeline_revision:34,captured_monotonic_ns:Date.now()*1e6,frame_age_ms:20},error:null});
    if(path==="/navigation/state"||path==="/navigation/config")return json({config:{revision:153,mode:"drive",speed:.1,max_motion_ms:150,decision_budget_ms:300,verified:true,corridor:[.35,.8,.65,.95],perception:{engine:"depth_plane"}},status:{running:false,stale:true,mode:"observe",run_id:"retired-pi-run",stop_reason:"navigation_stopped",journey:{version:1,id:"retired",active:false,state:"failed",reason:"decision_timeout"}},goals:{observe:false,drive:true},runtime:{known:true,observe:true,drive:true},detector:{configured:true,loaded:true}});
    return json({});
  });
  const image=()=>page.getByTestId("camera-surface").locator("img");
  const notice=page.getByTestId("vision-preview-paused");
  try{
    await page.goto(base+"/rider");
    await page.getByRole("button",{name:"Wizja Venom",exact:true}).click();
    await wait(async()=>await image().count()===1&&!await notice.isVisible(),"initial valid native frame never displayed");
    scenarios.push("retired journey retains observe-only GET preview without starting any goal");
    for(const failure of ["expired","mismatch","decode"]){
      const connection=await page.evaluate(failure=>{const s=window.__nativeRecovery;s.failure=failure;s.target=s.connections;return s.connections;},failure);
      await wait(async()=>await notice.isVisible(),"rejected parts falsely kept displayed frame current: "+failure);
      const staleUrl=await image().getAttribute("src");
      await wait(async()=>await page.evaluate(()=>window.__nativeRecovery.connections)>connection,"new but rejected "+failure+" parts kept the dead stream alive forever");
      await wait(async()=>await image().count()===1&&(await image().getAttribute("src"))!==staleUrl&&!await notice.isVisible(),"fresh GET reconnect did not replace stale frame: "+failure);
      scenarios.push(failure+" parts expire original preview and force GET reconnect; fresh validated source clears stale state");
    }
    check(mutations.length===0,"media recovery submitted robot mutations: "+mutations);
    return {fixtureOnly:true,passed:scenarios,mutations,stream:await page.evaluate(()=>window.__nativeRecovery)};
  } finally {await page.goto("about:blank");await page.unrouteAll({behavior:"wait"});}
}
