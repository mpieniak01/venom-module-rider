# Venom module: Rider-Pi

An optional Venom module for manual operation of Rider-Pi. Venom supplies the UI, language/theme system and host API; Pi owns hardware, calibration, profiles, motion sequencing and resource orchestration.

## R1

- Manual arrows: click for a pulse, hold for continuous intent, release for braking without disarming. A global red/green ARM switch and persistent STOP remain visible.
- Camera RAW/PROC preview, posture-height commands and roll-compensation controls.
- A permanent status under the driving keypad shows battery charge, Pi axis readings, target posture height and roll compensation. SDK axes keep their producer units; missing or stale readings display as unavailable. Height changes require an explicit Apply. Measurements retains detailed observations, source times, CPU/RAM history and CPU interval peaks.
- Direction-specific calibration in producer-defined units, smoothing curves and profile create/save/apply/rename/delete.
- Goal selection, resource ownership/power, explicit service diagnostics and journal reads.
- Face Studio: built-in and custom gestures, field/track editing, PNG/timeline preview, skins, styles, runtime configuration and explicit LCD playback.
- PL, EN and DE translations. The module uses Venom themes and navigation.

Manual operation does not require chat, voice recognition, an LLM or Rider-PC.

The driving screen keeps the camera next to a **Motion / Model** tuning panel. Motion exposes the current manual direction values, automatic speed and pulse, turn values and shared ramps. Model exposes sensitivity, navigation behavior and installed-model selection without nested tuning disclosures. Advanced geometry and diagnostics remain below the main workspace. Switching page tabs preserves the device's unsaved draft; switching devices clears it.

**Apply** uses one settings flow: stop active movement, pause requested observation, write the changed manual/navigation settings and model selection, then restore observation only. It never resumes driving. Failed writes retain the remaining draft. **Apply and Start** first applies the visible draft and uses the resulting Pi revision; a cold Start prepares analysis and waits for a fresh image for that revision before requesting motion. STOP supersedes a pending preparation or write and prevents subsequent admission or observation restart. Pi still enforces explicit consent, generation and capture/decision deadlines.

Compatible Pi versions expose the same maximum command magnitude (1.0) and pulse duration (500 ms) for manual, automatic and Automat2 control, including avoidance turns. Numeric labels show their allowed ranges. Existing saved values remain the initial form values, and a manual direction preset can retain a lower maximum. Older Pi versions retain their advertised or legacy bounds. Navigation uses its own policy limits rather than the manual direction preset; ramps remain shared. Pulses and reply validity are still shortened by the remaining capture/decision budget (at most 500 ms), and expired decisions cannot continue motion.

## R2 — compute and perception

Pi submits versioned, bounded binary jobs to Venom's shared active LLM and
AudioEngine: text, Whisper STT, Piper WAV TTS and camera **scene description**.
There is no copied Rider-PC model stack. Ordinary inference results carry no
actuator fields; explicit conversation commands use the separate R3 tool path.

The Perception tab reads executor selection from Pi and shows installed,
configured and actually ready models, queues, source age, inference time and
results. Opening it starts no capture/inference. Use Goals & resources to start
S12 scene description or S13 conversation; their Pi services work with the UI
closed. Goal OFF releases its own HTTP/session/media handles without cancelling
the other goal. STOP and operator ARM use their original independent lane.

Configure each device's compute token on the host (RIDER_COMPUTE_TOKEN, or its
private compute_token_env reference in RIDER_DEVICES_JSON), and the same token
in Pi's private rider-compute service env. Never use a public frontend variable.
Pi's services.compute_config CLI stores the single executor address/configuration;
the panel selects a configured ID with an optimistic Pi revision. Stop compute
goals before changing executor. Heartbeats and AI failure cannot change the
choice or operator ARM. A missing model or overload is an explicit error, never
a silent simulated/local fallback.

R2 needs the matching Pi compute v1 producer and Venom core exposing the shared
checked AudioEngine. Without that host speech support, capabilities report
unavailable. Enable/configure speech in the existing Venom voice/model settings;
the module does not create a second engine. A Piper voice only supports its
declared language. Cold model loading is included in inference time.

See [Compute v1](docs/COMPUTE_V1.md) and Pi docs_pl/COMPUTE_VENOM.md for transport,
limits, source-time semantics, lifecycle, provisioning and emergency CLI. Release
Pi/module/core together. Isolated demo engines are explicitly simulated; WSL
inference on synthetic inputs does not constitute physical-device acceptance.

## R3 — optional conversation tools

Enable **Conversation commands** in Perception for the selected robot. The option
defaults to OFF, is scoped to that host/device session, and does not arm the robot.
Use the existing Venom chat and voice interface, for example `Rider, status`,
`Rider, describe scene`, `Rider, say hello`, `Rider, face friendly`, or
`Rider, forward`. PL, EN and DE have an explicit bounded command vocabulary.
Authenticated Pi voice also accepts a bare direction/status command; ordinary
speech continues through the selected Venom model. Ambiguous or compound motion
requests are refused. AI cannot choose consent, actor, duration or command level.

