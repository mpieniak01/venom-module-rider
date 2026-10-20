# Automat2: continuous supervised journey v1

[Instrukcja operatora (PL)](JOURNEY_OPERATOR_PL.md)

Automat2 is a separate panel view and Pi contract. Manual controls and the
existing ten-second trial retain their behavior. A journey has no overall time
limit; every individual forward/steering pulse retains the configured low speed
and short deadline. This is reactive local obstacle avoidance, not a route or map.

## Prepare in the panel

Open **Automat2 → Open journey settings**. Enable avoidance and choose the
geometry method explicitly. **Current ground and footprint** requires measured
normalized intrinsics and footprint radius; incomplete values can be saved for
observation but cannot authorize ground-based driving. The supported **L/C/R image
zones** alternative uses the existing image masks, without metric distance or
physical footprint measurement. It still requires checking the visible zones,
steering sides, swept path and braking on the robot.

**Save on Pi** becomes available after a change. Observation does not lock editing:
save stops observation, writes the policy and resumes analysis only. Active driving
must be stopped before editing or saving. Any calibration-scoped change saves an
unaccepted **observe** policy, so editing a previously accepted drive policy never
submits the contradictory combination `mode=drive, verified=false`. The panel
reports the confirmed revision, a rejected save, or a saved policy whose analysis
restart failed separately. Saving never starts or arms motion.

After saving, use **Enable preview and analysis without driving**, inspect fresh
floor and obstacle evidence, and complete actual steering/braking checks. In
Automat2 the explicit **I confirm the zone, steering and braking** button saves
zone/model acceptance and swept-path acceptance atomically. It remains visible
when zone acceptance already exists but swept-path acceptance is missing. This
replaces the separate advanced swept-path checkbox in this view; the bounded
trial retains its existing controls. No acceptance is written automatically.

Both snapshot age (`pi-reported-plus-host-elapsed`) and the native MJPEG age
(`pi-status-anchor-plus-host-elapsed`) are recognized. Run/revision matching,
decoded visible media, known age and a qualified clear assessment remain required;
unknown host-only timing, stale frames and RAW cannot enable acceptance. The
panel explains which frame condition is missing. Motion deadlines do not change.
**Start journey** is available only when the persisted policy, detector and
executor meet the existing contract.
A green image or saving parameters alone does not grant authority. Neither camera
intrinsics nor physical acceptance are guessed or filled automatically.

## Operator view after a journey

The driving view keeps commands, live status and the camera together; setup
instructions live in the operator guide above. After a journey ends, explicit
manual takeover can continue displaying fresh analysis, even while manually
steering. These host-only images are marked `preview_only` and contain no Pi
journey authority. A running or preparing navigation producer still owns the
analysis; the preview cannot restart it, arm motion or renew its decision lease.

## Start and authority

The operator explicitly starts `POST /api/v1/rider/navigation/journey`. The host
forwards the original intent exactly once to Pi `/api/navigation/journey`, using
the negotiated `rider.navigation_loop` v1 envelope. Required fields extend the
trial request with `journey_version: 1`, `operator_control_version: 1`, and a
nonblank `panel_id`. Generation, settings/config revisions, timestamp, TTL,
operator consent and specific plan remain mandatory. Neither proxy nor retries
refresh an expired intent. Browser refresh, reconnect and view changes never
start or resume motion.

Both Pi safety and the selected Venom detector must advertise
`navigation_journey_version: 1`. Pi checks the persisted drive policy,
calibration/model identity, avoidance enabled with protocol 3, and accepted swept
path. Ground geometry still requires real camera/footprint calibration. The
model is loaded during bounded preparation; an old green preview is not consent.
The existing preparation transaction owns camera, analysis, cancellation and
rollback. Mixed versions cannot silently fall back to a ten-second trial.

Pi owns `arming_mode: journey`, restricted to navigation, panel identity and this
explicit start. There is a bounded startup watchdog, followed by short executor
liveness renewed only by accepted fresh navigation commands with valid operation,
generation, capture/decision and feedback deadlines. GET requests, preview,
browser polling and ARM replay cannot extend authority. Pulse expiration still
zeros outputs. Missing/expired decision, missing feedback, changed source or
policy, executor rejection and terminal avoidance failure end the journey.

