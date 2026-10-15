// Hardware-free Playwright CLI acceptance; independent browser contexts.
async page => {
 const check=(ok,reason)=>{if(!ok)throw new Error(reason)};
 const base=new URL(page.url()).origin;
 check(["localhost","127.0.0.1","[::1]"].includes(new URL(base).hostname),"loopback fixture required");
 const cfg=await(await page.request.get(base+"/api/v1/rider/config")).json();
 check(cfg.devices?.length && cfg.devices.every(d=>d.mode==="demo"),"demo-only allowlist required");
 await page.getByTestId("takeover-manual").click();
 await page.waitForTimeout(500);
 await page.route("**/api/v1/rider/control",route=>route.abort());
 await page.getByTestId("rider-continuous-forward").click();
 await page.waitForTimeout(800);
 check(await page.getByTestId("rider-continuous-forward").getAttribute("aria-pressed")==="false","lost uplink did not cancel intent");
 await page.unroute("**/api/v1/rider/control");
 await page.waitForTimeout(900);
 check(await page.getByTestId("rider-continuous-forward").getAttribute("aria-pressed")==="false","reconnect resumed motion");
 const lostLink=await page.getByTestId("manual-feedback").innerText();
 check(lostLink.includes("Zgłoszone ZERO"),"watchdog did not report zero");
 const context=await page.context().browser().newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const other=await context.newPage();
 try {
   await other.goto(base+"/rider");
   await other.getByTestId("rider-continuous-forward").waitFor({state:"visible"});
   check(await other.getByTestId("rider-continuous-forward").isDisabled(),"other browser silently owns authority");
   await other.getByTestId("takeover-manual").tap();
   await other.waitForTimeout(600);
   check(!(await other.getByTestId("rider-continuous-forward").isDisabled()),"explicit takeover did not bind new panel");
   await other.getByTestId("rider-continuous-forward").tap();
   await other.waitForTimeout(1000);
   check(await other.getByTestId("rider-continuous-forward").getAttribute("aria-pressed")==="true","touch release cancelled continuous intent");
   await page.waitForTimeout(500);
   check(await page.getByTestId("rider-continuous-forward").isDisabled(),"old panel still owns control");
   await other.getByTestId("rider-continuous-stop").tap();
   await other.waitForTimeout(600);
   check(await other.getByTestId("manual-feedback").innerText().then(text=>text.includes("Zgłoszone ZERO")),"touch STOP no zero");
   await other.screenshot({path:"output/playwright/continuous-mobile.png",fullPage:true});
   await page.getByTestId("rider-stop").click();
   await other.waitForTimeout(600);
   check(await other.getByTestId("rider-arm").getAttribute("aria-pressed")==="false","global stop from other panel failed");
   return {lostLink,touchAndTwoPanels:"PASS",globalStop:"PASS"};
 } finally { await context.close(); }
}
