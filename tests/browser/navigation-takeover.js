// Playwright CLI UI regression; every Rider request is intercepted.
// This checks ownership and cancellation. It never contacts physical hardware.
async (page) => {
  const runScenario = async (page, cancelDuringTakeover) => {
 await page.goto("about:blank");
 await page.unroute("**/api/v1/rider/**");
 const check=(ok,message)=>{if(!ok)throw new Error(message);};
 const sha="a".repeat(64), calls=[];
 let policy={revision:1,protocol_version:1,mode:"observe",speed:.1,max_motion_ms:150,decision_budget_ms:300,corridor:[.35,.65,.65,.95],perception:{engine:"depth_plane",residual_limit:.06,outlier_limit:.005,slope_min:2},verified:false,calibration_id:"",model_sha256:""};
 let observing=true,driving=false,armed=true,generation="fixture-initial",owner="manual";
 const detector={configured:true,loaded:true,sha256:sha,model:"depth-fixture",purpose:"relative_ground_geometry",drive_supported:true,clear_frames_required:3};
 const settings={"schema_version": 2, "revision": 0, "values": {"linear_speed": 0.2, "turn_speed": 0.35, "pulse_duration": 0.25, "speed_limit": 1.0, "linear_rate": 3.0, "turn_rate": 4.0, "linear_deceleration": 3.0, "turn_deceleration": 4.0, "reversal_pause": 0.0, "ramp_mode": 0}, "directions": {"forward": {"minimum": 0.0, "maximum": 1.0, "step": 0.01, "speed": 0.2, "duration": 0.25}, "backward": {"minimum": 0.0, "maximum": 1.0, "step": 0.01, "speed": 0.2, "duration": 0.25}, "left": {"minimum": 0.0, "maximum": 1.0, "step": 0.01, "speed": 0.35, "duration": 0.25}, "right": {"minimum": 0.0, "maximum": 1.0, "step": 0.01, "speed": 0.35, "duration": 0.25}}, "profiles": {}, "active_profile": null, "contract_id": "rider.manual_settings", "contract_version": 2, "limits": {"linear_speed": 1.0, "turn_speed": 1.0, "pulse_duration": 1.0, "speed_limit": 1.0, "linear_rate": 100.0, "turn_rate": 100.0, "linear_deceleration": 100.0, "turn_deceleration": 100.0, "reversal_pause": 0.25, "ramp_mode": 2}, "actuation": false, "units": "normalized_command", "legacy_units_scope": "command_magnitude_only; use parameter_schema for individual fields", "parameter_schema": {"version": 1, "actuator_fields": {"height": {"type": "integer", "unit": "sdk_height_command", "display_unit": "SDK", "display_factor": 1, "display_precision": 0, "minimum": 75, "maximum": 115, "step": 1, "meaning": "height_command_not_physical_measurement"}, "balance": {"type": "boolean", "unit": "enabled", "display_unit": "", "meaning": "roll_compensation_not_full_balance_shutdown"}}, "ramp_modes": [{"value": 0, "id": "linear", "label_pl": "Liniowy", "label_en": "Linear"}, {"value": 1, "id": "smooth_s", "label_pl": "\u0141agodny S", "label_en": "Smooth S"}, {"value": 2, "id": "logarithmic", "label_pl": "Logarytmiczny", "label_en": "Logarithmic"}], "direction_fields": {"speed": {"type": "number", "unit": "normalized_command", "display_unit": "%", "display_factor": 100, "minimum": 0, "maximum": 1, "display_precision": 1, "meaning": "command_magnitude_not_physical_speed"}, "minimum": {"type": "number", "unit": "normalized_command", "display_unit": "%", "display_factor": 100, "minimum": 0, "maximum": 1, "display_precision": 1, "meaning": "operator_defined_minimum_nonzero_command"}, "maximum": {"type": "number", "unit": "normalized_command", "display_unit": "%", "display_factor": 100, "minimum": 0, "maximum": 1, "display_precision": 1, "meaning": "profile_command_ceiling"}, "step": {"type": "number", "unit": "normalized_command", "display_unit": "%", "display_factor": 100, "minimum": 0, "maximum": 1, "display_precision": 3, "meaning": "manual_adjustment_step", "exclusive_minimum": true}, "duration": {"type": "number", "unit": "s", "display_unit": "ms", "display_factor": 1000, "minimum": 0.01, "maximum": 1.0, "display_precision": 0, "meaning": "whole_click_pulse"}}, "value_fields": {"linear_rate": {"type": "number", "unit": "normalized_command/s", "display_unit": "%/s", "display_factor": 100, "minimum": 0.001, "maximum": 100.0, "display_precision": 1, "meaning": "linear_rate"}, "turn_rate": {"type": "number", "unit": "normalized_command/s", "display_unit": "%/s", "display_factor": 100, "minimum": 0.001, "maximum": 100.0, "display_precision": 1, "meaning": "turn_rate"}, "linear_deceleration": {"type": "number", "unit": "normalized_command/s", "display_unit": "%/s", "display_factor": 100, "minimum": 0.001, "maximum": 100.0, "display_precision": 1, "meaning": "linear_deceleration"}, "turn_deceleration": {"type": "number", "unit": "normalized_command/s", "display_unit": "%/s", "display_factor": 100, "minimum": 0.001, "maximum": 100.0, "display_precision": 1, "meaning": "turn_deceleration"}, "reversal_pause": {"type": "number", "unit": "s", "display_unit": "ms", "display_factor": 1000, "minimum": 0, "maximum": 0.25, "display_precision": 0, "meaning": "pause_before_reversal"}}, "constraints": [{"id": "direction_range", "rule": "minimum <= maximum"}, {"id": "direction_speed", "rule": "speed == 0 or minimum <= speed <= maximum"}, {"id": "direction_step", "rule": "step > 0; zero-width range permits a disabled slider"}, {"id": "pulse_minimum", "rule": "minimum <= duration / (1/acceleration + 1/deceleration)"}]}, "control_modes": {"version": 1, "supported": ["pulse", "hold", "release"], "default": "pulse", "directions": ["forward", "backward", "left", "right"], "hold_renewal_ms": 150, "hold_timeout_ms": 500, "click_threshold_ms": 180, "request_ttl_ms": 500, "replacement": "latest_valid_sequence_wins", "release": "decelerate_keep_armed", "release_timeout_ms": 500, "stop": "immediate_disarm", "hold_permission": "operator_manual_panel", "settings_change": "idle_only", "command": {"endpoint": "/api/control", "action": "move", "fields": ["direction", "mode", "device_settings", "settings_revision"]}}, "authority": "device", "profile_modified": false, "legacy_aliases": {"linear_speed": "directions.forward.speed", "turn_speed": "directions.right.speed", "pulse_duration": "maximum_direction_duration"}};
 const config={devices:[{id:"fixture-demo",label:"Fixture",mode:"demo"}],device_id:"fixture-demo",session:"fixture-session"};
 const state=()=>({goals:{observe:observing,drive:driving},status:{running:observing||driving,stale:!observing&&!driving,mode:driving?"drive":"observe",run_id:"fixture-run",decision:{state:"clear",clear_count:4,vision_action:"go",action:driving?"go":"stop",motion_reason:driving?"clear_frames_ready":"observe_only",config_revision:policy.revision,perception_engine:"depth_plane",valid_for_ms:150}},detector});
 await page.route("**/api/v1/rider/**",async route=>{
  const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
  const json=body=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(body)});
  if(req.method()!=="GET"){
   const body=req.postDataJSON();calls.push({path,body});
   if(path==="/navigation/config"){check(body.revision===policy.revision,"revision conflict");policy={...policy,...body.values,revision:policy.revision+1};return json({meta:{},ok:true,data:{ok:true,config:policy}});}
   if(path==="/goal/s15_venom_perception"){observing=body.enabled;return json({ok:true,data:{ok:true}});}
   if(path==="/goal/s14_venom_navigation"){if(body.enabled)check(armed&&owner==="navigation"&&body.source==="navigation"&&body.generation===generation&&Number.isInteger(body.seq),"navigation goal envelope missing");driving=body.enabled;return json({ok:true,data:{ok:true}});}
   if(path==="/control"){
    check(["arm","stop","disarm"].includes(body.action),"unexpected actuator action");
    if(body.action==="arm"){check(!armed,"already_armed_disarm_first");check(body.owner==="navigation"&&body.lease_s===10,"bounded navigation ARM required");armed=true;owner="navigation";generation="fixture-nav";}
    else{armed=false;owner=null;driving=false;generation="fixture-stopped";if(cancelDuringTakeover&&body.action==="disarm")await new Promise(r=>setTimeout(r,500));}
    return json({ok:true,data:{ok:true}});
   }
   await route.abort();throw new Error("Blocked unexpected mutation "+path);
  }
  if(path==="/config")return json(config);
  if(path==="/navigation/config")return json({...state(),config:policy,runtime:{observe:true,drive:true,known:true}});
  if(path==="/navigation/state")return json(state());
  if(path==="/status"){const now=Date.now()/1000;return json({connection:{status:"online",configured:true,device_id:"fixture-demo",mode:"demo",session:"fixture-session"},polling:{control_ms:300},errors:{},settings,control:{server_time:now,safety:{state:armed?"ARMED":"DISARMED",owner:armed?owner:null,generation,observed_at:now,stale:false,can_move:armed,moving:driving,panel_motion_enabled:false,ready:true,capabilities:{move:{available:true}},remote_decision_deadline_supported:true}},telemetry:{camera:{on:observing||driving}},health:{camera:{on:observing||driving}},catalog:{features:[{name:"s14_venom_navigation",can_move:true,control_owner:"navigation"},{name:"s15_venom_perception",can_move:false}]},logic:{},resources:{},history:{},face:{},system:{}});}
  if(path.startsWith("/camera/"))return route.fulfill({status:200,body:Buffer.from("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCADwAUADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwBKKKK5DkCiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooA//Z","base64"),contentType:"image/jpeg",headers:{"X-Rider-Stale":"false","X-Rider-Frame-Id":"fixture-"+Date.now(),"X-Rider-File-Observed-At":String(Date.now()/1000),"X-Rider-Vision-State":"clear","X-Rider-Vision-Reason":"depth_plane_candidate","X-Rider-Vision-Policy":"pi_config","X-Rider-Source":"fixture"}});
  return json({});
 });
 await page.goto("http://localhost:3300/rider");await page.bringToFront();
 await page.getByTestId("trial-accept").waitFor();
 check(await page.getByTestId("loop-start").isDisabled(),"trial must wait for accepted parameters");
 await page.getByTestId("trial-accept").click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="trial-accept"]')===null);
 check(policy.verified&&policy.model_sha256===sha&&policy.calibration_id.startsWith("operator-"),"model-bound acceptance missing");
 check(!calls.some(c=>c.path==="/control"),"saving acceptance must not ARM or move");
 await page.getByTestId("loop-start").click();
 if(cancelDuringTakeover){
  const until=Date.now()+5000;
  while(!calls.some(c=>c.path==="/control"&&c.body.action==="disarm")&&Date.now()<until)await new Promise(r=>setTimeout(r,10));
  check(calls.some(c=>c.path==="/control"&&c.body.action==="disarm"),"takeover did not start");
  await page.getByTestId("loop-stop").click();
  const stopped=Date.now()+5000;
  while((driving||armed)&&Date.now()<stopped)await new Promise(r=>setTimeout(r,10));
  check(!calls.some(c=>c.path==="/control"&&c.body.action==="arm"),"STOP must cancel pending ARM");
  check(!calls.some(c=>c.path==="/goal/s14_venom_navigation"&&c.body.enabled),"STOP must cancel the navigation goal");
  return {result:"PASS",test:"STOP during transfer cancels navigation ARM",calls};
 }

 const activeUntil=Date.now()+5000;
 while(!driving&&Date.now()<activeUntil)await new Promise(r=>setTimeout(r,10));
 check(driving&&armed&&owner==="navigation"&&policy.mode==="drive","trial did not transfer navigation authority");
 const disarm=calls.findIndex(c=>c.path==="/control"&&c.body.action==="disarm"),arm=calls.findIndex(c=>c.path==="/control"&&c.body.action==="arm");
 check(disarm>=0&&arm>disarm,"manual ARM must be released before navigation ARM");
 check(calls.some(c=>c.path==="/control"&&c.body.action==="arm"&&c.body.lease_s===10),"timed ARM missing");
 await page.getByTestId("loop-stop").click();
 const stopUntil=Date.now()+5000;
 while((driving||armed)&&Date.now()<stopUntil)await new Promise(r=>setTimeout(r,10));
 check(!driving&&!armed,"STOP did not end trial");
 check(!calls.some(c=>c.body.action==="move"),"browser must not issue movement directly");
 return {result:"PASS",test:"ARMED/manual -> accepted parameters -> disarm -> timed navigation ARM -> authenticated goal -> STOP",calls};
};
  return {result:"PASS",scenarios:[await runScenario(page,false),await runScenario(page,true)]};
}
