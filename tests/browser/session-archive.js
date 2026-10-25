// Isolated session UI; all data/video endpoints are fixtures with no device connection.
async page=>{
 const check=(value,message)=>{if(!value)throw new Error(message);};
 const jpeg=Buffer.from("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAwAEADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwChRRRXoHCFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQB//2Q==","base64");
 const sha="8e19a552b0916f795708653260f653036609c2aed64e165c5fc5f70002fd375f";
 const sessions=[],writes=[],photos=[];
 const video=state=>({state,frames:state==="waiting_camera"?0:5,bytes:5000,duration_s:1,target_fps:5,gaps:0,error:null,ended_at:state==="complete"?1002:null});
 const base="/api/v1/rider/samples";
 await page.route("**/api/**",async route=>{
  const request=route.request(),path=new URL(request.url()).pathname,method=request.method();
  const reply=value=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
  if(method!=="GET")writes.push({path,body:request.postDataJSON()});
  if(path===base+"/capture"&&method==="POST"){const intent=request.postDataJSON();photos.push(intent);return reply({id:intent.capture_request_id,state:"saved",sample_id:"a".repeat(32),saved_at:1001});}
  if(path===base+"/automatic")return reply({state:"off"});
  if(path===base+"/sessions"&&method==="POST"){
   const body=request.postDataJSON();
   check(body.policy.record_video===true&&body.policy.record_images===false,"record omitted video opt-in");
   const session={session_id:String(sessions.length+1).repeat(32),source_device_id:"fixture-robot",started_at:1000+sessions.length,state:"collecting",counts:{},policy:body.policy,video:video("waiting_camera")};
   sessions.unshift(session);return reply(session);
  }
  if(path===base+"/sessions")return reply({sessions});
  if(path.startsWith(base+"/sessions/")){
   const id=path.split("/")[6],session=sessions.find(row=>row.session_id===id);
   check(!!session,"unknown fixture session");
   if(path.endsWith("/video/frames"))return reply({session_id:id,total:5,duration_s:1,frames:Array.from({length:5},(_,index)=>({index,source_time_s:index*.25,frame_id:"frame"+index,sequence:index,raw_sha256:sha})),next_offset:null});
   if(/\/video\/frames\/\d+$/.test(path))return route.fulfill({status:200,contentType:"image/jpeg",body:jpeg});
   if(path.endsWith("/end")){session.state="closed";session.video=video("complete");return reply(session);}
   if(session.state==="collecting")session.video=video("recording");
   return reply({session,samples:[],offset:0,limit:12,total:0});
  }
  if(path.endsWith("/analysis/profiles"))return reply({profiles:[]});
  if(path.endsWith("/analysis/runs"))return reply({runs:[]});
  return route.abort();
 });
 await page.goto("http://127.0.0.1:3313/");
 const bar=page.getByTestId("study-recording-bar"),record=page.getByTestId("study-record"),end=page.getByTestId("study-end");
 check(await bar.getByTestId("study-photo").count()===0,"header still has photo duplication");
 check(await bar.locator("details").count()===0,"header still has management details");
 await record.click();
 await page.getByTestId("study-video-status").waitFor();
 check((await page.getByTestId("study-video-status").textContent()).includes("Oczekiwanie"),"missing waiting-camera state");
 await page.getByTestId("fixture-samples").click();
 await page.getByTestId("samples-session").waitFor();
 check(await page.getByTestId("samples-session").locator("option").count()===2,"active recording missing from archive");
 check(await end.isEnabled(),"opening archive ended recording");
 check(await page.getByTestId("study-photo").count()===1,"sample tab lacks the single photo action");
 await page.getByTestId("study-photo").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-photo"]')?.disabled===false);
 check(photos.length===1&&photos[0].session_id===sessions[0].session_id&&photos[0].frame_key==="f".repeat(32),"photo lost displayed frame or active session");
 await page.getByTestId("fixture-control").click();
 check(await end.isEnabled(),"returning to control ended recording");
 check(await page.getByTestId("study-photo").count()===0,"photo control leaked into control screen");
 await page.getByTestId("fixture-samples").click();
 await end.click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-record"]')?.disabled===false);
 const first=sessions[0].session_id;
 await record.click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-end"]')?.disabled===false);
 await end.click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="study-record"]')?.disabled===false);
 check(sessions.length===2&&new Set(sessions.map(row=>row.session_id)).size===2,"second recording overwrote first");
 await page.waitForFunction(()=>document.querySelectorAll('[data-testid="samples-session"] option').length===3);
 await page.getByTestId("samples-refresh").click();
 await page.getByTestId("samples-session").selectOption(first);
 await page.getByTestId("archive-video-download").waitFor();
 check((await page.getByTestId("archive-video-download").getAttribute("href")).includes(first),"download points to another session");
 check((await page.getByTestId("samples-export").getAttribute("href")).includes(first),"export points to another session");
 await page.reload();
 await page.getByTestId("fixture-samples").click();
 await page.waitForFunction(()=>document.querySelectorAll('[data-testid="samples-session"] option').length===3);
 check(writes.every(row=>row.path===base+"/sessions"||row.path===base+"/capture"||row.path.endsWith("/end")),"unexpected actuator or camera write");
 return{result:"PASS",sessions:sessions.length,reloaded:true,archiveDuringRecording:true,videoOptIn:true,photoSaved:photos.length===1,robotContacted:false};
}
