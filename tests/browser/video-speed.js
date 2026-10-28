// Virtual clock is installed before mounting the fixture. No models or device.
async page=>{
 const check=(value,message)=>{if(!value)throw new Error(message);};
 const target=page.url(),sid="5".repeat(32),runId="6".repeat(32),sha="a".repeat(64),unexpected=[],writes=[],images=[];
 let delayed=false;
 const png=Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAYAAAChS3wfAAAAbUlEQVR4nO3QUQmAUBBFQTXXy2Mo89hLKygoB3Hme1kOd5oAAADgd+arh8cY65shd837vj3xZ3niyZcZoA6oGaAOqBmgDqgZoA6oGaAOqBmgDqgZoA6oGaAOqBmgDqgZoA6oGaAOqBmgDgAAMifrYwQ0T1A1TAAAAABJRU5ErkJggg==","base64");
 const session={session_id:sid,source_device_id:"fixture",state:"closed",started_at:1,policy:{},counts:{saved:0},video:{state:"complete",frames:4,bytes:4000,duration_s:.6,target_fps:5,gaps:0,error:null,ended_at:2}};
 await page.route("**/api/v1/rider/**",async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;
  const reply=value=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
  if(request.method()!=="GET"){writes.push(path);return route.abort();}
  if(path.endsWith("/samples/sessions"))return reply({sessions:[session]});
  if(path.endsWith("/sessions/"+sid))return reply({session,samples:[],total:0,offset:0,limit:12});
  if(path.endsWith("/video/frames")){
   const offset=Number(new URL(request.url()).searchParams.get("offset")||0),end=Math.min(session.video.frames,offset+500);
   return reply({session_id:sid,total:session.video.frames,duration_s:session.video.duration_s,frames:Array.from({length:end-offset},(_,n)=>({index:offset+n,source_time_s:(offset+n)*.2,frame_id:"frame"+(offset+n),sequence:offset+n,raw_sha256:sha})),next_offset:end<session.video.frames?end:null});
  }
  if(/\/video\/frames\/\d+$/.test(path)||path.endsWith("-mask.png")){
   images.push(path);
   const index=Number(path.split("/").at(-1).split("-")[0]);
   if(delayed&&index>0)await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,30)));
   return route.fulfill({status:200,contentType:"image/png",body:png});
  }
  if(path.endsWith("/analysis/profiles"))return reply({profiles:[{id:"da2-default",installed:true,windows:[1]}]});
    const run={id:runId,state:"succeeded",done:session.video.frames,total:session.video.frames,phase:"succeeded",created:1};
  if(path.endsWith("/analysis/runs"))return reply({runs:delayed?[run]:[]});
  if(path.endsWith("/analysis/runs/"+runId))return reply(run);
  if(path.endsWith("/report.json"))return reply({run_id:runId,input_kind:"video",rows:[],row_chunks:["rows-00000.json"],context:{spec:{},input_source:{kind:"session-video",session_id:sid}},summary:{frames:session.video.frames}});
  if(path.endsWith("/rows-00000.json"))return reply(Array.from({length:session.video.frames},(_,index)=>({source_index:index,raw_sha256:sha,source_time_s:index*.2,state:"clear",artifact_names:[String(index).padStart(4,"0")+"-mask.png"],classification_layer:{version:1,artifact:String(index).padStart(4,"0")+"-mask.png",width:64,height:48,palette_version:1,source_sha256:sha}})));
  unexpected.push(path);return route.abort();
 });
 await page.clock.install({time:new Date("2026-01-01T00:00:00Z")});
 await page.goto(target);
 await page.getByTestId("samples-session").selectOption(sid);
 const waitSource=async index=>{
  for(let attempt=0;attempt<150;attempt++){
   if(await page.getByTestId("source-video-image").getAttribute("data-source-index")===String(index))return;
   await new Promise(resolve=>setTimeout(resolve,10));
  }
  throw new Error("source did not reach frame "+index+" at the selected playback speed");
 };
 await page.getByTestId("source-video-image").waitFor();
 const waitReady=async()=>{
  for(let attempt=0;attempt<150;attempt++){
   if(await page.getByTestId("source-video-viewport").getAttribute("aria-busy")==="false")return;
   await new Promise(resolve=>setTimeout(resolve,10));
  }
  throw new Error("video frame was not ready before starting playback");
 };
 await waitReady();
 await page.clock.pauseAt(new Date("2026-01-01T00:00:01Z"));
 for(const [rate,delay]of [["1",200],["2",100],["5",40]]){
  await page.getByTestId("analysis-frame").fill("1");await waitSource(0);await waitReady();
  await page.getByTestId("source-video-speed").selectOption(rate);
  await page.getByTestId("source-video-play").click();
  await page.clock.runFor(1);
  await page.clock.runFor(delay-2);
  check(await page.getByTestId("source-video-image").getAttribute("data-source-index")==="0","playback advanced before its selected interval");
  await page.clock.runFor(2);await waitSource(1);
  await page.getByTestId("source-video-play").click();await page.clock.runFor(500);
  check(await page.getByTestId("source-video-image").getAttribute("data-source-index")==="1","paused playback advanced at speed "+rate);
 }
 await page.getByTestId("analysis-frame").fill("1");await waitSource(0);await waitReady();
 await page.getByTestId("source-video-speed").selectOption("1");
 await page.getByTestId("source-video-play").click();await page.clock.runFor(99);
 await page.getByTestId("source-video-speed").selectOption("5");
 await page.clock.runFor(39);
 check(await page.getByTestId("source-video-image").getAttribute("data-source-index")==="0","rate change reused an old deadline");
 await page.clock.runFor(2);await waitSource(1);
 await page.getByTestId("source-video-play").click();
 // RAW and mask each take 30 virtual milliseconds. Fetch time must fit INSIDE
 // each source interval, rather than being added to every playback step.
 delayed=true;session.video.frames=12;session.video.duration_s=2.2;
 const timings=[];
 for(const rate of [1,2,5]){
  await page.clock.resume();await page.goto(target);
  await page.getByTestId("samples-session").selectOption(sid);
  await page.getByTestId("analysis-run").selectOption(runId);
  await page.getByTestId("analysis-frame-overlay").waitFor();
  await page.getByTestId("source-video-speed").selectOption(String(rate));
  await page.clock.pauseAt(new Date(await page.evaluate(()=>Date.now())+100));
  const firstRequest=images.length;
  await page.evaluate(()=>{
   window.speedFrames=[{index:0,at:performance.now()}];window.speedMaxImages=0;
   window.speedObserver=new MutationObserver(()=>{
    const image=document.querySelector('[data-testid="source-video-image"]');
    if(!image)return;
    const index=Number(image.getAttribute("data-source-index"));
    if(window.speedFrames.at(-1).index!==index)window.speedFrames.push({index,at:performance.now()});
    window.speedMaxImages=Math.max(window.speedMaxImages,document.querySelectorAll('[data-testid="source-video-image"],[data-testid="source-video-pending-image"]').length);
   });
   window.speedObserver.observe(document.querySelector('[data-testid="source-video-viewport"]'),{subtree:true,attributes:true,childList:true});
  });
  await page.getByTestId("source-video-play").click();
  const start=await page.evaluate(()=>performance.now());
  for(let elapsed=0;elapsed<2200/rate+10;elapsed+=10){
   await page.clock.runFor(10);await new Promise(resolve=>setTimeout(resolve,20));
  }
  const observed=await page.evaluate(()=>{window.speedObserver.disconnect();return {frames:window.speedFrames,maxImages:window.speedMaxImages};});
  check(JSON.stringify(observed.frames.map(frame=>frame.index))===JSON.stringify(Array.from({length:12},(_,index)=>index)),"delayed playback skipped or repeated a frame at ×"+rate);
  for(const frame of observed.frames.slice(1))check(Math.abs(frame.at-start-frame.index*200/rate)<=10.1,"fetch/decode drift accumulated at ×"+rate+": "+JSON.stringify(frame));
  check(observed.maxImages<=3,"prefetch grew beyond current, retained and next pair");
  const requested=images.slice(firstRequest);
  for(let index=1;index<12;index++){
   check(requested.filter(path=>path.endsWith("/video/frames/"+index)).length===1,"RAW fetched twice while promoting frame "+index);
   check(requested.filter(path=>path.endsWith("/"+String(index).padStart(4,"0")+"-mask.png")).length===1,"mask fetched twice while promoting frame "+index);
  }
  check(await page.getByTestId("analysis-frame-overlay").getAttribute("data-source-index")==="11","delayed playback mismatched final mask");
  timings.push({rate,elapsedMs:observed.frames.at(-1).at-start,maxImages:observed.maxImages});
 }
 // Cross the index-page boundary while playing; only the next page is needed.
 await page.clock.resume();session.video.frames=502;session.video.duration_s=100.2;
 await page.goto(target);await page.getByTestId("samples-session").selectOption(sid);
 await page.getByTestId("source-video-image").waitFor();
 await page.getByTestId("analysis-frame").fill("499");await waitSource(498);
 await page.getByTestId("source-video-speed").selectOption("5");
 await page.getByTestId("source-video-play").click();await waitSource(501);
 check(await page.getByTestId("source-video-play").textContent()!=="Wstrzymaj","end of page-boundary playback did not pause");
 check(writes.length===0&&unexpected.length===0,"playback speed issued a model/device request");
 return {result:"PASS",rates:[1,2,5],frameIntervalsMs:[200,100,40],pausedAtEveryRate:true,rateChangedWhilePlaying:true,delayedRawAndMaskMs:30,multiFrameTimings:timings,noRepeatedFetch:true,pageBoundary500:true,requestsReadOnly:true,robotContacted:false};
}
