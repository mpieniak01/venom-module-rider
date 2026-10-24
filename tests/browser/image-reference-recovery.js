// Every HTTP request is intercepted. All saves stay in the local fixture.
async page=>{
 const check=(ok,message)=>{if(!ok)throw new Error(message);};
 page.setDefaultTimeout(8000);
 const bounded=async(p,label)=>{let timer;try{return await Promise.race([p,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),8000);})]);}finally{clearTimeout(timer);}};
 let reads=0,releaseRead,secondDone,readReady;
 const secondResponse=new Promise(resolve=>{secondDone=resolve;});
 const thirdRequest=new Promise(resolve=>{readReady=resolve;});
 const reference={version:1,revision:7,purpose:"visual_reference_only",values:{visible:true,frame_size:[320,240],track_width_cm:16,robot_width_cm:11.5,camera_height_cm:14,reference_pose:"lowest",left_edge:[[.39,.65],[.29,1]],right_edge:[[.58,.65],[.74,1]],markers:[[18,1],[60,.65]]}};
 await page.route("**/api/**",route=>route.abort());
 await page.route("**/api/v1/rider/navigation/image-reference",async route=>{
  check(route.request().method()==="GET","unexpected HTTP mutation");reads++;
  if(reads<3){await route.fulfill({status:422,contentType:"application/json",body:'{"detail":"fixture_unavailable"}'});if(reads===2)secondDone();return;}
  await new Promise(resolve=>{releaseRead=resolve;readReady();});
  return route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({meta:{envelope_version:1},ok:true,data:reference})});
 });
 await page.goto("http://127.0.0.1:3311/");
 await page.getByTestId("reference-read-error").filter({hasText:"reference.unavailable"}).waitFor();
 await page.getByTestId("image-reference-toggle").click();
 await bounded(secondResponse,"second read did not complete");
 await page.getByTestId("reference-retry").waitFor();
 check(reads===2,"opening did not retry the failed read");
 check(await page.getByTestId("reference-has-draft").textContent()==="false","local draft created without device state");
 await page.getByTestId("reference-retry").click();
 await bounded(thirdRequest,"third read did not start");
 await page.waitForFunction(()=>document.querySelector('[data-testid="reference-read-error"]').textContent==="");
 check(reads===3,"retry did not request current state");
 check(await page.getByTestId("reference-has-draft").textContent()==="false","draft created while read pending");
 releaseRead();
 await page.getByTestId("reference-track_width_cm").waitFor();
 check(await page.getByTestId("reference-track_width_cm").inputValue()==="16","saved dimensions lost on retry");
 await page.getByTestId("reference-camera_height_cm").fill("15");
 await page.getByTestId("reference-save").click();
 await page.getByRole("dialog").waitFor({state:"hidden"});
 const saves=await page.evaluate(()=>window.referenceFixtureSaves);
 check(saves.length===1&&saves[0].revision===7,"recovery lost the device CAS revision");
 check(saves[0].values.camera_height_cm===15,"new measured height not saved");
 check(saves[0].values.track_width_cm===16,"retry overwrote other measurements");
 return{result:"PASS",reads,saves:saves.length,robotContacted:false};
}