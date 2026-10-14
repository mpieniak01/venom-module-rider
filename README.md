# Venom module: Rider-Pi

An optional Venom module for manual operation of Rider-Pi. Venom supplies the UI, language/theme system and host API; Pi owns hardware, calibration, profiles, motion sequencing and resource orchestration.

## R1

- Manual arrows: click for a pulse, hold for continuous intent, release for braking without disarming. A global red/green ARM switch and persistent STOP remain visible.
- Camera RAW/PROC preview, posture-height commands and roll-compensation controls.
- Device observations, source times, CPU/RAM history and CPU interval peaks. Missing data remains missing.
- Direction-specific calibration in producer-defined units, smoothing curves and profile create/save/apply/rename/delete.
- Goal selection, resource ownership/power, explicit service diagnostics and journal reads.
- Face Studio: built-in and custom gestures, field/track editing, PNG/timeline preview, skins, styles, runtime configuration and explicit LCD playback.
- PL, EN and DE translations. The module uses Venom themes and navigation.

Manual operation does not require chat, voice recognition, an LLM or Rider-PC.

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

The manifest uses module API 1.0 and minimum core runtime 1.5, which differs from Venom's package version (validated on 1.9). R2/R3 also require the matching conversation-provider and shared AudioEngine companion changes. Pi must provide the negotiated HTTP envelope v1 and the versions listed in [docs/API.md](docs/API.md). Posture descriptors are supplied by the companion Pi change; unsupported capabilities are unavailable in the UI.

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
