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

Manual operation does not require chat, voice recognition, an LLM or Rider-PC. R1 provides the common domain for later integrations; compute offload and chat/voice control belong to subsequent increments.

## Installation

Use Venom's existing environment and optional-module installation workflow. Keep this checkout at modules/venom-module-rider in the selected Venom checkout. Add this manifest to the existing API_OPTIONAL_MODULES list:

    manifest:modules/venom-module-rider/module.json

Retain other installed entries. Set NEXT_PUBLIC_FEATURE_RIDER=true for the UI and run the host's existing optional-module preparation/generation commands before building. The route is /rider, the API prefix is /api/v1/rider. Backend imports need the module root on PYTHONPATH or the package installed in the host virtualenv. Frontend dependencies come from Venom; no duplicate frontend stack is needed.

The manifest uses module API 1.0 and minimum core 1.5; R1 integration is validated on Venom 1.9. Pi must provide the negotiated HTTP envelope v1 and the versions listed in [docs/API.md](docs/API.md). Posture descriptors are supplied by the companion Pi change; unsupported capabilities are unavailable in the UI.

## Device configuration

Configure devices on the **server**, using private environment configuration:

    RIDER_DEVICES_JSON=[{"id":"robot","label":"Rider-Pi","url":"http://rider.local:8080","mode":"device"}]

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
