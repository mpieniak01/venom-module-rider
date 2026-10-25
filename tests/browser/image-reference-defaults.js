// Inert fixture: no robot requests, and persistence is a local mock only.
async page=>{
 const check=(ok,message)=>{if(!ok)throw new Error(message);};
 page.setDefaultTimeout(8000);
 let releaseRead,readReady;
 const started=new Promise(resolve=>{readReady=resolve;});
 await page.route("**/api/**",route=>route.abort());
 await page.route("**/api/v1/rider/navigation/image-reference",async route=>{
  check(route.request().method()==="GET","unexpected HTTP write");
  await new Promise(resolve=>{releaseRead=resolve;readReady();});
  return route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({meta:{envelope_version:1},ok:true,data:{version:1,revision:0,purpose:"visual_reference_only",values:null}})});
 });
 const target=new URL("#scaled",page.url()).href;
 await page.goto("about:blank");
 await page.goto(target);
 let timeout;
 try{await Promise.race([started,new Promise((_,reject)=>{timeout=setTimeout(()=>reject(new Error("initial read missing")),8000);})]);}finally{clearTimeout(timeout);}
 const overlay=page.getByTestId("image-reference-overlay");
 await overlay.waitFor();
 check((await overlay.textContent()).includes("Domyślne pomiary"),"default reference was not labelled");
 check((await overlay.textContent()).includes("14 cm"),"camera height missing on image");
 check(await overlay.getAttribute("viewBox")==="0 0 320 240","source geometry changed for scaled 4:3 image");
 check(await page.getByTestId("reference-has-draft").textContent()==="false","viewing created a draft");
 releaseRead();
 await page.getByTestId("reference-revision").filter({hasText:"0"}).waitFor();
 await page.getByTestId("image-reference-toggle").click();
 for(const [key,value]of [["track_width_cm","16"],["robot_width_cm","11.5"],["camera_height_cm","14"]])check(await page.getByTestId("reference-"+key).inputValue()===value,"initial field missing: "+key);
 for(const [index,value]of ["18","20","40","60"].entries())check(await page.getByTestId("reference-marker-"+index).inputValue()===value,"initial distance missing");
 check(await page.evaluate(()=>window.referenceFixtureSaves.length)===0,"opening saved defaults automatically");
 check((await page.getByTestId("reference-source").textContent()).includes("Wartości początkowe"),"opening relabelled unchanged defaults");
 check((await overlay.textContent()).includes("Domyślne pomiary"),"opening lost default overlay label");
 await page.getByTestId("reference-camera_height_cm").fill("15");
 check((await page.getByTestId("reference-source").textContent()).includes("czekają"),"changed draft was not labelled");
 await page.getByTestId("reference-camera_height_cm").fill("14");
 check((await page.getByTestId("reference-source").textContent()).includes("Wartości początkowe"),"reverting did not restore default source");
 check((await overlay.textContent()).includes("Domyślne pomiary"),"reverting did not restore default overlay");
 await page.getByTestId("reference-save").click();
 await page.getByRole("dialog").waitFor({state:"hidden"});
 const saves=await page.evaluate(()=>window.referenceFixtureSaves);
 check(saves.length===1&&saves[0].revision===0,"default save lost revision zero");
 check(saves[0].values.camera_height_cm===14&&saves[0].values.markers.length===4,"default values not persisted by explicit save");
 return{result:"PASS",defaults:[16,11.5,14,18,20,40,60],scaledFrame:[640,480],saves:saves.length,robotContacted:false};
}
