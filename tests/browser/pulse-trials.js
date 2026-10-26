// Every Rider endpoint is intercepted before loading the UI; no real ARM or motor calls.
async page => {
  const base=new URL(page.url()).origin,check=(ok,message)=>{if(!ok)throw new Error(message);};
  check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback UI required");
  await page.goto("about:blank");
  const wait=async(fn,message)=>{const end=Date.now()+8000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,40));}};
  const sha="a".repeat(64),calls=[],saved=[];
  let observe=false,drive=false,motionActive=false,rejectSave=false,rejectResume=false,generation="settings-fixture";
  // Same incomplete ground calibration and saved drive mode as the deployed Pi.
  let policy={motion_limits:{version:1,max_motion_ms:500,max_speed:1,max_turn_ms:500,max_turn_speed:1},protocol_version:1,revision:53,mode:"drive",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.8,.65,.95],verified:true,calibration_id:"operator-fixture",model_sha256:sha,model_tuning_supported:true,perception:{engine:"depth_plane",model_id:"depth-anything-v2-indoor-small",residual_limit:.06,outlier_limit:.005,slope_min:2},avoidance:{enabled:false,protocol_version:3,sweep_verified:false,mirrored:false,turn_speed:.05,turn_ms:100,max_turn_pulses:3,max_stalled_corrections:2,progress_min_delta:.03,geometry:{mode:"ground",fx:null,fy:null,cx:.5,cy:.5,radius_ratio:null,lookahead_ratio:2,max_heading_deg:45}}};
  const settings={revision:1,directions:Object.fromEntries(["forward","backward","left","right"].map(key=>[key,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{display_factor:100,display_precision:0,display_unit:"%"}}},control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500}};
  settings.values={linear_speed:.6,turn_speed:.6,pulse_duration:.3,speed_limit:.6,linear_rate:.5,linear_deceleration:1,turn_rate:4,turn_deceleration:4,reversal_pause:0,ramp_mode:0};
  for(const [key,row] of Object.entries(settings.directions))Object.assign(row,{speed:.6,maximum:.6,duration:key==="forward"?.25:.3});
  settings.parameter_schema={direction_fields:Object.fromEntries(["speed","minimum","maximum","step","duration"].map(key=>[key,{type:"number",minimum:key==="duration"?.01:0,maximum:key==="duration"?.5:1,display_factor:key==="duration"?1000:100,display_unit:key==="duration"?"ms":"%",display_precision:2}])),value_fields:Object.fromEntries(["linear_rate","linear_deceleration","turn_rate","turn_deceleration","reversal_pause"].map(key=>[key,{minimum:key==="reversal_pause"?0:.001,maximum:key==="reversal_pause"?.25:100,display_factor:key==="reversal_pause"?1000:100,display_unit:key==="reversal_pause"?"ms":"%/s"}])),ramp_modes:[{value:0,id:"linear"},{value:1,id:"smooth_s"},{value:2,id:"logarithmic"}]};
  let run=null,currentCollection=null,collectionSerial=0,holder=null,holdTrial=false,delaySettings=false,settingsPending=false,delayRecording=false,recordingPending=false,changeOnRecording=false;
  const collections={},trialStarts=[];
  const abc=[{id:'A',version:1,count:1,pulse_ms:500,gap_ms:0},{id:'B',version:1,count:2,pulse_ms:250,gap_ms:100},{id:'C',version:1,count:5,pulse_ms:100,gap_ms:100}];
  const makeRun=body=>{const profile=abc.find(p=>p.id===body.trial_profile_id),events=[];let elapsed=0;for(let index=1;index<=profile.count;index++){events.push({kind:'pulse_started',pulse_index:index,elapsed_ms:elapsed});elapsed+=profile.pulse_ms;events.push({kind:'pulse_zero',pulse_index:index,elapsed_ms:elapsed});if(index<profile.count){events.push({kind:'gap_started',pulse_index:index,elapsed_ms:elapsed});elapsed+=profile.gap_ms;}}events.push({kind:'completed',pulse_index:profile.count,elapsed_ms:elapsed});return {version:1,run_id:body.request_id,profile_id:profile.id,profile_version:1,count:profile.count,pulse_ms:profile.pulse_ms,gap_ms:profile.gap_ms,direction:'forward',settings_revision:settings.revision,generation,group_id:'a'.repeat(64),common:{parameters:{speed:settings.directions.forward.speed},ramps:{linear_rate:settings.values.linear_rate,linear_deceleration:settings.values.linear_deceleration},context:body.trial_context},phase:holdTrial?'gap':'completed',reason:holdTrial?'running':'completed',pulse_index:holdTrial?1:profile.count,completed_pulses:holdTrial?1:profile.count,elapsed_ms:holdTrial?profile.pulse_ms:elapsed,events:holdTrial?events.slice(0,3):events,t:0,physical_completion:'unverified'};};
  const timeline=collection=>({schema_version:1,dataset_revision:'r-'+collection.session_id,session_id:collection.session_id,context:'motion-study-v1:wood',streams:[{id:'stream',device_id:'fixture',source_session:'settings-fixture',clock:'host-reception-monotonic',origin:'first_receipt',range:[0,1.5],points:[],events:[],outputs:[],profiles:{},trials:collection.run?[collection.run]:[]}],collection_status:{dropped:0,failed:0},counts:{imu_records:0},sampling:{},source_gaps_s:{p95:null}});
  const modelCatalog=()=>({models:[{id:"depth-anything-v2-indoor-small",label:"Depth V2",engine:"depth_plane",installation:"ready",installed:true,selectable:true,drive_supported:true,sha256:sha},{id:"depth-fixture-second",label:"Depth fixture second",engine:"depth_plane",installation:"ready",installed:true,selectable:true,drive_supported:true,sha256:"b".repeat(64)}],selection:{model_id:policy.perception.model_id,artifact_sha256:sha,revision:policy.revision},provider:"venom",pipeline_revision:1,providers:[{id:"venom",available:true}],local_models:[],diagnostics:{},switching:false});
  const safety=()=>({state:motionActive?"ARMED":"DISARMED",owner:motionActive?"manual":null,arming_mode:motionActive?"operator":null,moving:motionActive,operator_control:{version:1,state:motionActive?"active":"idle"},generation,navigation_journey_version:1,panel_motion_enabled:true,ready:true,can_move:false,stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder},pulse_trials:{version:1,current:run},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:policy,status:{running:observe||drive,stale:false,mode:drive?"drive":"observe",decision:{state:"clear",vision_action:"go",clear_count:3}},goals:{observe,drive},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,navigation_journey_version:1,ground_geometry_supported:true,supported_protocol_versions:[3]}});
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET"){
      const body=req.postDataJSON();calls.push({path,body});
      if(path==="/control"){check(body.action==="stop","unexpected actuation");motionActive=false;drive=false;if(run&&["pulse","gap"].includes(run.phase)){run={...run,phase:"cancelled",reason:"operator_stop"};if(currentCollection)currentCollection.run={...run};}generation="stopped-"+calls.length;return json({ok:true,data:{status:"executed",state:"DISARMED",generation,safety:safety()}});}
      if(path==="/settings"){settingsPending=true;if(delaySettings)await new Promise(resolve=>setTimeout(resolve,500));settingsPending=false;check(!observe&&!drive&&!motionActive,"manual settings written before idle");check(body.revision===settings.revision,"stale manual revision");Object.assign(settings.values,body.values);settings.directions=body.directions;settings.revision++;return json({ok:true,data:{...settings,persisted:true}});}
      if(path==='/samples/sessions'){
        check(body.policy.record_images===false&&body.policy.record_video===false,'trial started camera/video');recordingPending=true;
        if(delayRecording)await new Promise(resolve=>setTimeout(resolve,500));recordingPending=false;
        const id=String(++collectionSerial).repeat(32);currentCollection={session_id:id,state:'collecting',started_at:Date.now()/1000,source_device_id:'fixture',policy:body.policy};collections[id]=currentCollection;
        if(changeOnRecording){settings.revision++;settings.values.linear_rate=.8;changeOnRecording=false;}
        return json(currentCollection);
      }
      if(path.startsWith('/samples/sessions/')&&path.endsWith('/end')){const id=path.split('/')[3];collections[id].state='completed';return json(collections[id]);}
      if(path==='/motion/trials'){
        check(body.operator_consent===true&&body.panel_id&&body.owner==='manual'&&body.arming_mode==='operator','missing scoped consent');
        check(body.generation===generation&&body.settings_revision===settings.revision,'trial used stale settings/generation');
        const profile=abc.find(p=>p.id===body.trial_profile_id);check(settings.directions.forward.duration===profile.pulse_ms/1000,'trial ignored visible pulse');
        check(currentCollection?.state==='collecting','Start preceded recording');trialStarts.push(body);run=makeRun(body);currentCollection.run=run;holder=body.panel_id;motionActive=holdTrial;
        if(!holdTrial)generation='finished-'+trialStarts.length;
        return json({ok:true,meta:{},data:{confirmed:true,accepted:true,pulse_trial_id:run.run_id}});
      }
      if(path==="/perception/selection"){check(!observe&&!drive,"model changed while observing");check(body.revision===policy.revision,"model revision mismatch");policy={...policy,revision:policy.revision+1,verified:false,mode:"observe",perception:{...policy.perception,model_id:body.model_id}};return json({config:policy});}
      if(path==="/goal/s14_venom_navigation"){check(body.enabled===false,"save restarted navigation");drive=false;return json({ok:true,data:{ok:true}});}
      if(path==="/goal/s15_venom_perception"){
        if(body.enabled&&rejectResume)return json({detail:{code:"fixture_analysis_restart_failed"}},503);
        observe=body.enabled;return json({ok:true,meta:{},data:{ok:true}});
      }
      if(path==="/navigation/config"){
        check(!observe&&!drive,"saved before observation stopped");
        if(rejectSave)return json({detail:{code:"navigation_configuration_conflict"}},409);
        check(body.revision===policy.revision,"stale revision");
        check(body.values.mode==="observe"&&body.values.verified===false,"changed drive policy did not revoke drive mode");
        saved.push(body.values);policy={...body.values,motion_limits:{version:1,max_motion_ms:500,max_speed:1,max_turn_ms:500,max_turn_speed:1},protocol_version:1,revision:policy.revision+1};
        return json({ok:true,meta:{},data:{ok:true,persisted:true,config:policy}});
      }
      throw new Error("unexpected mutation: "+path);
    }
    if(path==='/motion/trials/profiles')return json({version:1,supported:true,profiles:abc});
    if(path.startsWith('/motion/trials/')){const id=path.split('/').at(-1),found=Object.values(collections).map(c=>c.run).find(r=>r?.run_id===id);return found?json({version:1,trial:found,stale:false}):json({detail:{code:'pulse_trial_not_found'}},404);}
    if(path==='/samples/sessions')return json({sessions:Object.values(collections)});
    if(path.startsWith('/samples/sessions/')){const id=path.split('/')[3];if(path.endsWith('/motion-timeline'))return json(timeline(collections[id]));return json({session:collections[id]});}
    if(path==="/perception/models")return json(modelCatalog());
    if(path==="/config")return json({devices:[{id:"fixture",label:"Fixture",mode:"demo"}],device_id:"fixture",session:"journey-settings-fixture"});
    if(path==="/status")return json({connection:{configured:true,status:"online",session:"journey-settings-fixture",device_id:"fixture",mode:"demo"},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},health:{},telemetry:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith("/navigation/"))return json(navigation());
    if(path==="/camera/pipeline")return json({contract_id:"rider.camera_pipeline",contract_version:1,supported:true,revision:1,state:"stopped",transport_epoch:1,active_profile:null,effective_stages:{},error:null,capabilities:{analysis_locations:["venom"],local_engines:["canny"],remote_transports:["snapshots"]},profile:{analysis_location:"venom",local_engine:"canny",remote_transport:"snapshots"}});
    if(path.startsWith("/camera/"))return json({detail:{code:"camera_off"}},404);
    return json({});
  });
  const scenarios=[];
  try{
    await page.setViewportSize({width:1366,height:768});await page.goto(base+'/rider');
    await page.getByTestId('pulse-profile').waitFor();
    check(calls.length===0,'opening panel actuated');
    await page.getByTestId('pulse-context').fill('wood');
    await page.getByTestId('pulse-profile').selectOption('A');
    check(await page.getByTestId('operator-manual-duration').inputValue()==='500','A did not prepare visible pulse');
    check(await page.getByTestId('operator-manual-speed').inputValue()==='0.6','A changed speed');
    check(await page.getByTestId('operator-linear_rate').inputValue()==='0.5','A changed ramps');
    check(calls.length===0,'selection wrote settings or started motion');
    await page.getByRole('tab',{name:'Kalibracja',exact:true}).click();await page.getByRole('tab',{name:'Sterowanie',exact:true}).click();
    await page.getByTestId('pulse-profile').waitFor();check(await page.getByTestId('pulse-profile').inputValue()==='A','tab switch lost recipe');
    const aBefore=calls.length;await page.getByTestId('pulse-start').click();
    await wait(()=>trialStarts.length===1&&collections['1'.repeat(32)]?.state==='completed','A did not complete recording');
    check(trialStarts[0].trial_profile_id==='A','wrong recipe submitted');
    check(calls.slice(aBefore).map(c=>c.path).filter(path=>path==='/settings'||path==='/samples/sessions'||path==='/motion/trials').join(',')==='/settings,/samples/sessions,/motion/trials','settings/recording/Start ordering wrong');
    check(await page.getByTestId('pulse-error').count()===0,'fast completed A was reported cancelled');
    scenarios.push('selection is draft-only, current values and tab persistence; fresh A Start saves then records and submits once');
    await page.getByTestId('pulse-profile').selectOption('C');await page.getByTestId('pulse-start').click();
    await wait(()=>trialStarts.length===2&&collections['2'.repeat(32)]?.state==='completed','C did not complete');
    check(trialStarts[1].trial_profile_id==='C'&&run.count===5,'C count or recipe lost');
    scenarios.push('C uses 5x100ms with common settings preserved');
    holdTrial=true;await page.getByTestId('pulse-profile').selectOption('B');await page.getByTestId('pulse-start').click();
    await wait(()=>trialStarts.length===3&&motionActive,'B not active');
    await page.getByTestId('rider-stop').click();
    await wait(()=>collections['3'.repeat(32)]?.state==='completed'&&run.phase==='cancelled','STOP did not end whole B');
    check(!motionActive,'STOP retained motion');
    scenarios.push('STOP during a series gap cancels remaining pulses and closes recording');
    holdTrial=false;delaySettings=true;await page.getByTestId('pulse-profile').selectOption('A');
    const starts=trialStarts.length;await page.getByTestId('pulse-start').click();await wait(()=>settingsPending,'delayed settings not exercised');await page.getByTestId('rider-stop').click();
    await wait(()=>!settingsPending,'late save did not return');await page.getByTestId('pulse-error').waitFor();
    check(trialStarts.length===starts,'late save started after STOP');delaySettings=false;
    scenarios.push('STOP during settings preparation prevents recording and Start after late save');
    await page.getByTestId('pulse-profile').selectOption('B');delayRecording=true;
    await page.getByTestId('pulse-start').click();await wait(()=>recordingPending,'recording preparation not pending');await page.getByTestId('rider-stop').click();
    await wait(()=>!recordingPending&&collections['4'.repeat(32)]?.state==='completed','cancelled preparation left recording open');
    check(trialStarts.length===starts,'late recording started motion after STOP');delayRecording=false;
    scenarios.push('STOP during recording preparation prevents ARM and cleans up the new recording');
    await page.getByRole('tab',{name:'Kalibracja',exact:true}).click();
    await page.getByTestId('study-session-A').selectOption('1'.repeat(32));await page.getByTestId('study-session-B').selectOption('2'.repeat(32));
    await page.getByTestId('pulse-series-comparison').waitFor();
    await wait(async()=>await page.getByTestId('pulse-series-A').count()===1&&await page.getByTestId('pulse-series-B').count()===1,'whole series comparison absent');
    check((await page.getByTestId('study-trial-A').inputValue()).startsWith('series:'),'A selector does not show whole series alignment');
    check((await page.getByTestId('study-trial-B').inputValue()).startsWith('series:'),'B selector does not show whole series alignment');
    check((await page.getByTestId('pulse-series-A').innerText()).includes('500'),'A comparison lost pulse duration');
    check((await page.getByTestId('pulse-series-B').innerText()).includes('5/5'),'C comparison shows individual pulse instead of series');
    check((await page.getByTestId('pulse-group-match').innerText()).includes('Zgodne'),'same common settings reported different');
    await page.getByTestId('pulse-series-comparison').scrollIntoViewIfNeeded();await page.screenshot({path:'test-results/browser-regressions/pulse-series-comparison.png'});
    scenarios.push('existing archive comparison selects whole A/C series and renders counts, durations and common settings');
    await page.getByRole('tab',{name:'Sterowanie',exact:true}).click();await page.getByTestId('pulse-profile').waitFor();await page.getByTestId('pulse-profile').scrollIntoViewIfNeeded();
    check(await page.getByTestId('pulse-profile').locator('xpath=ancestor::details').count()===0,'profiles hidden in accordion');
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'laptop overflow');
    await page.screenshot({path:'test-results/browser-regressions/pulse-profiles-laptop.png'});await page.setViewportSize({width:390,height:844});
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile overflow');
    check(calls.every(c=>c.path!=='/control'||c.body.action==='stop'),'fixture issued unexpected actuation');
    return {fixtureOnly:true,passed:scenarios,trialStarts:trialStarts.length,profiles:trialStarts.map(b=>b.trial_profile_id)};
  }finally{await page.goto('about:blank');await page.unrouteAll({behavior:'wait'});}
}
