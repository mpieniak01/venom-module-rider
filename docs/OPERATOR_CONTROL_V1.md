# Operator control v1

The selected Pi advertises the optional contract in
`control.safety.operator_control.version = 1`. This is a capability, not permission
or confirmation that a navigation worker is running. The current Pi-owned
`operator_control.state` is `idle`, `paused`, `qualifying` or `active`; its
`operation_id`, `generation` and `reason` correlate the accepted operation.
`panel_control.holder` remains authoritative while navigation owns motion.

The module forwards these commands through its existing `POST /control` route
and the existing Pi `/api/control` domain:

| Intent | Fields in addition to the standard panel envelope |
| --- | --- |
| Pause motion, retaining perception | `action: "navigation_pause"` |
| Request a fresh qualification | `action: "navigation_resume"` |
| Replace autonomous movement with a manual correction | `action: "move", intervene: true`, plus the existing direction, mode and `device_settings` |

The envelope retains `request_id`, `source: "manual"`,
`execution_context: "panel"`, `panel_id`, `generation`, `seq`, `ts`, `ttl_ms` and
`settings_revision`. The proxy neither changes timestamps nor impersonates the
navigation owner. The Pi admits the request under its own safety and motion
locks. A pause or explicit `move` intervention may include
`supersedes_operation_id`, the exact pending resume request identifier. This
allows the Pi to retire that operation when its new generation has not yet
reached the browser. It is scoped to the original generation, panel holder,
request freshness and operation identity; it is not a general stale-generation
exception. No status GET or retry is required before the urgent correction. Pause/intervention revoke the old generation and leave the automatic
controller paused. Resume requires existing permission; it never ARM/disarms,
clears E-Stop, renews a timed lease or replays old movement.

A cold `POST /navigation/trial` opts in by including both
`operator_control_version: 1` and a nonblank `panel_id`, only after capability
negotiation. Legacy requests omit these fields. A running opted-in Pi worker
advertises `operator_control_version: 1` in `/navigation/state.status`; capability
alone does not mean a warm resume is available. Older Pi versions keep their
existing read, preview, control and global STOP contracts; the panel must not
promise warm intervention without the advertised capability.

Each new qualification receives a new Pi-generated `run_id`, while retaining the
camera session and loaded model. Venom's existing consecutive-clear state is
scoped to device/run and its model, configuration and source signature. Three
new consecutive admissible results are required. Pi additionally owns the new
capture cutoff, distinct-source checks, freshness, local watchdog and final
actuation decision. Cached inference may save model work, but cannot transfer a
previous run's clear count or renew a frame's deadline. A GO response is not
proof of physical completion or clearance.

The host invalidates pending motion requests when an operator intervenes and
rejects a late cold-trial response after that boundary. These guards supplement
Pi generation checks; cancelling an HTTP request alone cannot revoke Pi motion.
The urgent operator lane uses the existing independent STOP transport with at
most one operator request in flight, leaving its second connection available to
global STOP. Global STOP does not acquire the operator lock and remains usable
with a stale host session. Pause and correction leave automatic sample capture
active; global STOP retains the existing sample invalidation behavior.

Raw, processed vision and hiding the preview are presentation choices. Camera
OFF uses the existing pipeline action, invalidates pending host motion before
preview teardown, and relies on Pi to retire dependent motion/perception before
capture shutdown. Camera ON does not resume motion. Camera OFF is confirmed only
by the authoritative pipeline response; conflicts or unconfirmed shutdown are
reported as failures. Normal correction, pause, resume and trial completion do
not use camera shutdown or service stop/start.

The replay demo implements this contract in memory, with one persistent producer
for opted-in trials. Its warm pause and resume retain the producer task, camera
state and original timed deadline. It is a test fixture, not a hardware control
or physical acceptance result.