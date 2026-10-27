import assert from "node:assert/strict";
import fs from "node:fs";
import {createRequire} from "node:module";
import vm from "node:vm";
import {test} from "node:test";
import ts from "typescript";
import type {RiderClient} from "../../web-next/lib/use-rider";
import {translate} from "../../web-next/lib/i18n";

const host=createRequire(new URL("../../../../web-next/package.json",import.meta.url));
const local=createRequire(new URL("../../web-next/lib/use-rider.ts",import.meta.url));
const React=host("react") as typeof import("react");
const {createRoot}=host("react-dom/client") as typeof import("react-dom/client");
const {JSDOM}=host("jsdom");
type Row=Record<string,unknown>;
function deferred(){let resolve!:()=>void;const promise=new Promise<void>(done=>{resolve=done;});return {promise,resolve};}
const pause=()=>new Promise<void>(resolve=>setTimeout(resolve,0));
async function renderFixture(file:string,apiFetch:(path:string,options?:RequestInit)=>Promise<unknown>,props?:Row){
 const dom=new JSDOM('<div id="root"></div>',{url:"http://fixture.invalid",pretendToBeVisual:true});
 const previous={window:globalThis.window,document:globalThis.document};
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true});
 const exports:Row={};
 const source=ts.transpileModule(fs.readFileSync(new URL("../../"+file,import.meta.url),"utf8"),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}
 }).outputText;
 const downloads:Blob[]=[];
 vm.runInNewContext(source,{
  exports,window:dom.window,document:dom.window.document,console,performance,Date,AbortController,
  crypto:globalThis.crypto,setTimeout,clearTimeout,Blob,
  URL:{createObjectURL:(blob:Blob)=>{downloads.push(blob);return "blob:fixture";},revokeObjectURL:()=>{}},
  require:(name:string)=>{
   if(name==="react")return React;
   if(name==="react/jsx-runtime")return host(name);
   if(name==="@/lib/api-client")return {apiFetch};
   if(name==="@/lib/env")return {getApiBaseUrl:()=>""};
   if(name==="@/lib/optional-modules/transport")return {createBrowserUuid:()=>crypto.randomUUID()};
   if(name==="./use-camera-pipeline")return {useCameraPipeline:()=>({status:null,busy:false})};
   if(name.endsWith(".css"))return {__esModule:true,default:{}};
   if(name==="./primitives"){
    return {Button:(p:Row)=>{const props={...p};delete props.tone;delete props.size;return React.createElement("button",props);},
     Field:({children}:Row)=>React.createElement("label",null,children as import("react").ReactNode),
     Select:(p:Row)=>React.createElement("select",p)};
   }
   return local(name+".ts");
  }
 });
 const root=createRoot(dom.window.document.getElementById("root")!);
 let client:RiderClient|undefined;
 const View=props?exports.FiguresPanel:()=>{
  client=(exports.useRider as (section:string)=>RiderClient)("control");return null;
 };
 await React.act(async()=>{root.render(React.createElement(View as import("react").ComponentType<Row>,props));await pause();});
 return {root,dom,downloads,get client(){assert.ok(client);return client;},async close(){
  await React.act(async()=>root.unmount());dom.window.close();Object.assign(globalThis,previous);
 }};
}
function status(){
 const now=Date.now()/1000;
 return {connection:{configured:true,status:"online",session:"fixture",device_id:"demo",mode:"demo"},
  polling:{},errors:{},control:{server_time:now,safety:{state:"DISARMED",generation:"fixture-generation",last_seq:0,
  observed_at:now,stale:false,ready:true,contract_version:1,panel_control:{version:1},figures:{version:1}}},
  settings:{revision:45,control_modes:{click_threshold_ms:150,hold_renewal_ms:150,hold_timeout_ms:500,request_ttl_ms:500,supported:["pulse","hold","release"]}},face:{library:null,runtime:null},catalog:null};
}
test("STOP during the preliminary stop prevents figure ARM, while an ordinary start still works",async()=>{
 const gate=deferred(),entered=deferred(),calls:Row[]=[];
 const api=async(path:string,options?:RequestInit)=>{
  const body=options?.body?JSON.parse(String(options.body)):null;
  if(body){calls.push({path,body});if(path.endsWith("/control")&&calls.length===1){entered.resolve();await gate.promise;}}
  if(path.endsWith("/config"))return {devices:[],device_id:"demo",session:"fixture"};
  if(path.includes("/status"))return status();
  return {ok:true,confirmed:true,pending:false};
 };
 const f=await renderFixture("web-next/lib/use-rider.ts",api);
 try{
  let start!:Promise<string>;
  await React.act(async()=>{start=f.client.startFigure("sway","inert test");void start.catch(()=>{});await entered.promise;});
  await React.act(async()=>{await f.client.stopManual();});
  await React.act(async()=>{gate.resolve();await assert.rejects(start,/figure_cancelled/);});
  assert.equal(calls.filter(row=>String(row.path).endsWith("/motion/figures/runs")).length,0);
  await React.act(async()=>{await f.client.startFigure("sway","inert test");});
  assert.equal(calls.filter(row=>String(row.path).endsWith("/motion/figures/runs")).length,1);
 }finally{gate.resolve();await f.close();}
});
test("cancelling a figure stops further renewal before cancellation is acknowledged",async()=>{
 const cancellation=deferred(),cancelEntered=deferred();
 const requests:string[]=[];let renewals=0;
 let run={phase:"running",step:1,step_count:2};
 const client={status:{control:{safety:{figures:{version:1}}}},canMutate:true,controlFresh:true,busy:null,
  currentSession:()=>"fixture",startFigure:async()=>"run",keepFigure:async()=>{renewals++;}} as unknown as RiderClient;
 const api=async(path:string)=>{
  requests.push(path);
  if(path.endsWith("/cancel")){cancelEntered.resolve();await cancellation.promise;run={...run,phase:"cancelled"};return {};}
  if(path.endsWith("/figures"))return {version:1,implementation:"own_sequence",figures:[{id:"sway",version:1,implementation:"own_sequence",qualification:"manual_trial",physical_completion:"unverified",duration_ms:4000,available:true,maximum_speed:.25,intensity_factor:.3}]};
  return {run};
 };
 const f=await renderFixture("web-next/components/figures.tsx",api,{client,t:(key:string)=>translate("pl",key)});
 try{
  await React.act(async()=>{f.dom.window.document.querySelector('[data-testid="figure-start"]').click();await pause();});
  await React.act(async()=>{f.dom.window.document.querySelector('[data-testid="figure-cancel"]').click();await cancelEntered.promise;});
  const before=renewals;
  await React.act(async()=>{await new Promise(resolve=>setTimeout(resolve,260));});
  assert.equal(renewals,before,"an ARM keepalive was sent after Cancel");
 }finally{await React.act(async()=>{cancellation.resolve();await pause();});await f.close();}
});
test("figure export uses the API request pipeline and downloads its JSON response",async()=>{
 const calls:string[]=[];
 const client={status:{control:{safety:{figures:{version:1}}}},canMutate:true,controlFresh:true,busy:null,
  currentSession:()=>"fixture",startFigure:async()=>"run",keepFigure:async()=>{}} as unknown as RiderClient;
 const exported={run_id:"run",phase:"completed",physical_completion:"unverified"};
 const api=async(path:string)=>{
  calls.push(path);
  if(path.endsWith("/export"))return exported;
  if(path.endsWith("/figures"))return {version:1,implementation:"own_sequence",figures:[{id:"sway",version:1,implementation:"own_sequence",qualification:"manual_trial",physical_completion:"unverified",duration_ms:4000,available:true,maximum_speed:.25,intensity_factor:.3}]};
  return {run:exported};
 };
 const f=await renderFixture("web-next/components/figures.tsx",api,{client,t:(key:string)=>translate("pl",key)});
 try{
  await React.act(async()=>{f.dom.window.document.querySelector('[data-testid="figure-start"]').click();await pause();});
  const create=f.dom.window.document.createElement.bind(f.dom.window.document);
  f.dom.window.document.createElement=(name:string)=>{const node=create(name);if(name==="a")node.click=()=>{};return node;};
  await React.act(async()=>{f.dom.window.document.querySelector('[data-testid="figure-export"]').click();await pause();});
  assert.equal(calls.filter(path=>path.endsWith("/export")).length,1);
  assert.deepEqual(JSON.parse(await f.downloads[0].text()),exported);
 }finally{await f.close();}
});