Typed RiderSkill tools and native conversation dispatch use the same module
domain/transport. Observation requires an already active camera; face playback
uses an existing gesture and preserves LCD settings/orientation. Speech uses the
shared Venom AudioEngine and Pi's single playback owner; capture discards audio
overlapping playback, including its short drain period.

Movement is one pulse from the current Pi calibration and needs the operator's
global ARM plus a private `RIDER_AGENT_TOKEN` configured on both host and Pi.
It remains `source=agent`, `execution_context=agent`, `channel=chat|voice`.
The intent binds Pi's agent epoch before planning or queueing. Manual takeover,
option OFF, session/device changes, expiry and cancellation invalidate pending
work. Agent refusal never disarms or stops newer manual motion. `/rider stop`
uses the independent STOP lane even while the conversation option is OFF or a
model/queue is busy. It cannot stop a different authenticated target robot.

See [Conversation tools](docs/CONVERSATION.md) for configuration and contracts.
R2 + R3 require the matching Pi/core companion changes. Automated acceptance uses
isolated fixtures; physical motion, audio quality and energy acceptance remain
separate operator checks.

## Installation

Use Venom's existing environment and optional-module installation workflow. Keep this checkout at modules/venom-module-rider in the selected Venom checkout. Add this manifest to the existing API_OPTIONAL_MODULES list:

    manifest:modules/venom-module-rider/module.json

Retain other installed entries. Set NEXT_PUBLIC_FEATURE_RIDER=true for the UI and run the host's existing optional-module preparation/generation commands before building. The route is /rider, the API prefix is /api/v1/rider. Backend imports need the module root on PYTHONPATH or the package installed in the host virtualenv. Frontend dependencies come from Venom; no duplicate frontend stack is needed.

The manifest requires module API 1.0 and core runtime ABI 1.6.0 or newer. Venom's
`CORE_RUNTIME_VERSION` identifies the module/runtime ABI independently of its
`APP_VERSION` package/release version. ABI 1.6 provides the MAF
`venom_core.execution.agent_contracts.tool_function` contract; install the matching
core contract before updating this module. A core with runtime ABI 1.5 is rejected
before importing Rider's backend. The module does not fall back to Semantic Kernel.

R2/R3 also require the matching conversation-provider and shared AudioEngine companion changes. Pi must provide the negotiated HTTP envelope v1 and the versions listed in [docs/API.md](docs/API.md). Posture descriptors are supplied by the companion Pi change; unsupported capabilities are unavailable in the UI.

## Central integration inventory

The optional read-only 263R hook exposes configured robots and existing cached
connection evidence to newer Venom cores, without probing or selecting a robot.
See [Integration observations](docs/INTEGRATION_OBSERVATIONS.md) and
[opis po polsku](docs/INTEGRATION_OBSERVATIONS_PL.md). Manual control on a host
meeting the required runtime ABI remains independent of the optional inventory
hook.

## Explicit HTTP LAN

An optional `transport_profile="trusted_lan"` device entry with an explicit
`expected_peer_id` supports a named Pi over HTTP without an application SSH tunnel.
It keeps backend tokens and checks passive identity before credential requests or
commands. New connections re-resolve the configured name and retire old pools;
Refresh coalesces recovery and does not call SSH in this profile. Existing entries
retain the default secure profile. See [HTTP LAN](docs/TRANSPORT_HTTP_LAN.md) and
[opis po polsku](docs/TRANSPORT_HTTP_LAN_PL.md) for configuration, limits, errors,
rollback and the matching Pi/core requirements. Phone browser microphones still
require a secure context.

## Reconnecting from the panel

Use the header **Refresh** button after a connection failure. It re-reads the
selected device/session, retires old observations and fetches current control
and settings. It shows progress and a retryable failure if Pi is still unavailable.
It never repeats motion, ARM, camera or navigation commands. Ordinary polling
never runs an external recovery helper. Trusted LAN reads can establish a fresh
verified generation after bounded backoff.

For a device behind an administrator-managed tunnel, its private
`RIDER_DEVICES_JSON` entry may include `reconnect_command`: an absolute path to
an executable on the Venom host. The helper accepts no arguments, must return
within 12 seconds, and must only ensure the configured transport is available.
Keep credentials in the transport's existing private configuration. Use a
supervised transport service so it also reconnects after network interruptions;
keep its restart and connection timeouts within the helper budget. Explicit trusted LAN
connections never invoke this helper. This option is unavailable for Demo devices and is
not exposed as an executable or command input in the browser.

