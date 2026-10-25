# R1 API and authority

## Host surface

All paths below are under /api/v1/rider. Mutations carry X-Rider-Session from GET /config; STOP/disarm can bypass an obsolete session. Outdated intents are rejected and never automatically retried.

| Host endpoint | Pi endpoint / purpose |
| --- | --- |
| GET/POST /config | Server target allowlist and explicit selection |
| GET /status?section=... | control, overview, calibration, resources, face; only required reads |
| POST /control | /api/safety or /api/control; manual panel ARM/disarm/STOP/clear, move, height, balance |
| POST /settings | /api/calibration/controls; revisioned values/directions/profile operations |
| GET /camera/raw, /camera/proc | Original binary frame and available source metadata |
| POST /perception/prepare | Load the selected host vision model for explicit Analysis; restart an already requested failed observation, never driving |
| POST /goal/{name} | /api/logic/feature/{name}; Pi-owned goal/resource orchestration |
| POST /resource/{name} | /api/resource/{name}; explicit power, stop or release |
| GET /navigation/config, /navigation/state | Pi navigation policy/status plus host detector capability |
| POST /navigation/trial | /api/navigation/trial; original bounded 10 s operator intent |
| POST /navigation/journey | /api/navigation/journey; separate opt-in journey v1, see [contract](JOURNEY_V1.md) |
| GET /diagnostics | /api/services/graph |
| GET /logs/{unit.service} | /api/services/{unit}/logs; explicit bounded journal request |
| GET/POST /face/library, DELETE /face/library/{id} | Pi gesture library |
| POST /face/frame | Pi PNG preview; does not activate LCD |
| GET/POST /face/runtime | Pi runtime settings with revision conflict detection |
| POST /face/play, /face/stop, /face/event | Explicit runtime operations |
| GET /face/runtime/frame | Runtime preview frame |
| GET/POST /conversation/config | Session-scoped optional conversation tools; revisioned ON/OFF, no ARM |

GET /config returns `devices` (public ID, label and demo/device mode),
`device_id`, `session` and optional `selection_warning`. POST /config takes
`{"device_id": "<allowlisted ID>" }` or null to disconnect. This same selection
powers the Demo/Prod panel field and is persisted by Venom. It stops previous
active motion/navigation, invalidates pending commands, rotates the session and
does not activate the next target. An offline previous target yields
`previous_device_stop_unconfirmed`; the UI must show this outcome rather than
claiming confirmed braking. Commands during a switch return
`device_switch_in_progress`; commands from an old session remain rejected.

## Negotiated Pi contracts

Transport requests application/vnd.rider.api+json;version=1. The envelope has meta and data. Domain versions are independent:

| Contract ID | Version |
| --- | --- |
| rider.telemetry, rider.health, rider.system, rider.safety | 1 |
| rider.calibration | 1; data rider.manual_settings 2 |
| rider.resources | 1 |
| rider.catalog, rider.tasks, rider.service_graph, rider.services | 2 |
| rider.face_studio, rider.face | 1 |
| rider.navigation_loop | 1; trial and negotiated journey capability are separate |
| rider.audio | 1; private shared playback/cancel |

Manual settings additionally require parameter_schema.version=1 and control_modes.version=1. Units, bounds, display factors, curves, hold timing and posture descriptors come from Pi. Unsupported versions produce an explicit error; legacy responses are not guessed.

## Motion boundary

Manual commands identify the Pi generation, monotonic sequence, Pi-clock timestamp and bounded TTL. Moves identify a direction/mode and device_settings=true, with the current settings revision; Venom sends no independently scaled velocity. Height/balance are separate explicit actions gated by operator permission. Moving goals require a matching owner, fresh intent and Pi approval; the module never grants ownership automatically.

STOP uses a separate connection pool, cancels pending local actuator requests and bypasses freshness/session checks. Pi owns hardware watchdogs and final execution. Losing the view or focus ends held intent; reconnecting never replays it. The Pi test-motion permission is separate from panel ARM.

## Data and load

Source measurement timestamps and unknown values are preserved. Host last contact is separate from a device observation. Binary frame metadata is passed through; stale/placeholder images are not presented as current.

Default active-view cadence: control 500 ms, calibration 1000 ms, overview 2000 ms, resources/Face 5000 ms. Held intent renewal and timeout come from Pi descriptors. Read cache TTLs differ by domain and concurrent callers share a request. Collection is demand-driven: selecting/mounting a view does not start camera, LCD, a goal or physical motion.

An explicit Analysis selection and observation start prepare the current host model,
including after global memory release. Release stops consumers and unloads models;
unsolicited frames cannot reload the released model. Explicit preparation restores its runtime without changing global admission. Model preparation preserves the
Pi policy revision and acceptance. Passive status reads never prepare a model.

If an observe-only worker stopped while its goal remains requested, explicit
Analysis restarts that observation after model preparation. A running worker,
a driving goal or unknown driving ownership is never restarted by this action.

## Measured image references

GET/POST /navigation/image-reference proxies the Pi-owned visual reference. CAS revision, validation and storage are on Pi. The overlay shows measured track/body widths and marker rows for the recorded lowest pose. Its purpose is visual_reference_only: it does not change navigation policy, lens intrinsics, model decisions or motion approval. SVG points use the original frame coordinates and preserve letterboxing. Operator edits stay a draft until Save on robot; viewing never writes configuration.

### Operator vision policy corrections

Revision 2 presets accept bounded `behavior.overrides` through the existing device-owned navigation configuration transaction. `behavior_catalog.parameter_schema` and `active_parameters` expose the editable and effective values. Revision 1 definitions remain pinned. See [Vision policy parameters](VISION_PARAMETERS.md) for defaults, units, precedence, reset semantics and diagnostic-only object tracking.

When no saved image reference is available, the panel displays the initial lowest-pose measurements: track 16 cm, robot 11.5 cm, camera height 14 cm, and markers 18/20/40/60 cm in the recorded 320×240 image. These defaults are labelled and do not create a stored configuration or a draft on viewing. Inputs and saving remain disabled until the Pi reference revision is known. Saved measurements, including a hidden overlay, take precedence. The normalized overlay also supports scaled 4:3 images; a changed aspect ratio is not silently stretched.