for(const stage of ["preliminary-stop","dispatched"] as const){
 test("aborting figure start during "+stage+" prevents late motion",async()=>{
  const gate=deferred(),entered=deferred(),calls:Row[]=[];
  const api=async(path:string,options?:RequestInit)=>{
   const body=options?.body?JSON.parse(String(options.body)):null;
   if(body){
    calls.push({path,body});
    if(stage==="preliminary-stop"&&path.endsWith("/control")&&calls.length===1 ||
       stage==="dispatched"&&path.endsWith("/motion/figures/runs")){
     entered.resolve();await gate.promise;
    }
   }
   if(path.endsWith("/config"))return {devices:[],device_id:"demo",session:"fixture"};
   if(path.includes("/status"))return status();
   return {ok:true,confirmed:true,pending:false};
  };
  const f=await renderFixture("web-next/lib/use-rider.ts",api);
  const controller=new AbortController();
  try{
   let start!:Promise<string>;
   await React.act(async()=>{start=f.client.startFigure("sway","inert test",controller.signal);void start.catch(()=>{});await entered.promise;});
   await React.act(async()=>{controller.abort();await pause();});
   const stops=calls.filter(row=>String(row.path).endsWith("/control")&&(row.body as Row).action==="stop");
   assert.equal(stops.length,stage==="dispatched"?2:1,"a dispatched start must be stopped immediately");
   await React.act(async()=>{gate.resolve();await assert.rejects(start,/figure_cancelled/);});
   assert.equal(calls.filter(row=>String(row.path).endsWith("/motion/figures/runs")).length,stage==="dispatched"?1:0);
  }finally{gate.resolve();await f.close();}
 });
}

