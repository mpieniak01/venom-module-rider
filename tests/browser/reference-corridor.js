// The actual workspace receives an inert saved-reference response; no Pi.
async page=>{
 const base=new URL(page.url()).origin,requests=[];
 let referenceReads=0,referenceAvailable=true;
 const check=(v,m)=>{if(!v)throw new Error(m);};
 const reference={version:1,revision:2,purpose:"visual_reference_only",values:{visible:true,frame_size:[320,240],track_width_cm:16,robot_width_cm:11.5,camera_height_cm:14,reference_pose:"lowest",left_edge:[[120/320,166/240],[87/320,239/240]],right_edge:[[190/320,166/240],[233/320,239/240]],markers:[[18,1],[20,227/240],[40,181/240],[60,160/240]]}};
 const collision={version:1,far_width_ratio:1,left_shift:.3,right_shift:.3,end_fraction:.2,reference_corridor:[.35,.65,.65,.95],guidance:{version:1,motion_mode:"rotate_then_drive",center_deadband:.1,rotation_bounds:[.2,.88,.8,.95]}};
 const sha="a".repeat(64),session="reference-fixture";
 const policy={revision:1,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2},verified:true,calibration_id:"fixture",model_sha256:sha,collision_geometry_supported:true,axis_guidance_supported:true,avoidance:{protocol_version:3,enabled:true,sweep_verified:true,mirrored:false,turn_speed:.05,turn_ms:100,max_turn_pulses:3,max_stalled_corrections:2,progress_min_delta:.03,geometry:{mode:"image",collision}}};
 const safety={state:"DISARMED",owner:null,generation:"fixture",last_seq:0,panel_motion_enabled:true,can_move:false,ready:true,stale:false,observed_at:Date.now()/1000,output:{lx:0,az:0}};
 const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(d=>[d,{speed:.2,duration:.25}]))};
 const detector={configured:true,loaded:true,sha256:sha,drive_supported:true,supported_protocol_versions:[1,2,3],collision_geometry_v1:true,axis_guidance_v1:true};
 const nav={config:policy,status:{running:false,stale:false,mode:"observe",decision:{}},goals:{drive:false,observe:false},runtime:{drive:true,observe:true,known:true},detector};
 await page.route("**/api/v1/rider/**",async route=>{
  const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
  const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
  if(req.method()!=="GET"){requests.push(path);return json({error:"unexpected mutation"},500);}
  if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session});
  if(path==="/status")return json({connection:{configured:true,status:"online",session,device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety},telemetry:{},health:{},resources:{},catalog:{features:[]},logic:{},face:{}});
  if(path==="/navigation/image-reference"){referenceReads++;return json({meta:{envelope_version:1},ok:true,data:referenceAvailable?reference:{...reference,values:null}});}
  if(path.startsWith("/navigation/"))return json(nav);
  if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,revision:1,profile:{analysis_location:"venom",local_engine:"canny",remote_transport:"mjpeg"},state:"stopped",transport_epoch:1,camera_session:null,capabilities:{analysis_locations:["venom","local"],local_engines:["canny"],remote_transports:["snapshots","mjpeg"]},effective_stages:{},latest_frame:null,error:null});
  if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
  return json({});
 });
 await page.goto(base+"/rider");
 await page.getByTestId("workspace-automatic").click();
 const section=page.locator("details").filter({has:page.getByTestId("ground-geometry-settings")});
 await section.locator(":scope > summary").click();
 const button=page.getByTestId("collision-use-reference");
 await button.waitFor();
 await button.click();
 const corridor=section.getByRole("textbox",{name:"Strefa zatrzymania: x0,y0,x1,y1 (0–1)",exact:true});
 try{await page.waitForFunction(()=>[...document.querySelectorAll("input")].some(e=>e.value==="0.320263,0.8,0.675029,0.95"),null,{timeout:5000});}
 catch{throw new Error("reference read count="+referenceReads+" inputs="+JSON.stringify(await section.locator("input").evaluateAll(es=>es.map(e=>e.value)))+" alerts="+JSON.stringify(await page.locator('[role="alert"]').allTextContents()));}
 check(await corridor.inputValue()==="0.320263,0.8,0.675029,0.95","saved reference did not update the actual navigation draft");
 check(Math.abs(Number(await page.getByTestId("collision-far_width_ratio").inputValue())-81.1906)<.000001,"perspective taper was not applied");
 referenceAvailable=false;
 await button.click();
 await page.getByRole("alert").filter({hasText:"Brak zapisanej referencji"}).waitFor();
 check(await corridor.inputValue()==="0.320263,0.8,0.675029,0.95","missing reference replaced the current draft");
 check(requests.length===0,"deriving a draft sent a mutation");
 check(policy.verified&&policy.avoidance.sweep_verified,"Pi fixture mutated before apply");
}