`POST /api/v1/rider/connection/refresh` accepts only an empty JSON object and
requires the current `X-Rider-Session` and the existing module access/origin
checks. It coalesces simultaneous requests. In the secure profile it invokes the configured
helper only when a fresh control read reports `pi_unreachable`. The trusted LAN
profile instead rebuilds its named HTTP transport and verifies identity. A zero helper exit
code is followed by a new negotiated Pi read before reporting recovery. Healthy
secure-profile connections are not restarted. Trusted LAN Refresh always retires
the current pools and creates a newly verified transport generation, including
when the connection is healthy. Device selection and Pi permissions stay intact.
The final Pi read determines recovery even when the helper reports a diagnostic
error. In read-only environments the recovery POST remains prohibited; Refresh
falls back to the existing status GET and reports that transport recovery is
restricted, rather than treating that restriction as a connection failure.

## Device configuration

Configure devices on the **server**, using private environment configuration:

    RIDER_DEVICES_JSON=[{"id":"robot","label":"Rider-Pi","url":"https://rider.local","mode":"device"}]

The default list is empty. The permanent **Demo / Prod** selector uses this single
device list; there is no second environment setting. A browser can select only
configured IDs and cannot supply a destination URL. Unconfigured modes remain
visible and disabled. Selection stops previous active motion and navigation,
creates a new session, and clears observations, pending commands and preview
subscriptions. An unreachable previous Pi produces an explicit unconfirmed-stop
notice; its own watchdog still owns braking.

Venom saves the chosen ID in `data/modules/rider/panel-selection.json`
(`RIDER_SELECTION_FILE` can override the private path). It survives page and host
restarts and is shared by panel clients. Only the choice is stored here;
calibration and loop configuration remain on each Pi. First startup can use an
explicitly allowlisted `RIDER_DEFAULT_DEVICE`. Restore never arms, starts capture,
or resumes a loop. Invalid/removed saved IDs select an available demo or no device,
never a production fallback.

RIDER_ALLOWED_ORIGINS is a comma-separated exact allowlist when UI and API origins differ. Mutations obey Venom's environment policy. STOP and disarm remain available in read-only environments. RIDER_ACCESS_TOKEN optionally requires a Bearer token on all module requests; supply it through a trusted server proxy or emergency CLI, never through a public frontend environment variable. Use the host's access controls when exposing it beyond a trusted local environment.

## Hardware-free local demo

From the module checkout:

    source ../../.venv/bin/activate
    make demo

The launcher reuses existing Python and Node environments, starts a loopback Pi fixture and the **real Venom host API/UI**, and prints http://localhost:3300/rider. Defaults are UI 3300, host API 8800 and fixture 18880; flags on scripts/run_demo.py allow different unused ports. Ctrl-C closes only the launcher's own processes.

The demo forces RIDER_DEMO_ONLY=true, disables physical IoT and LLM startup, and accepts only loopback fixtures. Simulated values and actuator acknowledgements are marked DEMO. Fixture state is ephemeral. A fixture result is not evidence of physical motion or device rendering.


### Recorded camera replay

The control screen combines camera preview, loop Start/Stop, and manual arrows.
While a Pi loop is active, the processed preview reuses its fresh, matching
analysis. It briefly waits for the next fresh Pi result instead of starting a competing
inference that could reject a Pi decision. The response wait is bounded to
500 ms; stale images remain unavailable.
**Take manual control** retains camera ownership, stops loop goals and requests
manual ARM through the existing Pi control contract. Giving control to Venom
uses the independently calibrated drive policy and timed navigation ARM.

For a hardware-free replay, keep a recorded `last_frame.jpg` and a
`manifest.json` containing its `original_mtime` outside Git. Supply the actual,
hardware-free `apps/compute/decision_lease.py` from the matching Pi checkout:

    make demo ARGS="--replay-dir /path/to/recording --decision-lease-source /path/to/Rider-Pi/apps/compute/decision_lease.py"

Configure the local segmentation model with `RIDER_VISION_MODEL` and
`RIDER_VISION_SHA256`. Replay uses the real Venom vision endpoint and Pi's
monotonic decision permit; actuator callbacks only change fixture state.
The preview labels a replay and retains the original recording timestamp.
Replay requires the local fixture; it can coexist with a production allowlist
when `--with-demo` is explicitly enabled. It always targets the demo ID.
Defaults remain observation, disarmed and no running camera or loop.

For cross-repository integration checks, set `RIDER_TEST_PI_LEASE` to that Pi
source file when running `make check`. The scenarios test correlated frames,
three clear decisions, obstacle STOP, deadline STOP, no reconnect resume and
manual takeover with a deterministic detector fixture. These scenarios verify
control behavior; a saved real image is separately checked through the actual
segmentation/quality detector. The extra integration test skips when no explicit
Pi source is provided.

### Local panel connected to a real Pi

Use a private device allowlist JSON under ignored config/local/. Pass it explicitly:

    make demo ARGS="--device-file config/local/devices.json --device-id robot"

