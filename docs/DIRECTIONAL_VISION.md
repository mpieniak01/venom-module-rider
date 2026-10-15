# Editable zones and bounded directional trials

The control screen supports dragging and resizing the STOP rectangle on the
current camera image, restoring the near-zone preset, and editing relative depth
residual and outlier sensitivity beside the preview. Changes remain drafts until
Save. Saving stops producers, checks the Pi revision, writes its existing policy,
and resumes observation only if observation was previously active. Save never
arms the robot. Editing is unavailable during a driving trial. Numeric coordinates
remain available under the existing advanced settings.

The close STOP region and wider ground-fit reference are separate. The editor
uses normalized image coordinates with letterboxing accounted for. The detector
rejects masks below 64 pixels. Relative depth deviations and pixel fractions are
not obstacle height, distance or a measured stopping envelope.

## Directional assessment

One DA2 depth map and one shared ground plane produce left, center and right
sector assessments. Each reports coverage, state, near/far outlier counts,
fraction, centroid and bounds in normalized image coordinates. Incomplete or
incorrectly oriented sectors report UNKNOWN. No second model inference is added.
Three consecutive valid samples report growing/stable/receding image occupancy;
this is diagnostic, not measured approach velocity or time to collision. Gaps
over 500 ms, unknown data and turn settling reset trend continuity.

Without avoidance, side labels follow the image. With protocol v2 avoidance,
left/right decisions follow the robot; the saved mirrored-camera setting swaps
image sides. Centroid and bounds explicitly remain in image coordinates.
The operator must verify actual camera orientation and yaw direction.

## Optional protocol v2

Avoidance is disabled by default. Old saved Pi policies migrate to disabled
avoidance; the forward-only protocol v1 remains available. An enabled v2 request
requires depth_plane. Venom advertises supported_protocol_versions [1, 2]; the
panel requires v2 support before enabling avoidance. Pi independently checks the
version and every run/frame/sequence/revision/model and freshness permit.

Pi owns these avoidance policy values:
- enabled, sweep_verified, mirrored: booleans;
- turn_speed: 0.01–0.1 SDK, default 0.05;
- turn_ms: 50–150 ms, default 100;
- max_turn_pulses: 1–5 per original trial, default 3.

The v2 frame includes avoidance and motion_phase (ready or turn_settling).
The response retains the v1 identity and TTL fields, adds directional sectors and
maneuver_clear_count, and permits go, stop, turn_left or turn_right. Unknown
versions/actions or malformed sector evidence are rejected by Pi.

The sequence is STOP, three distinct clear side observations, bounded yaw-only
turn, new captures after the turn permit plus 100 ms settling, then three front
CLEAR observations before forward motion. A locked side cannot oscillate to the
other side mid-maneuver. Recesses, unavailable coverage, exhausted turn budget
or missing consent produce STOP. The Pi also requires an actual preceding zero
submission and verifies selected-sector coverage before a turn callback.
All waiting and turns consume the existing total 10-second trial; no new ARM,
watchdog extension or increase to the 0.1 SDK / 150 ms forward limit is added.
Resuming means travel in the newly assessed heading, not a return to the old path.

## Acceptance status and tests

This is an experimental, paired module/Pi implementation. Existing recorded
floor/box/dark-scene regressions remain applicable. They do not validate side
clearance, swept-body clearance, yaw sign, thin lateral obstacles, corners or
post-turn camera conditions. A frontal camera alone cannot establish side/rear
clearance. Hardware acceptance on both directions remains outstanding; do not
treat software test success or a motor ACK as observed physical success.

Automated tests run the real Venom decision loop against a supplied copy of the
real Pi DecisionLease, recording callbacks instead of contacting motors. Use
RIDER_TEST_PI_LEASE for that private source locator. The existing full browser
regression also covers pointer editing, draft-only changes and save/resume,
besides trial admission, cancellation, visual pause, terminal STOP and takeover.
Every Rider HTTP request is intercepted in that browser test.

The host serves a generated copy under web-next/optional-modules. After editing
module frontend sources, run the existing host command
node web-next/scripts/prepare-optional-module-node-paths.mjs before browser checks.
A successful module type check alone does not refresh this generated copy.
