// All Rider HTTP requests are intercepted; there is no real robot or ARM.
async page => {
  const base=new URL(page.url()).origin,check=(value,message)=>{if(!value)throw new Error(message);};
  check(['localhost','127.0.0.1','[::1]'].includes(new URL(base).hostname),'loopback fixture required');
  await page.goto('about:blank');
  const wait=async(fn,message)=>{const end=Date.now()+9000;while(!await fn()){check(Date.now()<end,message);await new Promise(resolve=>setTimeout(resolve,40));}};
  const sha='a'.repeat(64),calls=[];
  let armed=true,owner='manual',operator='paused',producer=true,generation='fixture-0',serial=0,cancelResume=true;
  const advance=()=>generation='fixture-'+(++serial);
  const settings={revision:1,control_modes:{version:1,supported:["pulse","hold","release"],hold_renewal_ms:150,hold_timeout_ms:500,click_threshold_ms:180,request_ttl_ms:500},values:{linear_speed:.2,turn_speed:.2,pulse_duration:.25,speed_limit:1,linear_rate:.5,linear_deceleration:1,turn_rate:4,turn_deceleration:4,reversal_pause:0,ramp_mode:0},directions:Object.fromEntries(['forward','backward','left','right'].map(name=>[name,{speed:.2,duration:.25,minimum:0,maximum:1,step:.01}])),parameter_schema:{direction_fields:{speed:{minimum:0,maximum:1}},value_fields:Object.fromEntries(['linear_rate','linear_deceleration','turn_rate','turn_deceleration','reversal_pause'].map(key=>[key,{minimum:key==='reversal_pause'?0:.001,maximum:key==='reversal_pause'?.25:100,display_factor:1}])),ramp_modes:[{value:0,id:'linear'}]}};
  const policy={revision:1,mode:'drive',speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95],perception:{engine:'depth_plane'},verified:true,calibration_id:'operator-fixture',model_sha256:sha};
  const safety=()=>({state:armed?'ARMED':'DISARMED',owner,generation,moving:false,last_seq:0,panel_motion_enabled:true,can_move:armed,ready:true,stale:false,observed_at:Date.now()/1000,capabilities:{move:{available:true}},panel_control:{version:1,holder:null},operator_control:{version:1,state:operator,operation_id:'fixture',generation},remote_decision_deadline_supported:true,scoped_stop_generation_supported:true});
  const navigation=()=>({config:policy,status:{running:producer,stale:false,mode:'drive',run_id:'fixture-persistent',operator_control_version:1,decision:{state:'clear',clear_count:3,action:'go',vision_action:'go',reason:'clear_frames_ready'}},goals:{drive:producer,observe:false},runtime:{drive:true,observe:true,known:true},detector:{configured:true,loaded:true,sha256:sha,drive_supported:true,clear_frames_required:3}});
  await page.route('**/api/v1/rider/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname.slice('/api/v1/rider'.length);
    const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    if(request.method()!=='GET'){
      const body=request.postDataJSON();calls.push({path,body});
      if(path==='/control'){
        if(body.action==='navigation_resume'&&cancelResume)return json({detail:{code:'navigation_trial_cancelled'}},409);
        if(body.action==='stop'){armed=false;owner=null;operator='idle';advance();}
        else if(body.action==='navigation_resume'){check(armed&&!body.operator_consent&&!body.operator_plan,'warm resume renewed or lost consent');owner='navigation';operator='active';advance();}
        else throw new Error('unexpected control '+body.action);
        return json({ok:true,data:{confirmed:true,status:'executed',safety:safety()}});
      }
      if(path==='/goal/s14_venom_navigation'){check(body.enabled===false&&!armed,'goal changed before STOP');producer=false;return json({ok:true,data:{ok:true}});}
      if(path==='/settings'){check(!armed&&!producer&&body.revision===settings.revision,'settings before idle or stale');Object.assign(settings.values,body.values);settings.directions=body.directions;settings.revision++;return json({ok:true,data:{...settings,persisted:true}});}
      if(path==='/navigation/trial'){check(!armed&&!producer&&body.settings_revision===settings.revision&&body.operator_consent===true&&body.operator_plan&&body.panel_id,'fresh trial omitted applied settings or consent');armed=true;producer=true;owner='navigation';operator='active';advance();return json({ok:true,data:{ok:true,config:policy}});}
      throw new Error('unexpected mutation '+path);
    }
    if(path==='/config')return json({devices:[{id:'fixture',label:'Fixture',mode:'demo'}],device_id:'fixture',session:'start-settings-fixture'});
    if(path==='/status')return json({connection:{configured:true,status:'online',session:'start-settings-fixture',device_id:'fixture',mode:'demo'},polling:{control_ms:100},settings,errors:{},control:{server_time:Date.now()/1000,safety:safety()},telemetry:{},health:{},resources:{},catalog:{features:[]},logic:{},face:{}});
    if(path.startsWith('/navigation/'))return json(navigation());
    if(path==='/camera/pipeline')return json({supported:false});
    if(path.startsWith('/camera/'))return json({detail:{code:'camera_off'}},404);
    return json({});
  });
  try{
    await page.goto(base+'/rider');await page.getByTestId('workspace-automatic').click();
    await wait(async()=>await page.getByTestId('loop-start').isEnabled(),'unchanged warm Start unavailable');
    await page.getByTestId('loop-start').click();
    await wait(()=>calls.length===1,'cancelled resume not requested');
    await wait(async()=>await page.getByTestId('loop-start').isEnabled(),'cancelled operation did not release Start');
    check(!(await page.locator('body').innerText()).includes('navigation_trial_cancelled'),'expected cancellation persisted as an error');
    cancelResume=false;calls.length=0;
    await page.getByTestId('loop-start').click();
    await wait(async()=>(await page.getByTestId('loop-state').innerText()).includes('Automat steruje'),'warm resume not confirmed');
    check(calls.length===1&&calls[0].body.action==='navigation_resume','unchanged Start stopped or renewed consent');
    owner='manual';operator='paused';advance();
    await wait(async()=>(await page.getByTestId('loop-state').innerText()).includes('Automat w pauzie'),'paused state not observed');
    await page.getByTestId('operator-linear_rate').fill('0.7');
    await wait(async()=>await page.getByTestId('loop-start').isEnabled(),'apply-and-start unavailable');
    const changedStart=calls.length;await page.getByTestId('loop-start').click();
    await wait(()=>calls.slice(changedStart).some(call=>call.path==='/navigation/trial'),'changed settings tried warm resume after disarming');
    check(settings.values.linear_rate===.7,'Start did not apply changed ramp');
    check(!calls.slice(changedStart).some(call=>call.body.action==='navigation_resume'),'changed Start reused stale warm consent');
    check(calls.slice(changedStart).map(call=>call.path).join(',')==='/control,/goal/s14_venom_navigation,/settings,/navigation/trial','cold Start ordering changed');
    return {fixtureOnly:true,scenarios:['unchanged settings retain warm resume without new consent','changed settings stop, save and obtain one fresh trial'],mutations:calls.map(call=>({path:call.path,action:call.body.action}))};
  }catch(error){throw new Error(error.message+' | '+JSON.stringify({calls,operator,armed,producer,state:await page.getByTestId('loop-state').innerText(),text:(await page.locator('body').innerText()).slice(-3000)}));}finally{await page.goto('about:blank');await page.unrouteAll({behavior:'wait'});}
}
