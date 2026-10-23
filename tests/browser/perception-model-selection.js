// Self-contained Playwright CLI flow. Every Rider API request is intercepted.
// The page uses synthetic image results; no camera, model download or robot is contacted.
async page => {
  const base = new URL(page.url()).origin;
  if (!["localhost", "127.0.0.1", "[::1]"].includes(new URL(base).hostname)) throw new Error("loopback UI required");
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const wait = async (predicate, message) => {
    const until = Date.now() + 12000;
    while (!await predicate()) { check(Date.now() < until, message); await page.waitForTimeout(40); }
  };
  await page.goto("about:blank");
  const calls = [], scenarios = [];
  const a = "depth-anything-v2-indoor-small", b = "segformer-b0-ade20k";
  const hashes = { [a]: "a".repeat(64), [b]: "b".repeat(64) };
  let model = a, revision = 31, pipelineRevision = 8, provider = "venom", engine = "ssd";
  let safety = "DISARMED", switching = false, failSelection = false, failCatalog = false, wrongFrames = 0, sequence = 0, releaseSelection;
  let pendingSelection = false, piOffline = false, installedYolo = false, wrongLocalFrames = 0;
  const config = () => ({ revision, mode: "observe", speed: .1, max_motion_ms: 150, decision_budget_ms: 300, corridor: [.35,.8,.65,.95], verified: false, calibration_id: "", model_sha256: "",
    perception: { engine: model === a ? "depth_plane" : "segformer", model_id: model, artifact_sha256: hashes[model], residual_limit: .06, outlier_limit: .005, slope_min: 2 } });
  const modelRow = (id, label) => ({ id, label, engine: id === a ? "depth_plane" : "segformer", task: id === a ? "relative_ground_geometry" : "semantic_segmentation", revision: "fixture-pinned",
    installed: true, installation: "ready", loaded: id === model && !switching, selectable: true, reason: null, drive_supported: id === a, provider: "CPUExecutionProvider", sha256: hashes[id], job: null });
  const localModels = () => ["canny", "ssd"].map(id => ({ ...modelRow("local-" + id, "Pi " + id.toUpperCase()), engine: id, sha256: "", loaded: provider === "local" && engine === id, drive_supported: false }));
  const catalog = () => ({
    models: [modelRow(a, "Depth Anything V2"), modelRow(b, "SegFormer-B0"), { ...modelRow("fast-scnn-floor", "Fast-SCNN"), installed: false, installation: "needs_review", selectable: false, reason: "model_needs_review", qualification_details: ["external_data_missing", "preprocessing_unverified"] }, { ...modelRow("yolo26n-sem-ade20k", "YOLO26"), sha256: "c".repeat(64), installed: installedYolo, selectable: installedYolo, installation: installedYolo ? "ready" : "not_installed", reason: installedYolo ? null : "model_not_installed" }],
    selection: piOffline ? null : { model_id: model, artifact_sha256: hashes[model], revision }, selection_error: piOffline ? "pi_unreachable" : null, provider: piOffline ? null : provider, local_engine: engine, pipeline_revision: piOffline ? null : pipelineRevision,
    providers: [{ id: "venom", available: true }, { id: "local", available: true }], local_models: localModels(), switching,
    diagnostics: { runtime: { state: switching ? "loading" : "active", active_model_id: model }, inference_ms: 30, fps: 2, ram_mb: null, vram_mb: null },
  });
  const profile = () => ({ analysis_location: provider, local_engine: engine, remote_transport: "snapshots" });
  const pipeline = () => ({ contract_id: "rider.camera_pipeline", contract_version: 1, revision: pipelineRevision, profile: profile(), active_profile: profile(), state: "active", transport_epoch: 1, camera_session: "perception-fixture",
    capabilities: { analysis_locations: ["venom", "local"], local_engines: ["canny", "ssd"], remote_transports: ["snapshots"], stream_drive_supported: true, local_drive_supported: false }, effective_stages: { capture: true }, error: null });
  const navigation = () => ({ config: config(), detector: { configured: true, loaded: !switching, drive_supported: model === a, sha256: hashes[model], purpose: model === a ? "relative_ground_geometry" : "semantic_segmentation" },
    runtime: { known: true, observe: true, drive: true }, goals: { observe: true, drive: false },
    status: { running: true, stale: false, run_id: "observe-" + revision, config_revision: revision, mode: "observe" } });
  await page.route("**/api/v1/rider/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.slice("/api/v1/rider".length);
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (request.method() !== "GET") {
      const body = request.postDataJSON(); calls.push({ path, body });
      if (path === "/perception/models/yolo26n-sem-ade20k/install") { installedYolo = true; return json({ job: { id: "fixture-install", state: "ready", progress: 100 } }, 202); }
      if (path === "/perception/selection") {
        check(body.revision === revision, "selection ignored config CAS");
        if (failSelection) return json({ detail: { code: "model_runtime_unavailable" } }, 409);
        switching = true; pendingSelection = true;
        await new Promise(resolve => { releaseSelection = resolve; });
        model = body.model_id; revision++; switching = false; pendingSelection = false;
        return json({ selection: catalog().selection, config: config() });
      }
      if (path === "/camera/pipeline") {
        check(body.action === "configure", "selection started/stopped the camera");
        check(body.expected_revision === pipelineRevision, "provider ignored pipeline CAS");
        provider = body.profile.analysis_location; engine = body.profile.local_engine; pipelineRevision++; revision++;
        return json(pipeline());
      }
      throw new Error("forbidden Rider mutation " + path);
    }
    if (path === "/config") return json({ devices: [{ id: "fixture", label: "Perception fixture", mode: "demo" }], device_id: "fixture", session: "perception-fixture" });
    if (path === "/status") return json({
      connection: { configured: true, status: piOffline ? "offline" : "online", session: "perception-fixture", device_id: "fixture", mode: "demo" }, polling: { control_ms: 200 }, errors: {},
      settings: { revision: 1, directions: {}, parameter_schema: {}, control_modes: { supported: ["pulse","hold","release"], click_threshold_ms: 150, hold_renewal_ms: 100, hold_timeout_ms: 400, request_ttl_ms: 500 } },
      control: { server_time: Date.now()/1000, safety: { state: safety, owner: null, generation: "fixture", observed_at: Date.now()/1000, stale: false, moving: false, can_move: safety === "ARMED", ready: true } },
      health: {}, telemetry: {}, resources: {}, catalog: { features: [] }, logic: {}, face: {},
    });
    if (path === "/perception/models") return failCatalog ? json({ detail: { code: "fixture_catalog_unavailable" } }, 503) : json(catalog());
    if (path === "/camera/pipeline") return json(pipeline());
    if (path === "/navigation/config" || path === "/navigation/state") return json(navigation());
    if (path.startsWith("/camera/")) {
      sequence++;
      const wrong = wrongFrames > 0; if (wrong) wrongFrames--;
      const frameModel = wrong ? a : model, frameRevision = wrong ? revision - 1 : revision;
      const analysis = { run_id: "observe-" + frameRevision, frame_id: "frame-" + sequence, sequence, config_revision: frameRevision, state: "clear", vision_action: "stop", clear_count: 0,
        model_id: frameModel, artifact_sha256: hashes[frameModel], model_generation: "fixture-" + frameRevision, perception_engine: frameModel === a ? "depth_plane" : "segformer" };
      const source = { contract_version: 1, camera_session: "perception-fixture", transport_epoch: 1, pipeline_revision: pipelineRevision, sequence, frame_id: analysis.frame_id, captured_monotonic_ns: Date.now()*1e6, sha256: "d".repeat(64) };
      if (provider === "local") {
        const actualEngine = wrongLocalFrames > 0 ? "canny" : engine;
        if (wrongLocalFrames > 0) wrongLocalFrames--;
        Object.assign(source, { model_id: "local-" + actualEngine, local_engine: actualEngine, analysis_location: "local" });
      }
      const label = provider === "local" ? "Pi " + engine : frameModel;
      return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#202a36"/><path d="M160 480L280 200H360L480 480Z" fill="#23835e"/><path d="M400 340H540V470H400Z" fill="#ce5260"/><path d="M80 380H130V470H80Z" fill="#d9a540"/><text x="20" y="40" fill="white" font-size="18">' + label + '</text></svg>',
        headers: { "X-Rider-Stale": "false", "X-Rider-Frame-Age-Ms": "20", "X-Rider-Frame-Age-Source": "pi-reported-plus-host-elapsed", "X-Rider-Frame-Id": analysis.frame_id,
          "X-Rider-Source": provider === "venom" ? "venom" : "proc", "X-Rider-Source-Json": JSON.stringify(source), "X-Rider-Analysis-Json": JSON.stringify(analysis), "X-Rider-Vision-Inference-Ms": "30", "X-Rider-Vision-State": "clear" } });
    }
    return json({ detail: { code: "fixture_unavailable" } }, 404);
  });
  try {
    await page.goto(base + "/rider");
    const panel = page.getByTestId("perception-models"), choice = page.getByTestId("perception-model-choice"), location = page.getByTestId("perception-provider-choice"), apply = page.getByTestId("perception-model-apply");
    await panel.locator("summary").first().click();
    await wait(async () => await choice.inputValue() === a, "initial model missing");
    await wait(async () => (await page.getByTestId("perception-preview-result").innerText()).includes(a), "applied A preview missing");
    check(calls.length === 0, "page mount mutated camera, robot or model");
    await choice.selectOption(b);
    check((await page.getByTestId("perception-active-model").innerText()).includes("Depth Anything V2"), "draft B relabelled applied A");
    check((await page.getByTestId("perception-preview-result").innerText()).includes(a), "draft B relabelled A imagery");
    await wait(async () => !await apply.isDisabled(), "idle confirmed disarmed switch blocked");
    await apply.click();
    await wait(() => pendingSelection, "selection was not submitted");
    check(await page.getByTestId("camera-surface").locator("img").count() === 0, "old mask survived model switch");
    releaseSelection();
    await wait(async () => (await page.getByTestId("perception-preview-result").innerText()).includes(b), "applied B preview missing");
    await wait(async () => (await page.getByTestId("perception-active-model").innerText()).includes("SegFormer-B0"), "applied B model not acknowledged");
    check(!await page.getByTestId("perception-switch-block").count(), "own confirmed switch was mislabeled as an external conflict");
    wrongFrames = 3;
    await wait(async () => await page.getByTestId("camera-surface").locator("img").count() === 0, "late A result was not rejected");
    await wait(async () => (await page.getByTestId("perception-preview-result").innerText()).includes(b), "B preview did not recover");
    scenarios.push("draft vs applied; switch clears mask; late A cannot become B");
    await location.selectOption("local"); await choice.selectOption("local-ssd");
    check(!(await choice.locator("option").allTextContents()).some(text => text.includes("SegFormer")), "host models leaked into local catalog");
    const beforeLocal = calls.length;
    await wait(async () => !await apply.isDisabled(), "local selection unavailable");
    await apply.click();
    await wait(() => provider === "local", "provider did not change to local");
    await wait(async () => (await page.getByTestId("perception-active-model").innerText()).includes("Pi SSD"), "local selected model not synchronized");
    check(calls.slice(beforeLocal).length === 1 && calls[beforeLocal].path === "/camera/pipeline", "local selection used a second host model store");
    await wait(async () => await page.getByTestId("perception-local-preview").count() === 1, "local processed preview missing");
    wrongLocalFrames = 3;
    await wait(async () => await page.getByTestId("camera-surface").locator("img").count() === 0, "old local producer image was labelled with the new engine");
    await wait(async () => await page.getByTestId("camera-surface").locator("img").count() === 1, "local source identity did not recover");
    scenarios.push("provider-first local model selection uses one pipeline transaction and validates actual PROC engine");
    await location.selectOption("venom"); await choice.selectOption(a); failSelection = true;
    await wait(async () => !await apply.isDisabled(), "return to Venom unavailable");
    await apply.click();
    await wait(async () => await page.getByTestId("perception-operation-error").count() === 1, "failed second phase hidden");
    await wait(async () => (await page.getByTestId("perception-active-model").innerText()).includes("Venom") && (await page.getByTestId("perception-active-model").innerText()).includes("SegFormer-B0"), "partial provider success falsely rolled back or relabelled target");
    check(provider === "venom" && model === b, "partial flow restored an old provider or false model");
    failSelection = false;
    scenarios.push("two-stage failure reports actual provider and actual model");
    await page.getByRole("button", { name: "Odśwież wybór i stan modeli", exact: true }).click();
    await wait(async () => await choice.inputValue() === b, "refresh did not adopt current state");
    await choice.selectOption(a);
    model = a; revision++;
    await wait(async () => (await page.getByTestId("perception-switch-block").innerText()).includes("innym panelu"), "external selection conflict missing");
    check(await apply.isDisabled(), "conflicted draft could overwrite another panel");
    await page.getByRole("button", { name: "Odśwież wybór i stan modeli", exact: true }).click();
    await wait(async () => await choice.inputValue() === a, "conflict refresh failed");
    safety = "ARMED"; await choice.selectOption(b);
    await wait(async () => (await page.getByTestId("perception-switch-block").innerText()).includes("rozbrój"), "stationary ARMED authority did not block selection");
    check(await apply.isDisabled(), "stationary ARMED robot permitted selection");
    safety = "DISARMED"; await choice.selectOption("fast-scnn-floor");
    await wait(async () => (await page.getByTestId("perception-model-block").innerText()).includes("kwalifikacji"), "unqualified model not explained");
    check(await apply.isDisabled(), "unqualified model selectable");
    failCatalog = true;
    await wait(async () => await page.getByTestId("perception-read-error").count() === 1, "catalog read failure hidden");
    check(await apply.isDisabled(), "failed catalog allowed selection");
    failCatalog = false;
    scenarios.push("external conflict, stationary ARM, unqualified model and unavailable catalog block selection");
    await wait(async () => await page.getByTestId("perception-read-error").count() === 0, "catalog did not recover");
    piOffline = true;
    await wait(async () => (await page.getByTestId("perception-active-model").innerText()).includes("nieustalony"), "offline Pi incorrectly shown as the Venom provider");
    await page.getByTestId("perception-selection-error").waitFor();
    await location.selectOption("venom"); await choice.selectOption("yolo26n-sem-ade20k");
    check(await apply.isDisabled(), "offline Pi permitted selection");
    await page.getByTestId("perception-model-details").locator("summary").click();
    const beforeInstall = calls.length;
    await page.getByTestId("perception-install").click();
    await wait(() => installedYolo, "host installation unnecessarily required an online Pi");
    check(calls.length === beforeInstall + 1 && calls[beforeInstall].path.endsWith("/install"), "host install mutated the robot");
    piOffline = false;
    await wait(async () => !(await page.getByTestId("perception-active-model").innerText()).includes("nieustalony"), "Pi selection did not recover");
    await page.getByRole("button", { name: "Odśwież wybór i stan modeli", exact: true }).click();
    await wait(async () => await choice.inputValue() === a, "restored selection missing");
    scenarios.push("offline Pi keeps Venom catalog and explicit host installation available without selecting a model");
    await panel.locator("summary").first().click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: "output/playwright/perception-models-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    check(!await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), "mobile horizontal overflow");
    await page.screenshot({ path: "output/playwright/perception-models-mobile.png", fullPage: true });
    await panel.locator("summary").first().click();
    await page.screenshot({ path: "output/playwright/perception-selector-mobile.png", fullPage: true });
    check(calls.every(call => ["/perception/selection", "/camera/pipeline", "/perception/models/yolo26n-sem-ade20k/install"].includes(call.path)), "unexpected robot mutation");
    check(calls.filter(call => call.path === "/camera/pipeline").every(call => call.body.action === "configure"), "selection started camera");
    return { scope: "synthetic model and camera fixture only; no hardware or downloads", scenarios, calls, sequence };
  } finally { releaseSelection?.(); await page.goto("about:blank"); await page.unroute("**/api/v1/rider/**"); }
}