Alternatively invoke scripts/run_demo.py with those arguments. Without the file flag the launcher always starts the isolated fixture, even if a real device exists in the inherited environment. File mode starts only the Venom API/UI, preserves the hardware authority on Pi, and never automatically arms or starts a resource. It is an operator panel, not an automated physical-motion test runner. The configured-device host defaults to the `full` LLM runtime profile so the Venom cockpit can select installed models. Use `--runtime-profile light` or `--runtime-profile llm_off` explicitly to restrict the host. The isolated fixture always remains `llm_off`; model warmup and background optional services remain disabled by this launcher.

To keep both modes available in one local panel, configure the private production
allowlist once and add the local fixture:

    make demo ARGS="--device-file /private/devices.json --with-demo"

The initial selection is **Demo**, unless a saved operator choice exists.
Switch Demo/Prod in the panel without changing files or restarting services.
The fixture and production target have separate credential references; built-in
demo tokens are never used for production. Recorded replay flags apply only to
the local fixture. Automated switching checks use two fixtures; registering a
production target does not constitute a physical-device test.

Service and resource operations have a separate bounded response timeout. Motion
and STOP retain their fast transport path. `pi_operation_timeout` means the result
is unconfirmed: the panel refreshes observations and does not resend the operation.

## Quality checks

    source ../../.venv/bin/activate
    make check

This runs Ruff, formatting, mypy, targeted Python tests with a 90% coverage floor, TypeScript, ESLint and frontend contract/hold-controller tests, using host dependencies. Tests never import robot/UART/GPIO drivers. Browser acceptance uses the demo; explicit ARM and actuator requests stay within the fixture.

Local session reports, screenshots and operator/device settings stay under ignored docs_dev/, output/ and local environment files. The host integration also requires make pr-fast.

## Camera, decisions and a forward trial

The control workspace keeps the processed camera view, manual takeover and
navigation decisions together. This loop chooses a short forward pulse or STOP;
it does not plan a route, estimate distances or steer around obstacles.

- **Drive forward · 10 s** is one explicit operator action. Pi prepares the
  camera and producer, validates the saved calibration and executor, and obtains
  one timed navigation ARM. Motion requires three fresh CLEAR decisions; a
  blocked or unknown image never grants GO. The original ARM window includes
  startup time, so this is an upper bound rather than ten seconds of movement.
- **Preview without driving** is optional and starts passive observation.
  It is not a prerequisite to clicking Drive forward. Image GO during observation
  still means an execution reply of STOP. There is no per-trial confirmation dialog.
- **Stop loop and camera** cancels a pending trial, stops motion before disabling
  drive and observation, closes the preview, and releases the camera reservation.
  Pi confirms hardware capture and the unused frame distributor have stopped.
  Another task's camera ownership is reported rather than forcibly released.
  A new explicit observation or drive start prepares the camera again.
  Global STOP and manual takeover remain separate actions; neither permits
  automatic resumption.

The decision cards separate the image verdict, the actual Venom-to-Pi reply and
the executor's reported motion. Floor/confidence percentages, consecutive clear
frames, inference time, request roundtrip and response age explain GO or STOP.
A GO reply requests forward motion at the saved SDK command magnitude, yaw zero,
bounded by the smaller of the saved pulse limit and remaining decision permit.
An accepted request or reported movement is not a travelled-distance measurement.
Stale and latched records never appear as current GO permission.

The saved Pi policy and its matching model/parameters remain requirements for
automatic driving. Changing critical settings invalidates that acceptance.
**Prepare automatic drive** leads to an explicit observation and image/path
confirmation below the camera; it does not arm or start a trial. The saved
revision is shown after acceptance. Manual controls remain independent of that
vision acceptance. See [control workspace](docs/CONTROL_WORKSPACE.md).

A single execution summary says whether Venom can send movement, with manual
permission shown separately. Manual ARM cannot authorise the autonomous loop.
The preview explains semantic classes: green is confident floor, blue is a
ground-like class outside the current GO rule (road, sidewalk, rug), red is
another class and yellow is uncertain. Blue or red pixels are model outputs,
not measured obstacles. Dominant ROI class/fraction exposes classification
mistakes, without relaxing the GO rule or decision deadlines.

## Operation and removal

Settings are saved only after **Save**, with the Pi revision included. Unsaved drafts are labelled, and a concurrent change blocks stale saves until the draft is discarded. Profiles are stored and applied by Pi; no second calibration database exists in Venom.

Views poll only required observations, share cached/deduplicated Pi reads, stop when hidden, and maintain no idle background collector. Camera OFF/unknown and stopped Face runtime suppress frame requests. A separate transport lane prioritizes STOP. Accepted/pending SDK requests never mean physically completed motion.

To disable the module, remove its backend manifest entry, set its UI flag to false, regenerate the host module list, and restart/rebuild the host. Pi's emergency panel/CLI remains the hardware path. Removing the module does not erase calibration or profiles on Pi.

For a configured device host, `--enable-audio` enables the shared Venom STT/TTS stack. Audio stays off by default and the isolated demo rejects this option. This does not start Pi media goals or arm the robot.

