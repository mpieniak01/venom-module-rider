/** Real browsers, inert fixtures only. No Venom server, credentials, model or Pi. */
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {createServer} from "node:http";
import {createRequire} from "node:module";
import {mkdir,readFile,writeFile,access} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
import vm from "node:vm";

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"..");
const webRoot=path.resolve(process.env.RIDER_WEB_ROOT??path.join(root,"../..","web-next"));
const require=createRequire(path.join(webRoot,"package.json"));
const {build}=require("esbuild"),{chromium}=require("playwright");
const output=path.join(root,"test-results","browser-regressions");
const args=process.argv.slice(2);
if(args.some((value,index)=>value!=="--only"&&args[index-1]!=="--only"))throw new Error("Usage: check_browser_regressions.mjs [--only name]");
const filter=args.includes("--only")?args[args.indexOf("--only")+1]:"";
if(args.includes("--only")&&!filter)throw new Error("--only requires a regression name");
const fixtures={
  "in-place-turn":"in-place-turn-fixture.tsx",
  "figures":"figures-fixture.tsx",
  "input-region-operator":"input-region-operator-fixture.tsx",
  "input-region":"input-region-fixture.tsx",
  "room-preset":"room-preset-fixture.tsx",
  "operator-parameters":"operator-parameters-fixture.tsx",
  "image-reference":"image-reference-fixture.tsx",
  "session-archive":"session-archive-fixture.tsx",
  "video-analysis":"video-analysis-fixture.tsx",
  workspace:"global-study-fixture.tsx",
};
const cases=[
  {name:"in-place-turn",fixture:"in-place-turn",file:"in-place-turn.js"},
  {name:"figures",fixture:"figures",file:"figures.js"},
  {name:"manual-control-stability",fixture:"workspace",file:"manual-control-stability.js"},
  {name:"preview-dirty-policy",fixture:"workspace",file:"preview-dirty-policy.js"},
  {name:"pulse-trials",fixture:"workspace",file:"pulse-trials.js"},
  {name:"journey-settings",fixture:"workspace",file:"journey-settings.js"},
  {name:"operator-start-settings",fixture:"workspace",file:"operator-start-settings.js"},
  {name:"input-region-operator",fixture:"input-region-operator",file:"input-region-operator.js"},
  {name:"input-region",fixture:"input-region",file:"input-region.js"},
  {name:"room-preset",fixture:"room-preset",file:"room-preset.js"},
  {name:"operator-parameters",fixture:"operator-parameters",file:"operator-parameters.js"},
  {name:"calibration-defaults",fixture:"image-reference",file:"image-reference-defaults.js"},
  {name:"calibration-recovery",fixture:"image-reference",file:"image-reference-recovery.js"},
  {name:"recording-sessions",fixture:"session-archive",file:"session-archive.js"},
  {name:"video-comparison",fixture:"video-analysis",file:"video-analysis.js"},
  {name:"video-playback-speed",fixture:"video-analysis",file:"video-speed.js"},
  {name:"journey-lifecycle",fixture:"workspace",file:"journey.js"},
  ...[
    ["balanced","depth-anything-v2-indoor-small"],
    ["room_fluid","depth-anything-v2-indoor-small"],
    ["room_fluid","segformer-b0-ade20k"],
    ["room_fluid","yolo26n-sem-ade20k"],
  ].map(([profile,model])=>({name:"journey-"+profile+"-"+model,fixture:"workspace",file:"journey-start-prod.js",query:"?profile="+profile+"&model="+model})),
].filter(value=>!filter||value.name.includes(filter));
if(!cases.length)throw new Error("No matching browser regression; nothing was verified");
await mkdir(output,{recursive:true});
const assets=new Map();
for(const name of new Set(cases.map(value=>value.fixture))){
  const result=await build({entryPoints:[path.join(root,"tests/browser",fixtures[name])],bundle:true,jsx:"automatic",write:false,
    outfile:path.join(output,"assets",name+".js"),alias:{"@":webRoot,"@/lib/i18n":path.join(root,"tests/browser/workspace-host.tsx"),"@/lib/theme":path.join(root,"tests/browser/workspace-host.tsx")},
    define:{"process.env":JSON.stringify({NODE_ENV:"development"})}});
  for(const file of result.outputFiles)assets.set("/assets/"+path.basename(file.path),file.contents);
}
let serverApiRequests=0;
const html=name=>'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Isolated Rider regression</title><style>body{margin:0;font-family:Arial,sans-serif}*{box-sizing:border-box}</style><link rel="stylesheet" href="/assets/'+name+'.css"><div id="root"></div><script src="/assets/'+name+'.js"></script>';
const server=createServer((request,response)=>{
  const url=new URL(request.url,"http://127.0.0.1");
  if(url.pathname.startsWith("/api/")){serverApiRequests++;response.writeHead(503);response.end("Fixture has no API");return;}
  if(request.method!=="GET"&&request.method!=="HEAD"){response.writeHead(405);response.end();return;}
  const name=url.pathname.match(/^\/fixtures\/([a-z-]+)\.html$/)?.[1]??(["/","/rider"].includes(url.pathname)?"workspace":null);
  if(name&&Object.hasOwn(fixtures,name)){response.writeHead(200,{"content-type":"text/html; charset=utf-8"});response.end(html(name));return;}
  if(url.pathname==="/empty"){response.writeHead(200,{"content-type":"text/html"});response.end("<!doctype html><title>Isolation probe</title>");return;}
  const asset=assets.get(url.pathname);
  if(asset){response.writeHead(200,{"content-type":url.pathname.endsWith(".css")?"text/css":"text/javascript"});response.end(asset);return;}
  response.writeHead(404);response.end();
});
await new Promise((resolve,reject)=>{server.once("error",reject);server.listen(0,"127.0.0.1",resolve);});
const origin="http://127.0.0.1:"+server.address().port;
const report={head:execFileSync("git",["rev-parse","HEAD"],{cwd:root,encoding:"utf8"}).trim(),worktree_dirty:!!execFileSync("git",["status","--porcelain"],{cwd:root,encoding:"utf8"}).trim(),playwright:require("playwright/package.json").version,browser:"",isolation:false,cases:[]};
let browser;
async function contextFor(blocked){
  const context=await browser.newContext({viewport:{width:1440,height:1100},serviceWorkers:"block"});
  await context.route("**/*",route=>{
    const url=new URL(route.request().url());
    const allowed=url.origin===origin&&!url.pathname.startsWith("/api/")&&["GET","HEAD"].includes(route.request().method());
    if(allowed)return route.continue();
    blocked.push({method:route.request().method(),url:url.href});return route.abort("blockedbyclient");
  });
  await context.routeWebSocket("**/*",socket=>{blocked.push({method:"WS",url:socket.url()});socket.close();});
  for(const method of ["get","post","put","patch","delete","head","fetch"]){
    Object.defineProperty(context.request,method,{value:async()=>{throw new Error("Direct HTTP is disabled; mock the browser request");}});
  }
  return context;
}
try{
  const executable=process.env.RIDER_BROWSER_EXECUTABLE||chromium.executablePath();
  try{await access(executable);}catch{throw new Error("Chromium is missing. Install the project's Playwright browser or set RIDER_BROWSER_EXECUTABLE to an existing compatible executable. Nothing was downloaded.");}
  browser=await chromium.launch({headless:true,executablePath:executable});report.browser=browser.version();
  const blocked=[],probe=await contextFor(blocked);
  try{
    const page=await probe.newPage();await page.goto(origin+"/empty");
    const prevented=await page.evaluate(async()=>Promise.all(["/api/v1/rider/control","https://example.invalid/api/v1/rider/control"].map(async url=>{try{await fetch(url,{method:"POST",body:"{}"});return false;}catch{return true;}})));
    assert.deepEqual(prevented,[true,true]);assert.equal(blocked.length,2);
    const socketPrevented=await page.evaluate(url=>new Promise(resolve=>{
      const socket=new WebSocket(url),timer=setTimeout(()=>resolve(false),3000);
      socket.onopen=()=>{clearTimeout(timer);resolve(false);socket.close();};
      socket.onclose=socket.onerror=()=>{clearTimeout(timer);resolve(true);};
    }),origin.replace("http:","ws:")+"/socket");
    assert.equal(socketPrevented,true);assert.equal(blocked.filter(row=>row.method==="WS").length,1);
    await assert.rejects(probe.request.post(origin+"/api/v1/rider/control"),/Direct HTTP is disabled/);
    assert.equal(serverApiRequests,0);report.isolation=true;console.log("PASS network-isolation");
  }finally{await probe.close();}
  for(const item of cases){
    const blocked=[],context=await contextFor(blocked),page=await context.newPage(),errors=[];
    page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(10000);page.on("pageerror",error=>errors.push(error.stack??error.message));
    const started=Date.now();let timer,caseFailed=false;
    await context.tracing.start({screenshots:true,snapshots:true,sources:false});
    try{
      const fixtureUrl=origin+"/fixtures/"+item.fixture+".html"+(item.query??"");
      if(item.file!=="operator-parameters.js"){
        // Supply a real URL without mounting the app before its mocks exist.
        await page.route(origin+"/fixtures/"+item.fixture+".html*",route=>route.fulfill({contentType:"text/html",body:"<!doctype html><title>Fixture bootstrap</title>"}),{times:1});
      }
      await page.goto(fixtureUrl);
      if(item.file==="operator-parameters.js")await page.getByTestId("navigation-profiles").waitFor();
      blocked.length=0;errors.length=0;
      const source=(await readFile(path.join(root,"tests/browser",item.file),"utf8")).trim().replace(/;$/,"");
      const scenario=vm.runInThisContext("("+source+"\n)",{filename:item.file});
      const result=await Promise.race([scenario(page),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error("Browser case exceeded 90 seconds")),90000);})]);
      assert.deepEqual(errors,[],"Uncaught browser exception");assert.deepEqual(blocked,[],"Unmocked request blocked by isolation guard");
      report.cases.push({name:item.name,state:"passed",elapsed_ms:Date.now()-started,result});console.log("PASS "+item.name);
    }catch(error){
      caseFailed=true;
      report.cases.push({name:item.name,state:"failed",elapsed_ms:Date.now()-started,error:String(error.stack??error),blocked,pageErrors:errors});
      await page.screenshot({path:path.join(output,item.name+"-failure.png"),fullPage:true,timeout:3000}).catch(()=>{});
      console.error("FAIL "+item.name+": "+error.message);
    }finally{clearTimeout(timer);await context.tracing.stop(caseFailed?{path:path.join(output,item.name+"-trace.zip")}:{});await context.close();}
  }
  assert.equal(serverApiRequests,0,"A request escaped the browser guard and reached the static server");
}catch(error){report.error=String(error.stack??error);console.error(error.message);process.exitCode=1;}
finally{
  if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));
  report.server_api_requests=serverApiRequests;await writeFile(path.join(output,"report.json"),JSON.stringify(report,null,2)+"\n");
}
const failed=report.cases.filter(value=>value.state!=="passed").length;
if(failed||report.error||report.cases.length!==cases.length||!report.isolation)process.exitCode=1;
console.log(`${report.cases.length-failed}/${cases.length} browser regressions passed; report: ${path.join(output,"report.json")}`);
