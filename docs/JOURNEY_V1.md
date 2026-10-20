# Automat2: continuous supervised journey v1

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
floor and obstacle evidence, and complete actual steering/braking checks. Confirm
the swept path only after those checks; then save it and accept the current zone
and braking against the fresh visible analysis. **Start journey** is available
only when the persisted policy, detector and executor meet the existing contract.
A green image or saving parameters alone does not grant authority. Neither camera
intrinsics nor physical acceptance are guessed or filled automatically.

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
