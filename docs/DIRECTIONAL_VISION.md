# Directional vision and bounded steering

## Operator flow and compatibility

One drive action prepares the camera, analysis and Pi authority for the existing
10-second trial. Manual takeover and the separate STOP controls remain available.
Avoidance is disabled by default. Protocol v1 retains forward-only behavior;
v2 retains STOP followed by yaw-only turns. Explicit v3 uses steer_left/right:
the existing low forward command and yaw are submitted together.

The image mode keeps the draggable STOP rectangle, near preset and residual/
outlier settings beside the preview. Save checks the Pi revision, stops producers
and resumes observation only if it was previously active. Save never arms.
Old policies without geometry migrate to image mode; they do not silently gain
a new projection. The Pi-owned policy remains the shared source for all panels.

## Adaptive ground mode

V3 also supports avoidance.geometry.mode=ground. The same single DA2 inference
provides the depth map; no second model is loaded. A deterministic robust inverse-Z
plane fit uses broad lower-image support. Every trimmed refit must retain wide,
near and sufficient support. A vertical front face cannot replace the floor
merely because it is large. Missing support produces UNKNOWN.

Projection uses the plane normal and model-scale height estimated from this
capture, replacing the legacy fixed image-slope test. This is vision-derived
geometry, not synchronized IMU or measured height. Small changes are evaluated in
the current ground coordinates. A normal change over 3 degrees or model height
change over 15% clears stability history; it never restores the turn budget.
Progress comparisons require a stricter 1 degree / 5% match.

The planner checks 49 headings over the configured range. Each projects a capsule
inflated by a conservative circumscribed body radius. Continuous groups of clear
headings form gaps; left/right summaries report an available passage, not that
the entire half-image is free. Every selected path needs full image coverage and
a clear near guard. Deviating depth points are checked against the capsule as
well as sampled rays so a thin observed obstacle cannot fall between samples.
Recesses are blocked at the missing ground intersection.

The preview labels source-space regions L / C / R. Cyan marks L, white marks
the central STOP region or straight capsule, and violet marks R. Image mode
uses the actual adjacent assessment rectangles, not equal image thirds.
Ground mode labels the actual projected paths; a missing projection is a
question-mark badge without an invented boundary. Strokes remain interrupted
outside the camera field of view. Incomplete projection remains UNKNOWN.
Ground mode replaces the image ROI editor with camera/footprint settings;
a percentage rectangle is not a physical path.

A question mark also marks quality rejection or incomplete assessment. Outline
colors identify regions, not permission to move. The pixel mask retains its
existing green/red/yellow meaning and the dashed-blue ground-fit reference
remains separate. Quality rejection still skips model loading.

The L/C/R cards immediately below the image describe that decoded frame, not a
newer navigation-state poll. Expired, undecoded, mismatched or unconfirmed frames
cannot publish a clear side. JPEG and native MJPEG share the same renderer and
source correlation. RAW and local PROC do not acquire a Venom assessment.

Preview metadata uses `sector_space=image|projected_paths|unavailable` and a
bounded `sectors` summary in source-image coordinates, before command mirroring.
The Pi decision response retains its original planner-side mapping, including
`avoidance.mirrored`. Robot commands remain in the separate Venom → Pi card.
The original frame/run/sequence/configuration/source identifiers and preview
age limit are unchanged; no additional frame retrieval or inference is added. The orange dashed ROI editor is a separate unsaved draft; it does not relabel an already assessed frame.

## Calibration fields

These fields live in the existing avoidance policy and existing revision/save
transaction; there is no separate configuration service.

| Field | Meaning |
| --- | --- |
| fx, fy | Focal lengths divided by delivered image width and height |
| cx, cy | Principal point in normalized delivered-image coordinates |
| radius_ratio | Conservative body radius / minimum accepted camera height |
| lookahead_ratio | Forward horizon in current inferred ground-height units |
| max_heading_deg | Half-range of searched headings, default 45 degrees |
| max_stalled_corrections | Consecutive comparable corrections without improvement, default 2 |
| progress_min_delta | Minimum normalized clearance gain, default 0.03 |