test("abort does not stop again after a newer operator STOP",async()=>{
 const gate=deferred(),entered=deferred(),calls:Row[]=[];
 const api=async(path:string,options?:RequestInit)=>{
  const body=options?.body?JSON.parse(String(options.body)):null;
  if(body){calls.push({path,body});if(path.endsWith("/motion/figures/runs")){entered.resolve();await gate.promise;}}
  if(path.endsWith("/config"))return {devices:[],device_id:"demo",session:"fixture"};
  if(path.includes("/status"))return status();
  return {ok:true,confirmed:true,pending:false};
 };
 const f=await renderFixture("web-next/lib/use-rider.ts",api),controller=new AbortController();
 try{
  let start!:Promise<string>;
  await React.act(async()=>{start=f.client.startFigure("sway","inert test",controller.signal);void start.catch(()=>{});await entered.promise;});
  await React.act(async()=>{await f.client.stopManual();controller.abort();await pause();});
  assert.equal(calls.filter(row=>String(row.path).endsWith("/control")&&(row.body as Row).action==="stop").length,2);
  await React.act(async()=>{gate.resolve();await assert.rejects(start,/figure_cancelled/);});
 }finally{gate.resolve();await f.close();}
});


test("hiding the page aborts a pending figure start",async()=>{
 const gate=deferred(),entered=deferred(),calls:string[]=[];
 let signal:AbortSignal|undefined;
 const client={status:{control:{safety:{figures:{version:1}}}},canMutate:true,controlFresh:true,busy:null,
  currentSession:()=>"fixture",startFigure:async(_id:string,_plan:string,received:AbortSignal)=>{
   signal=received;entered.resolve();await gate.promise;return "hidden-run";
  },keepFigure:async()=>{}} as unknown as RiderClient;
 const api=async(path:string)=>{
  calls.push(path);
  if(path.endsWith("/figures"))return {version:1,implementation:"own_sequence",figures:[{id:"sway",version:1,implementation:"own_sequence",qualification:"manual_trial",physical_completion:"unverified",duration_ms:4000,available:true,maximum_speed:.25,intensity_factor:.3}]};
  return {run:{phase:"running"}};
 };
 const f=await renderFixture("web-next/components/figures.tsx",api,{client,t:(key:string)=>translate("pl",key)});
 try{
  await React.act(async()=>{f.dom.window.document.querySelector('[data-testid="figure-start"]').click();await entered.promise;});
  await React.act(async()=>{
   Object.defineProperty(f.dom.window.document,"hidden",{value:true,configurable:true});
   f.dom.window.document.dispatchEvent(new f.dom.window.Event("visibilitychange"));
  });
  assert.equal(signal?.aborted,true);
  await React.act(async()=>{gate.resolve();await pause();});
  assert.ok(calls.some(path=>path.endsWith("/hidden-run/cancel")));
  await React.act(async()=>{
   Object.defineProperty(f.dom.window.document,"hidden",{value:false,configurable:true});
   f.dom.window.document.dispatchEvent(new f.dom.window.Event("visibilitychange"));await pause();
  });
  assert.equal(f.dom.window.document.querySelector('[data-testid="figure-start"]').disabled,false,"hidden cancellation left Start disabled");
 }finally{gate.resolve();await f.close();}
});

test("unmount aborts a pending start and scopes cancellation of a late accepted run",async()=>{
 const gate=deferred(),entered=deferred(),calls:string[]=[];
 let signal:AbortSignal|undefined;
 const client={status:{control:{safety:{figures:{version:1}}}},canMutate:true,controlFresh:true,busy:null,
  currentSession:()=>"fixture",startFigure:async(_id:string,_plan:string,received:AbortSignal)=>{
   signal=received;entered.resolve();await gate.promise;return "delayed-run";
  },keepFigure:async()=>{}} as unknown as RiderClient;
 const api=async(path:string)=>{
  calls.push(path);
  if(path.endsWith("/figures"))return {version:1,implementation:"own_sequence",figures:[{id:"sway",version:1,implementation:"own_sequence",qualification:"manual_trial",physical_completion:"unverified",duration_ms:4000,available:true,maximum_speed:.25,intensity_factor:.3}]};
  return {run:{phase:"running"}};
 };
 const f=await renderFixture("web-next/components/figures.tsx",api,{client,t:(key:string)=>translate("pl",key)});
 try{
  await React.act(async()=>{f.dom.window.document.querySelector('[data-testid="figure-start"]').click();await entered.promise;});
  await React.act(async()=>f.root.unmount());
  assert.equal(signal?.aborted,true);
  await React.act(async()=>{gate.resolve();await pause();});
  assert.ok(calls.some(path=>path.endsWith("/delayed-run/cancel")),"late accepted run was not cancelled");
 }finally{gate.resolve();await f.close();}
});
