// Virtual clock is installed before mounting the fixture. No models or device.
async page=>{
 const check=(value,message)=>{if(!value)throw new Error(message);};
 const target=page.url(),sid="5".repeat(32),sha="a".repeat(64),unexpected=[],writes=[];
 const png=Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAYAAAChS3wfAAAAbUlEQVR4nO3QUQmAUBBFQTXXy2Mo89hLKygoB3Hme1kOd5oAAADgd+arh8cY65shd837vj3xZ3niyZcZoA6oGaAOqBmgDqgZoA6oGaAOqBmgDqgZoA6oGaAOqBmgDqgZoA6oGaAOqBmgDgAAMifrYwQ0T1A1TAAAAABJRU5ErkJggg==","base64");
 const session={session_id:sid,source_device_id:"fixture",state:"closed",started_at:1,policy:{},counts:{saved:0},video:{state:"complete",frames:4,bytes:4000,duration_s:.6,target_fps:5,gaps:0,error:null,ended_at:2}};
 await page.route("**/api/v1/rider/**",route=>{
  const request=route.request(),path=new URL(request.url()).pathname;
  const reply=value=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
  if(request.method()!=="GET"){writes.push(path);return route.abort();}
  if(path.endsWith("/samples/sessions"))return reply({sessions:[session]});
  if(path.endsWith("/sessions/"+sid))return reply({session,samples:[],total:0,offset:0,limit:12});
  if(path.endsWith("/video/frames"))return reply({session_id:sid,total:4,duration_s:.6,frames:[0,1,2,3].map(index=>({index,source_time_s:index*.2,frame_id:"frame"+index,sequence:index,raw_sha256:sha})),next_offset:null});
  if(/\/video\/frames\/\d+$/.test(path))return route.fulfill({status:200,contentType:"image/png",body:png});
  if(path.endsWith("/analysis/profiles"))return reply({profiles:[{id:"da2-default",installed:true,windows:[1]}]});
  if(path.endsWith("/analysis/runs"))return reply({runs:[]});
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
 await page.clock.pauseAt(new Date("2026-01-01T00:00:01Z"));
 for(const [rate,delay]of [["1",200],["2",100],["5",40]]){
  await page.getByTestId("analysis-frame").fill("1");await waitSource(0);
  await page.getByTestId("source-video-speed").selectOption(rate);
  await page.getByTestId("source-video-play").click();
  await page.clock.runFor(delay-1);
  check(await page.getByTestId("source-video-image").getAttribute("data-source-index")==="0","playback advanced before its selected interval");
  await page.clock.runFor(2);await waitSource(1);
  await page.getByTestId("source-video-play").click();await page.clock.runFor(500);
  check(await page.getByTestId("source-video-image").getAttribute("data-source-index")==="1","paused playback advanced at speed "+rate);
 }
 await page.getByTestId("analysis-frame").fill("1");await waitSource(0);
 await page.getByTestId("source-video-speed").selectOption("1");
 await page.getByTestId("source-video-play").click();await page.clock.runFor(99);
 await page.getByTestId("source-video-speed").selectOption("5");
 await page.clock.runFor(39);
 check(await page.getByTestId("source-video-image").getAttribute("data-source-index")==="0","rate change reused an old deadline");
 await page.clock.runFor(2);await waitSource(1);
 await page.getByTestId("source-video-play").click();
 check(writes.length===0&&unexpected.length===0,"playback speed issued a model/device request");
 return {result:"PASS",rates:[1,2,5],frameIntervalsMs:[200,100,40],pausedAtEveryRate:true,rateChangedWhilePlaying:true,requestsReadOnly:true,robotContacted:false};
}