## Robot-owned motion permission

Rider-Pi's sole UART executor owns operator authorization. Manual commands are
forwarded without a telemetry GET prerequisite; the robot validates generation,
sequence, TTL, settings revision and actuator readiness and returns the ACK.
A missing observation retains the last permission with an explicit warning.
It never converts ARM to DISARMED or replays a failed gesture. Execution inhibition
and connectivity are shown separately. Tests require independent permission and
chat/voice cannot arm the robot. Deploy the Pi authority change before this client.

### Experimental ground geometry

The panel can select semantic surface classes or relative depth-plane geometry
when the selected Pi exposes the `perception` policy. Pi owns the saved method,
ROI and thresholds; changing them resets the run and prior acceptance. Legacy
policies migrate in memory to semantic defaults without rewriting the file.

Depth-plane geometry uses the pinned Depth Anything V2 Indoor Small model on
Venom, checks inverse-depth plane agreement in the corridor, and exposes relative
deviation thresholds. It does not report centimetres or calibrated stopping
distance. Geometry can produce image GO in observation, where its actuator reply stays
STOP. The forward trial uses the same Pi-owned acceptance, three fresh CLEAR
frames, model hash and bounded decision lease as semantic driving. The trial
button releases existing manual ARM, confirms disarming, then obtains navigation
authority for up to 10 seconds. STOP during transfer cancels subsequent ARM.
Manual takeover stops navigation and returns authority to the arrow controls.
Neither image GO nor saving trial conditions starts physical movement.

Pulse duration and decision validity are separate limits. Every SDK pulse remains
bounded by the saved motion limit; a fresh GO may renew the run after a pulse
ends while the previous decision is still valid. Expired decisions still latch
STOP and cannot revive the run.

Install the module's `vision_depth` extra in the host virtualenv, then provision
explicitly; startup and requests never download weights:

```bash
python scripts/provision_depth_model.py --output data/depth-small --revision 8078d68a9c75a972131914f6afd0c1723be0da7f
PYTHONPATH=../..:. python scripts/replay_depth.py --dataset /path/to/recordings --output output/depth-replay
```

Replay uses synthetic frame identities and cannot validate physical movement,
capture freshness or braking. Model files, recordings and reports stay ignored.


### Forward-trial panel regression

The panel keeps analysis, forward trial and loop stop on the camera/manual-control
screen. Detailed geometry diagnostics are collapsed; stale image results are not
shown as current movement permission. Manual ARM does not require a separate
operator disarm before clicking the trial.

The self-contained browser regression intercepts every Rider API request and
covers manual-to-navigation transfer and STOP during transfer. It contacts no
physical robot. With the local panel running, use Playwright CLI:

    playwright-cli -s=rider-regression open http://localhost:3300/rider
    playwright-cli -s=rider-regression run-code --filename=tests/browser/navigation-takeover.js
    playwright-cli -s=rider-regression close

Run from the module checkout. Browser launch settings are environment-specific;
the Node syntax check is included in the check-web target.


### Cold journey start regression

Explicit **Prepare** starts observation. After fresh matching frames arrive,
the main journey button offers **Confirm and start** when acceptance is missing.
One explicit click saves image/steering confirmation before requesting the journey
with the saved revision. It never starts from a rejected write or mismatched frame.
A rejected save restores the observation requested by the operator and permits
retry. STOP cancels the whole transaction, including the confirmation write phase;
a late completion cannot enqueue a journey or restart observation.

Before releasing changes to journey preparation, run
`tests/browser/journey-start-prod.js` against both connection modes. The fixture
begins with stopped, unverified revision 134, a historical GO result and Pi GET
metadata. It checks missing/mismatched frames, a rejected save and recovery,
retry, combined confirmation/Start, STOP during confirmation and cancellation of delayed Start. Every Rider API
request is intercepted; these runs never contact the physical executor.

    playwright-cli -s=rider-regression open http://localhost:3300/rider
    playwright-cli -s=rider-regression run-code --filename=tests/browser/journey-start-prod.js
    playwright-cli -s=rider-regression goto 'http://localhost:3300/rider?fixtureMode=demo'
    playwright-cli -s=rider-regression run-code --filename=tests/browser/journey-start-prod.js
    playwright-cli -s=rider-regression close

Run `tests/browser/operator-workspace.js` as well for camera/keypad stability,
compact settings and nonoverlapping journey labels at seven viewport sizes.
`make check-web` checks script syntax and web unit tests; it does **not** execute
these browser flows. Fixture success does not replace physical operator acceptance.


### Bounded vision trial

The operator starts a trial with one POST to /api/v1/rider/navigation/trial. The request identifies the current Pi generation, configuration and manual-settings revisions, a unique request_id, a fresh Pi-clock timestamp/TTL, and explicit operator consent/plan. The live executor must advertise scoped_stop_generation_supported and remote_decision_deadline_supported. Pi prepares its existing camera/goal/configuration services and grants one navigation ARM for at most 10 seconds. Requests are not replayed on refresh or reconnect.