## Obstacle episodes

Each active vision frame and response carries:

```json
{"journey":{"version":1,"id":"operator-request-id","maneuver_epoch":0}}
```

Responses also carry a process `host_session`; a changed host session ends the
Pi journey. Legacy frames omit journey metadata and preserve the existing budget.
The run/frame/sequence, configuration, model and camera/transport identities
remain checked in both snapshot and native streaming paths.

Pi advances the epoch by exactly one only after a correction is followed by three
fresh clear frames after the motion/settling capture boundary and an accepted
straightening GO. Venom requires its preceding qualified GO before accepting that
advance. Both sides then reset the correction budget for the next obstacle. A
single clear frame, camera tilt/height change or epoch replay cannot reset it.
Unresolved obstacles keep the existing bounded correction/no-visual-progress
STOP. No physical displacement is inferred from command acknowledgement.

## Operator state and stopping

Navigation status retains `journey` with `version`, `id`, `active`, `state`,
`reason`, `elapsed_s` and `maneuver_epoch`, including the last ended journey.
States distinguish qualification, forward, steering, waiting and termination.
The panel shows current image assessment separately from Pi actuation state.

Stop journey sends the existing global control STOP and retains observation;
Stop loop and camera keeps its resource shutdown meaning. The fixed header STOP
remains available. Manual takeover ends the journey, reads fresh Pi state, then
requests explicit manual ARM under the existing takeover button. Journey never
uses warm navigation pause/resume and never silently resumes after manual control.

## Qualification and limits

Offline tests cover the actual API/proxy, executor admission/watchdogs, vision
planner and Pi decision lease; long sequences must include more than one obstacle,
expired/replayed frames, host/source changes and terminal STOP. Browser tests
intercept every Rider API request and never reach motors. Physical acceptance is
separate: low-speed start, two successive obstacles, no free side, manual takeover,
STOP, stream loss and camera posture change under operator supervision.

Vision estimates relative free space. It does not measure metric stopping distance
or prove physical progress. Existing camera calibration and operator acceptance
remain required. Low battery, cliff detection, metric odometry/SLAM, autonomous
recovery after a terminal fault and new vision models are outside v1. Keep the
existing short command watchdogs and the near swept-path guard.

## Preview and transport recovery

A confirmed camera OFF suspends snapshot reads and native media reconnects;
pipeline status polling continues so a later camera ON reconnects automatically.
The last image remains explicitly paused. An expired new native candidate cannot
renew, or prematurely invalidate, another displayed source: its original 1 s
preview deadline still applies. Invalid identity and source changes remain stale.
Warm observation displays the last terminal journey from Pi `last_trial`, without
presenting it as an active journey or automatically restarting motion.

Pi reuses one bounded HTTP decision connection per runner. A failed POST is never
replayed; an observation retry uses a new capture. Idle/error/shutdown closes the
connection. Response bounds, same-origin token handling, exact-source echo and
capture/feedback deadlines are unchanged. `decision_transport.connections` and
`.requests` in Pi navigation status expose reuse without recording credentials.

A diagnostic native run is scoped to camera session AND Pi policy revision, so
saving settings without restarting capture cannot permanently reject the preview
as a replay. A live Pi producer still owns its exact-source preview. When the
journey is explicitly terminal and its producer stopped, the retained S14 request
may use diagnostic preview only after Pi confirms DISARMED, cannot move, no
movement and nonstale control. Preparing/active/unknown/ESTOP states do not use
this exception. No goal, ARM, navigation resume or motor request is issued.
`preview_only:true` labels this independent assessment; its fresh image can show
GO for the current policy/model while the Pi motion reply remains absent/STOP.
Operator calibration acceptance still requires the live Pi run's matching frame.

A slow status roundtrip does not replace a tighter recent source-age upper bound
for the same camera authority. Reuse is bounded to 2 s; source timestamps and all
stored/header age floors still apply. Camera session/epoch/profile replacement
always discards the preceding anchor. The source, cache, display and motor
freshness limits are unchanged.