The body radius must cover every robot point relative to the ground projection
of the camera across accepted heights/poses, with a margin. Using the minimum
physical camera height conservatively inflates this bound at higher poses.
Intrinsics must correspond to actual resize/crop/rotation of the delivered
image. Lens distortion must be characterized or corrected before acceptance;
this path assumes a pinhole image. No SDK height command substitutes for a
measurement. fx, fy and radius_ratio start empty: they are not guessed.

The preview explains missing calibration. The host advertises
ground_geometry_supported. The Pi rejects nonzero ground commands without its
configured projection and valid normalized ground-reference/support evidence.

## Maneuver and progress

A v3 correction requires three distinct clear path observations and an accepted
near guard. The chosen side stays locked. Frames captured before/during a
correction cannot authorize subsequent nonzero output. Three new front CLEAR
observations straighten the motion in its new heading.

Comparable fresh post-correction observations measure the change in normalized
front clearance. Insufficient gain increments the stalled counter; sufficient
gain clears it. Uncertain or changed perspective cannot count as improvement.
The independent max_turn_pulses limit still applies. Stagnation or an exhausted
budget without a clear path produces terminal avoidance_no_progress. Later GO
cannot revive it. UNKNOWN submits ZERO only within the original valid session;
loss of feedback or operator/global STOP ends the run.

No speed, pulse, feedback budget or original ARM window is increased:
forward <=0.1 SDK and <=150 ms; yaw 0.01–0.1 SDK and 50–150 ms.
A heading candidate chooses the correction side; it is not a metric yaw command
or a certified dynamic trajectory. Physical combined-vx/yaw sweep and braking
must be validated for the configured limits.

## Validation boundary and next data PR

Software tests cover changed height/pitch/roll, boxes, thin obstacles, recesses,
missing calibration, incomplete coverage, gap width, stagnation, one inference,
rendering and real Venom-loop to real Pi-lease admission with recorded callbacks.
Browser tests intercept every Rider request and cover editing, persistence,
start/cancel/STOP and terminal outcomes. These are not physical acceptance.

Historical weak box recordings remain negative examples; thresholds were not
relaxed to force a correction. They lack calibrated intrinsics/body geometry,
so the new ground mode cannot establish a physical turn from them. Dominant
raised planes, monocular depth errors and unseen lateral/rear space remain
limitations. This is experimental and stays disabled until calibrated.

The next PR collects timestamped independent sequences: free floor and left/
right/central/both-side obstacles, thin objects, recesses, light changes, accepted
heights, pitch/roll transitions, and frames after real bounded corrections.
Record camera intrinsics/image transforms, conservative body measurements,
capture identity, configuration/model digest and executor outcome. Split tuning
from held-out acceptance. Report false GO/STOP, side errors, flicker, latency,
progress and observed trajectory/braking. A motor ACK is not observed movement.

The host serves a generated frontend copy. After module UI edits, run the
existing host web-next/scripts/prepare-optional-module-node-paths.mjs before
browser checks. RIDER_TEST_PI_LEASE points tests at the paired Pi source copy;
tests never contact motor hardware.

### Stable preview updates

The image and capture metadata stay paired until the next JPEG has loaded in the
visible image element. Each assessment is read only from its decoded source frame.
The L/C/R text cards sample the latest assessment once per second for readability;
the image and authoritative motion decision keep their normal cadence. The card
sample retains its capture identity and is reset immediately for an invalid live
source or a changed model, camera or policy. A short hint states the one-second
text cadence. This applies to both snapshot polling and native MJPEG.

The displayed frame keeps its original freshness deadline. A failed read or an
expired frame still shows the existing stale-preview notice and uncertain
assessments; updates never extend an older frame's validity.