During that original trial, fresh visual STOP sends an immediate zero goal while analysis continues. Three new CLEAR observations may resume the existing small forward pulses. Operator STOP, manual takeover, loss of current decisions/communication, invalid contracts, executor errors and ARM expiry end the trial; no automatic ARM or restart follows. Legacy lease adapters without scoped pause retain terminal STOP.

Active processed previews consume the existing inference result instead of fetching another Pi RAW. X-Rider-Run-Id, X-Rider-Sequence, X-Rider-Frame-Id and X-Rider-Config-Revision identify the image; X-Rider-Analysis-Json contains its corresponding assessment. Relative preview age uses host monotonic elapsed time plus Pi-reported input age, and does not measure the uplink. Pi remains authoritative for end-to-end decision freshness. A diagnostic host-received age is a lower bound, not proof of source-capture freshness.

Depth-plane geometry remains experimental. The recorded floor-seam false STOP is retained as a regression; geometry diagnostics now distinguish nearer and farther deviations. No threshold, speed, pulse, ramp or freshness limit is loosened by this change.


### Accepted scene and release scope

On 2026-10-15 the operator confirmed that the forward GO/STOP process is stable
in the currently calibrated floor scene on the physical Rider-Pi. This accepts
that scene and the current low-speed setup, not arbitrary floors, lighting,
camera mounts or measured stopping distances.

A still-fresh decision with less than 50 ms left for a useful pulse causes ZERO
and a bounded wait for another fresh decision, not an immediate trial failure.
It cannot extend old GO, renew ARM or exceed the saved speed and pulse limits.
Loss of feedback, invalid identities and executor faults remain terminal STOP.
The companion Pi implementation is required; see Rider-Pi PR275.


### Alternative depth models (offline research)

[DA3 comparison runner](docs/DA3_RESEARCH.md) provides explicit DA2 / DA3 Small /
Base / Metric Large selection for recorded-scene tests.
[Measured results](docs/DA3_RESULTS.md) retain DA2 as the production baseline.
The research lane does not change the live panel, Pi policy or movement permissions.


### Editable zones and directional trials

See [directional vision](docs/DIRECTIONAL_VISION.md) for the camera-zone editor,
shared left/center/right assessment and versioned paired directional trials (v2 legacy turns, v3 forward corrections).
Avoidance is off by default and requires physical acceptance on the actual mount.

## Manual continuous drive

The camera workspace provides one pad with a **Pulse / hold** / **Continuous drive** selector.
Continuous drive selects a direction with one click; clicking the same direction or
using the centre STOP brakes and keeps ARM. A pulse replaces the continuous
selection permanently. Left/right turn in place using Pi's current settings.
This is manual control and does not use vision obstacle avoidance.

The panel distinguishes selected intent, Pi acknowledgement and the latest
reported actuator output. Acknowledgement is not proof of physical travel.
Continuous drive requires `panel_control.version == 1` on Pi. With
`operator_intervention_version == 1`, the first direction gesture supplies fresh
manual consent and ends pending/active navigation without a separate ARM or
Take manual click. Older Pi uses explicit ARM or Take manual control in this
browser. Other panels may always use global STOP; a new takeover invalidates
the previous generation before submitting its requested direction.
Legacy Pi retains the pulse/hold controls and reports continuous drive unavailable.

The direction lives only in memory. Closing, hiding or leaving the control
view, window blur, loss of communication, rejection or a changed generation or
settings revision cancels it. A return to the page or connection never resumes
movement. Changing RAW/Venom preview within the same panel keeps the direction.
Pi enforces its existing hold timeout even when release cannot be delivered.

`tests/browser/manual-continuous.js` is a Playwright CLI flow for the real host
UI connected exclusively to the loopback demo. It refuses physical allowlists.
Automated acceptance uses fixture outputs; physical acceptance remains a separate
operator step before release. Publish candidate PRs without auto-merge when
that physical acceptance is pending. Keep the stable deployed SHA; test an
explicit preview candidate, stop/disarm before rollback, then select the prior
version through the existing deployment procedure. Never reset an active robot
or delete a branch as a substitute for rollback.

Panel identities provide concurrency ownership within the single-operator control domain. Caller authorization remains the existing module access guard; a panel identity is not a credential or a separate user account.

## Camera sample sessions

Collect the exact visible RAW source next to the camera, with a bounded sequence
option. Samples, label revisions and ZIP exports live in a durable Venom directory
outside Git; Pi does not become an image archive. Collection requires no model or
ARM and is independent of the motor intent/STOP lane. Browse and label sessions
with Pi disconnected. See [Sample sessions v1](docs/SAMPLES_V1.md) for operator
flow, limits, source clocks, local import, API and rollback.

