// Only the isolated motion-study fixture; never a physical robot panel.
async page=>{
 const check=(v,m)=>{if(!v)throw new Error(m);};
 const origin=new URL(page.url()).origin;
 check(new URL(origin).port==="18798","Fixture port required");
 const f=await(await page.request.get(origin+"/fixture")).json();
 check(f.config.devices.every(d=>d.mode==="demo"),"Demo devices required");
 if(await page.getByTestId("study-end").isEnabled())await page.getByTestId("study-end").click();
 await page.getByTestId("study-record").click();
 const list=await(await page.request.get(origin+"/api/v1/rider/samples/sessions")).json();
 const active=list.sessions.filter(s=>s.state==="collecting");
 check(active.length===1,"Exactly one session");const sid=active[0].session_id;
 check(active[0].policy.record_images===true,"Automatic images enabled");
 const cfg=await(await page.request.get(origin+"/api/v1/rider/config")).json();
 const on=await page.request.post(origin+"/api/v1/rider/goal/s2_camera_preview",{headers:{"X-Rider-Session":cfg.session},data:{enabled:true}});
 check(on.ok(),"Fixture camera on");
 await page.getByRole("tab",{name:"Pomiary",exact:true}).click();
 let data;
 for(let n=0;n<30;n++){
  data=await(await page.request.get(origin+"/api/v1/rider/samples/sessions/"+sid)).json();
  if(data.samples.length>=2)break;
  await new Promise(resolve=>setTimeout(resolve,400));
 }
 check(data.samples.length>=2,"Images persisted outside camera tab");
 check(data.session.state==="collecting","Same active session");
 check(await page.getByTestId("sample-capture").count()===0,"No competing collection controls");
 await page.getByTestId("study-end").click();
 const closed=await(await page.request.get(origin+"/api/v1/rider/samples/sessions/"+sid)).json();
 check(closed.session.state==="closed","Unified stop closes recording");
 return {session:sid,images:closed.session.counts.saved,evidence:closed.session.evidence};
}
