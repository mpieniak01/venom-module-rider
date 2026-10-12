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

The default list is empty. A browser can select only configured IDs; it cannot provide a destination URL. Selection creates a new session and drops old observations/commands. RIDER_DEFAULT_DEVICE can select an explicitly configured ID at startup; it never arms or starts hardware.

RIDER_ALLOWED_ORIGINS is a comma-separated exact allowlist when UI and API origins differ. Mutations obey Venom's environment policy. STOP and disarm remain available in read-only environments. RIDER_ACCESS_TOKEN optionally requires a Bearer token on all module requests; supply it through a trusted server proxy or emergency CLI, never through a public frontend environment variable. Use the host's access controls when exposing it beyond a trusted local environment.

## Hardware-free local demo

From the module checkout:

    source ../../.venv/bin/activate
    make demo

The launcher reuses existing Python and Node environments, starts a loopback Pi fixture and the **real Venom host API/UI**, and prints http://localhost:3300/rider. Defaults are UI 3300, host API 8800 and fixture 18880; flags on scripts/run_demo.py allow different unused ports. Ctrl-C closes only the launcher's own processes.

The demo forces RIDER_DEMO_ONLY=true, disables physical IoT and LLM startup, and accepts only loopback fixtures. Simulated values and actuator acknowledgements are marked DEMO. Fixture state is ephemeral. A fixture result is not evidence of physical motion or device rendering.

### Local panel connected to a real Pi

Use a private device allowlist JSON under ignored config/local/. Pass it explicitly:

    make demo ARGS="--device-file config/local/devices.json --device-id robot"

Alternatively invoke scripts/run_demo.py with those arguments. Without the file flag the launcher always starts the isolated fixture, even if a real device exists in the inherited environment. File mode starts only the Venom API/UI, preserves the hardware authority on Pi, and never automatically arms or starts a resource. It is an operator panel, not an automated physical-motion test runner.

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