## Source recordings and offline replay

In **Samples**, **Record session** retains one RAW MJPEG source, its indexed
frame timestamps/hashes and telemetry. The current recorder targets 5 received
frames/s; gaps and interrupted recordings remain visible. Each session is kept
separately. Recording does not start a camera, load a model or enable movement.
It no longer saves a second automatic series of photos. The camera button saves
one additional photo only on request.

Play or seek a finalized recording without a model or Pi connection. Video frames
are read directly from source byte ranges; browsing does not extract another set
of JPEG files. Start **Analyze entire video as a new study** explicitly to process
all indexed frames with a frozen model/profile/configuration. Changing settings
requires a new run and cannot overwrite or relabel an earlier result.

The green/yellow/red histogram reports the count and share of clear/uncertain/
obstacle frames from the selected saved run. Select a colour or frame to compare
the original with that run's overlay. Rejected images remain visible. These are
image classifications, not a measure of accuracy or authorization to drive.
Video studies keep chunked result rows and compact transparent classification
layers, without copying RAW frames, saving a second full-image video, or retaining
a depth-array/PNG pair for every frame. The browser composites each saved layer
with the hash-matched source only while inspecting it. Storage and execution limits
produce an explicit incomplete/failed run, never silent truncation.

## Stored session analysis

The sample library can explicitly analyse stored originals with retained DA2 or DA3 Small profiles, compare immutable runs and export reports while the Pi is disconnected. See [the analysis contract](docs/ANALYSIS_V1.md). Collection and analysis are separate steps; offline results never grant movement permission.

Automatic events use the same sample registry: [Automatic capture v1](docs/AUTOMATIC_CAPTURE_V1.md).

Offline mapping qualification: [geometry research v1](docs/MAPPING_RESEARCH_V1.md).

## Operator workspace

See [control workspace](docs/CONTROL_WORKSPACE.md) for Manual/Automatic/Automat2 views, fixed directional controls, the three STOP meanings and hardware-free layout acceptance.

### Panel action styles

The Rider panel wraps Venom’s shared `components/ui/button` component, including
its standard variants and capsule shape, without a custom inset selection shadow.
It uses two shared action sizes: small (36 px) for tools and large
(44 px) for operator commands. Direction keys retain their fixed spatial pad and
at least 44 px height. Use the shared `Button`, `Pill` and `Panel` primitives
for consistent shapes, status colors and contextual help. Emergency STOP stays
red; camera power and automatic pause use the warning tone. Button `title` help
appears after a short hover or keyboard focus, closes on pointer press, and keeps disabled controls on the
native title. Tips do not intercept emergency Escape or pointer input.

`tests/browser/ui-consistency.js` is a Playwright CLI `run-code --filename`
scenario against an isolated loopback demo. It mocks every Rider request and
checks the two sizes, responsive geometry, tips, tab styles and emergency Escape.
Run it alongside `control-workspace.js`; `make check-web` validates its syntax.

### Form controls across Rider tabs

Use `Input`, `Select`, `Textarea`, `Checkbox`, `Range` and `Field` from the module
primitives for every form. `Input` wraps Venom's shared input; the native select,
checkbox and slider preserve existing browser events, option values, producer
bounds and fieldset semantics. All fields share theme colors, 36 px height,
4 px corners, labels, focus and disabled/error states. Text areas grow vertically.
Keep action buttons on the existing shared Venom button wrapper; direction pads,
tabs and selectable media cards retain their specialized interaction geometry.

`tests/browser/form-consistency.js` checks populated forms on a demo-only host:
all six tabs, expanded automatic/camera settings, face controls and sample labels.
It verifies native draft events, producer minimums, fieldset disabling, field
geometry, accessible labels and light/dark colors at desktop and phone widths.
Every Rider mutation is rejected; sample-library GETs are fixtures. Run with
Playwright CLI `run-code --filename` alongside the existing control-workspace,
camera-lifecycle and ui-consistency scenarios.

## Operator workspace on small screens

The driving workspace opens in its focused view on narrow or short windows. The
expand/restore button switches back to the host layout. Steering arrows and motor
STOP stay in place while manual/automatic views and status messages change.
Current messages use a stable inline slot below the arrows. Detailed errors,
model settings and recording are disclosures below the camera/keypad. Small
landscape windows use page scrolling while retaining readable controls and the
sticky emergency STOP.

The device transport is independent of the web stack. After a host restart,
restore the configured device connection before diagnosing disabled controls.
An unavailable connection keeps motion controls disabled; reconnecting does not
arm the robot or replay a previous movement command.


## Automat2 — continuous supervised journey

The separate Automat2 view starts a low-speed journey with local obstacle
avoidance until STOP or a terminal freshness/avoidance fault. The existing
ten-second trial remains available. No periodic ARM or timer extension is used.
Pi and Venom negotiate journey v1; mismatched versions show a clear unavailable
state. Per-obstacle correction budgets reset only after fresh qualified
straightening, and stale mission/epoch/host/source identities cannot resume a
journey. Camera and manual controls remain in the same workspace.

