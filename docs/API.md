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
| POST /goal/{name} | /api/logic/feature/{name}; Pi-owned goal/resource orchestration |
| POST /resource/{name} | /api/resource/{name}; explicit power, stop or release |
| GET /diagnostics | /api/services/graph |
| GET /logs/{unit.service} | /api/services/{unit}/logs; explicit bounded journal request |
| GET/POST /face/library, DELETE /face/library/{id} | Pi gesture library |
| POST /face/frame | Pi PNG preview; does not activate LCD |
| GET/POST /face/runtime | Pi runtime settings with revision conflict detection |
| POST /face/play, /face/stop, /face/event | Explicit runtime operations |
| GET /face/runtime/frame | Runtime preview frame |
| GET/POST /conversation/config | Session-scoped optional conversation tools; revisioned ON/OFF, no ARM |

## Negotiated Pi contracts

Transport requests application/vnd.rider.api+json;version=1. The envelope has meta and data. Domain versions are independent:

| Contract ID | Version |
| --- | --- |
| rider.telemetry, rider.health, rider.system, rider.safety | 1 |
| rider.calibration | 1; data rider.manual_settings 2 |
| rider.resources | 1 |
| rider.catalog, rider.tasks, rider.service_graph, rider.services | 2 |
| rider.face_studio, rider.face | 1 |
| rider.audio | 1; private shared playback/cancel |

Manual settings additionally require parameter_schema.version=1 and control_modes.version=1. Units, bounds, display factors, curves, hold timing and posture descriptors come from Pi. Unsupported versions produce an explicit error; legacy responses are not guessed.

## Motion boundary

Manual commands identify the Pi generation, monotonic sequence, Pi-clock timestamp and bounded TTL. Moves identify a direction/mode and device_settings=true, with the current settings revision; Venom sends no independently scaled velocity. Height/balance are separate explicit actions gated by operator permission. Moving goals require a matching owner, fresh intent and Pi approval; the module never grants ownership automatically.

STOP uses a separate connection pool, cancels pending local actuator requests and bypasses freshness/session checks. Pi owns hardware watchdogs and final execution. Losing the view or focus ends held intent; reconnecting never replays it. The Pi test-motion permission is separate from panel ARM.

## Data and load

Source measurement timestamps and unknown values are preserved. Host last contact is separate from a device observation. Binary frame metadata is passed through; stale/placeholder images are not presented as current.

Default active-view cadence: control 500 ms, calibration 1000 ms, overview 2000 ms, resources/Face 5000 ms. Held intent renewal and timeout come from Pi descriptors. Read cache TTLs differ by domain and concurrent callers share a request. Collection is demand-driven: selecting/mounting a view does not start camera, LCD, a goal or physical motion.
