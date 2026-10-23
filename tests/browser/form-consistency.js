// Read-only demo plus mocked sample GETs; every mutation is rejected.
async page => {
  const origin=new URL(page.url());
  if(!["localhost","127.0.0.1","[::1]"].includes(origin.hostname))throw new Error("loopback fixture required");
  const base=origin.origin, check=(ok,message)=>{if(!ok)throw new Error(message);};
  const config=await(await page.request.get(base+"/api/v1/rider/config")).json();
  check(config.devices?.length&&config.devices.every(item=>item.mode==="demo"),"demo-only allowlist required");
  const mutations=[];
  const collection={session_id:"form-fixture",started_at:1700000000,source_device_id:"demo",state:"closed",counts:{saved:1}};
  const sample={sample_id:"sample-form",ordinal:1,frame_id:"fixture-frame",labels:[],quality:{flags:[]},raw:{sha256:"a".repeat(64)}};
  await page.route("**/api/v1/rider/**",async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.slice("/api/v1/rider".length);
    const json=body=>route.fulfill({contentType:"application/json",body:JSON.stringify(body)});
    if(req.method()!=="GET") {mutations.push(path);return route.fulfill({status:409,contentType:"application/json",body:JSON.stringify({detail:{code:"fixture_mutation_forbidden"}})});}
    if(path==="/samples/sessions")return json({sessions:[collection]});
    if(path==="/samples/sessions/form-fixture")return json({session:collection,samples:[sample],total:1});
    if(path==="/samples/items/sample-form")return json(sample);
    if(path==="/samples/items/sample-form/original")return route.fulfill({contentType:"image/png",body:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN2kAAAAASUVORK5CYII=","base64")});
    return route.continue();
  });
  const results=[];
  const inspect=async(tab)=>{
    const fields=await page.getByTestId("rider-page").locator("input:visible,select:visible,textarea:visible").evaluateAll(nodes=>nodes.map(node=>({kind:node.dataset.riderControl,type:node.type,height:node.getBoundingClientRect().height,radius:getComputedStyle(node).borderRadius,font:getComputedStyle(node).fontSize,color:getComputedStyle(node).color,bg:getComputedStyle(node).backgroundColor,labelled:!!node.labels?.length||!!node.getAttribute("aria-label"),min:node.getAttribute("min"),max:node.getAttribute("max"),step:node.getAttribute("step")})));
    for(const field of fields){
      check(field.kind,"unshared form control in "+tab);
      check(field.labelled,"unlabelled control in "+tab+": "+JSON.stringify(field));
      check(field.color!==field.bg,"unreadable field in "+tab);
      if(["input","select"].includes(field.kind)){
        check(Math.abs(field.height-36)<.1,"mixed field heights: "+JSON.stringify(field));
        check(field.radius==="4px","mixed field shape: "+JSON.stringify(field));
      }
      if(field.kind==="textarea")check(field.height>=84,"text area too small");
    }
    const arm=await page.getByTestId("rider-arm").boundingBox();
    check(arm&&arm.width>=150,"header ARM clipped in "+tab);
    results.push({tab,fields:fields.length,kinds:[...new Set(fields.map(item=>item.kind))]});
  };
  try {
    for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
      await page.setViewportSize(viewport);await page.goto(base+"/rider");
      await page.getByTestId("rider-manual-mode").waitFor();
      await page.getByTestId("rider-manual-mode").selectOption("continuous");
      check(await page.getByTestId("rider-forward").isDisabled(),"editing input mode armed robot");
      await inspect("Sterowanie "+viewport.width);
      await page.getByTestId("workspace-automatic").click();
      await page.getByTestId("workspace-secondary").locator("details").evaluateAll(nodes=>nodes.filter(node=>node.querySelector("input,select,textarea")).forEach(node=>{node.open=true;}));
      await inspect("Automat and camera settings "+viewport.width);
      const fieldset=page.getByTestId("workspace-secondary").locator("fieldset").first();
      if(await fieldset.count()) {
        await fieldset.evaluate(node=>{node.disabled=true;});
        const children=fieldset.locator("input,select,textarea");
        for(let i=0;i<await children.count();i++)check(await children.nth(i).isDisabled(),"fieldset disabled semantics lost");
        await fieldset.evaluate(node=>{node.disabled=false;});
      }
      for(const tab of ["Pomiary","Kalibracja","Cele i zasoby","Face Studio","Percepcja"]){
        await page.getByRole("tab",{name:tab,exact:true}).click();
        if(tab==="Kalibracja"){
          const field=page.getByTestId("rider-page").locator('input[type="number"]').first();
          await field.waitFor();
          const original=await field.inputValue();await field.fill("");check(await field.inputValue()==="","number field cannot be cleared");await field.fill("-1");
          check(await field.evaluate(node=>node.validity.rangeUnderflow),"producer minimum lost");
          await field.fill(original);
        }
        if(tab==="Face Studio"){
          const name=page.getByTestId("rider-page").locator('input[data-rider-control="input"]').first();
          await name.waitFor();await name.fill("Form fixture");
          const toggle=page.getByTestId("rider-page").locator('input[type="checkbox"]').first();
          await toggle.check();check(await toggle.isChecked(),"native checkbox change lost");
        }
        if(tab==="Percepcja"){
          await page.getByTestId("samples-browser").locator("summary").first().click();
          await page.getByTestId("samples-session").selectOption("form-fixture");
          await page.getByTestId("sample-thumbnail").first().click();
          const note=page.getByTestId("sample-editor").locator("textarea");await note.fill("First line\nSecond line");
          check((await note.inputValue()).includes("\n"),"multiline input lost newlines");
          check(await note.getAttribute("maxlength")==="1024","text limit lost");
        }
        await inspect(tab+" "+viewport.width);
        await page.screenshot({path:"forms-"+viewport.width+"-"+tab.replaceAll(" ","-")+".png"});
        if(tab==="Kalibracja"){
          await page.evaluate(()=>{document.documentElement.dataset.theme="venom-light";});
          await inspect(tab+" light "+viewport.width);
          await page.screenshot({path:"forms-light-"+viewport.width+".png"});
          await page.evaluate(()=>{document.documentElement.dataset.theme="venom-dark";});
        }
      }
    }
    check(mutations.length===0,"form drafts sent mutations: "+mutations.join(","));
    return {result:"PASS",fixtureOnly:true,mutations:0,results};
  }finally{await page.goto("about:blank");await page.unroute("**/api/v1/rider/**");}
}
