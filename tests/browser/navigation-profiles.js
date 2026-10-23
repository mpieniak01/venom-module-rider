// Run against navigation-profiles-fixture.tsx only. It contains inert, local client mocks.
async page => {
  const check = (value, message) => { if (!value) throw new Error(message); };
  await page.getByTestId("navigation-profiles").waitFor();
  check(await page.getByTestId("behavior-profile").isVisible(), "Caution selector is hidden on entry");
  const select = page.getByTestId("behavior-profile"), apply = page.getByTestId("behavior-apply");
  check(await page.evaluate(() => window.profileFixtureCalls.length) === 0, "Opening the fixture issued a mutation");
  check(await select.inputValue() === "legacy", "Legacy was not preserved");
  await select.selectOption("balanced");
  check(await page.getByTestId("behavior-draft").isVisible(), "No pending selection shown");
  await page.getByTestId("fixture-other-panel").click();
  check(await apply.isDisabled(), "External revision did not disable stale draft");
  check((await page.getByTestId("behavior-message").textContent()).includes("drugim panelu"), "No conflict message");
  await page.getByTestId("behavior-refresh").click();
  await select.selectOption("fluid"); await apply.click();
  await page.getByTestId("behavior-saved").waitFor();
  const calls = await page.evaluate(() => window.profileFixtureCalls);
  check(calls.length === 1 && calls[0].path === "/navigation/config", "Unexpected side effect");
  check(JSON.stringify(calls[0].body) === JSON.stringify({revision:10,values:{behavior:{version:1,profile_id:"fluid",profile_revision:1}}}), "Incorrect profile CAS body");
  check(await select.inputValue() === "fluid", "Applied profile not visible");
  await page.getByTestId("fixture-provider").selectOption("local");
  await select.selectOption("balanced");
  check(await apply.isDisabled(), "Local Canny provider allowed a host temporal profile");
  check((await page.getByTestId("behavior-message").textContent()).includes("lokalny algorytm"), "Missing local provider explanation");
  await select.selectOption("legacy");
  check(await apply.isEnabled(), "Supported Legacy rollback was disabled for local provider");
  await page.getByTestId("fixture-provider").selectOption("venom");
  await page.getByTestId("fixture-drive").click(); await select.selectOption("balanced");
  check(await apply.isDisabled(), "Active drive allowed profile mutation");
  await page.getByTestId("fixture-pause").click(); await page.getByTestId("behavior-refresh").click();
  await page.getByTestId("fixture-support").click();
  check(await select.isDisabled(), "Unsupported catalog allowed selection");
  check(await select.locator("option").count() === 4, "Unsupported caution choices should remain discoverable");
  await page.getByTestId("fixture-language").selectOption("de");
  check((await page.getByTestId("navigation-profiles").textContent()).includes("Vorsichtsstufe"), "German translation missing");
  await page.setViewportSize({width:390,height:844});
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile horizontal overflow");
  return { scenarios: 10, mutations: calls.length, robot_contacted: false, pass: true };
}