See [Journey v1](docs/JOURNEY_V1.md) for protocol, limits and operator acceptance.
`make check-journey` runs the focused hardware-free regression. The paired lease
fixture optionally accepts `RIDER_TEST_PI_LEASE` pointing to the actual matching
Pi source, with its repository root on `PYTHONPATH`; ordinary tests never locate
or connect to a robot. Physical acceptance remains a separate operator step.

## Recorded motion study

Calibration offers compact fields and synchronized recorded motion comparison.
See [Motion study v1](docs/MOTION_STUDY_V1.md) for capture, A/B, archive contracts,
quality limits and isolated acceptance.

Qualified body orientation and replay share the same timeline. See
[Attitude replay v1](docs/ATTITUDE_REPLAY_V1.md) for the producer convention,
relative reference, unknown-data fallback and resource limits.

CPU/RAM continuity and diagnostic semantics: [system history](docs/SYSTEM_HISTORY.md).


Jedna sesja badania z telemetrią i serią klatek RAW: [Wspólny zapis](docs/UNIFIED_RECORDING.md).

## Native preview recovery regression

The native preview reconnect watchdog advances only after a source/analysis-matched,
fresh JPEG is decoded and committed. Incoming bytes or increasing sequence numbers
alone cannot renew it. The original image keeps its original freshness deadline;
a failed stream cannot indefinitely present it as current.

Before releasing native media changes, run `tests/browser/native-stream-recovery.js`
with Playwright CLI against the local panel. Every Rider endpoint is intercepted.
The fixture starts from a retired journey with an active camera, then sends
continuous expired parts, mismatched analysis and undecodable JPEGs. It requires
GET reconnect and a fresh replacement after each failure, with zero robot mutations.
`make check-web` includes syntax validation; the browser flow must be run separately.

## Experimental temporal driving profiles

Both panels can select a Pi-owned Legacy/Cautious/Balanced/Fluid policy.
New profiles add source-region history, bounded SLOW and current-image boundary
diagnostics while retaining legacy compatibility and independent motion gates.
See [Temporal navigation v1](docs/TEMPORAL_NAVIGATION_V1.md) for capability,
compact-map replay, tests and the required local acceptance.

## CUDA requirement for Rider perception

Live perception, model qualification and archive analysis require CUDA. Missing CUDA or a CUDA ONNX execution provider is an error; model inference does not fall back to CPU. Install the module vision extra with onnxruntime-gpu==1.27.0 for the CUDA 13 environment; do not install the CPU onnxruntime wheel into the same interpreter. GPU execution is verified from the loaded model/session, not from catalog labels. Existing CPU performance reports do not qualify the CUDA deployment.


### Observation continuity during journey preparation

Pi capability `approval_without_restart_version: 1` allows image/steering
acceptance to save only approval metadata without stopping the running observer.
Model, profile, ROI, speed and deadline edits retain their existing stopped-write
contract. Older Pi versions keep the previous acceptance flow.

A cold S15 → S14 producer handoff still uses the existing bounded Pi preparation.
The camera and host inference can continue displaying a separate passive preview
while preparation is fresh and the executor is disarmed or qualifying. A healthy
Pi producer then takes over the preview; diagnostic results never qualify motion,
carry journey authority or renew a movement deadline. STOP still cancels Start.


## Manual pulse trials A/B/C

The Motion panel offers three Pi-owned recipes: A is one 500 ms pulse, B is two 250 ms pulses with 100 ms zero gaps, C is five 100 ms pulses with 100 ms zero gaps. Selecting a recipe prepares the visible forward-duration draft. Existing direction strength and ramps remain the common settings; other directions and navigation/model configuration are preserved. Editing the duration away from the recipe shows custom settings.

Start trial applies the manual settings, opens a motion-only recording and submits one fresh, explicitly consented Start. It does not enable camera/video. Pi schedules the series in its existing executor, confirms zero output before each gap and ends the trial's permission. Global STOP, manual intervention, lost authority and the fixed 1500 ms series deadline retire all remaining pulses. A late HTTP reply or reopening the panel cannot repeat a Start. Saved manual presets are not overwritten. The form retains the last applied values after the trial.

Calibration's existing A/B comparison can select a whole series, show completed pulse counts and actual times, and compare common settings. Pi-relative events are anchored to host reception; this is not proof of synchronized IMU timing or physical stopping/distance. Sparse IMU data is not converted into invented smoothness or distance.

The module exposes GET /api/v1/rider/motion/trials/profiles, POST /api/v1/rider/motion/trials and GET /api/v1/rider/motion/trials/{request_id}. Pi recipes/status use rider.pulse_trials version 1. Start uses the existing manual safety/consent channel and settings revision. Older Pi without this capability keeps ordinary manual control and does not expose trial Start.
