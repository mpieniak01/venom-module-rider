# Temporal navigation and operator profiles v1

The host can assess source-resolution regions across fresh frames before
producing GO, bounded SLOW or STOP. This is an opt-in experimental policy for
Automat2, using the installed perception model and existing camera transport.
It does not install another runtime, change the device camera, or run two models.
Legacy remains the default for existing and missing behavior configuration.

The optional `vision_temporal` extra declares the existing qualified OpenCV
version plus NumPy/Pillow; combine it with the installed model extra. No API
handler installs dependencies. A host without the capability retains Legacy.

## Selection and compatibility

Both operator panels read the Pi-owned navigation configuration. The driving
profile drawer beside model selection offers Legacy, Cautious, Balanced and
Fluid. Parameters are read-only. A draft is separate from the saved selection;
a stale configuration revision returns 409 without an automatic retry.

Saving requires stopped navigation/observation goals and a fresh stopped
executor. The check and atomic save share the executor settings mutex. Manual
non-STOP commands cannot race this transaction; STOP bypasses that mutex.
A successful profile change writes observation mode and clears driving,
calibration, model and swept-clearance approval. It starts neither the camera,
analysis nor motion and never restores ARM. Camera power and manual controls
remain independent. Operator correction still leaves automation paused, with
explicit resume requiring three fresh positive observations.

The selected identity is:

```json
{"behavior":{"version":1,"profile_id":"balanced","profile_revision":1}}
```

Pi GET `/api/navigation/config` adds a presentation-only `behavior_catalog`,
including active identity, released parameter definitions, experimental status
and `effective_policy_sha256`. The digest covers canonical behavior identity
and effective profile parameters; configuration revision, model/source identity
and motion authority remain independent mandatory checks. The catalog is not
persisted as configuration.

Venom GET `/api/v1/rider/navigation/config` and `/navigation/state` expose the
same selection. Host presentation disables profile selection when either
catalog or host capability is unavailable. POST `/navigation/config` forwards
the existing `{revision, values:{behavior:...}}` CAS transaction with the current
operator session. Direct Pi writes still receive the same device-side checks.

Decision protocols 1/2/3 retain their numbering and existing meanings. New
behavior is negotiated separately as `temporal_navigation_v1: true` in the
selected host's authenticated vision status. The Pi preflight checks it before
acquiring motion authority. Old peers/configurations retain Legacy; an unknown
SLOW is rejected rather than treated as GO.

## Evidence and movement

Semantic adapters retain compact class labels and maximum probabilities at the
source size; the profile applies its pixel confidence threshold. Depth retains
relative residual/support masks and existing reference-validity checks. Both
feed the same spatial and temporal evaluator used by offline replay.

Connected regions use area and span, including thin obstacles crossing the
corridor. A near coherent obstacle stops immediately. A lone neural depth
residual is not independent proof of a cliff. Calibrated ground mode preserves
its complete existing 3D body-volume collision and near guards, including
objects above the projected floor footprint; a 2D floor mask cannot erase them.
No uncalibrated image boundary is presented as a metric body projection.

History uses source/session/model/policy context, distinct canonical capture
identities, elapsed milliseconds and bounded frame storage. Duplicate captures,
source changes, long gaps and invalid evidence reset qualification. Sparse LK
uses forward/backward verification and RANSAC to estimate shared image motion;
linked region growth is diagnostic, not metric time-to-collision. Texture alone
is not an obstacle. When a featureless image has no established flow, fresh
model observations can still qualify temporally; losing established reliable
tracking resets evidence and produces STOP. No old mask or reprojection renews
a model timestamp or motor permission.

SLOW requires previous fresh clearance, a currently supported near corridor,
valid matching profile/digest and the profile's exact reduced speed factor.
The Pi independently checks identity, revision, run/frame/sequence, original
capture age, model, authority and remaining deadline. It uses the original
bounded motion callback/permit. No TTL, ARM window or pulse cap is increased.
A near or unreliable temporal STOP vetoes steering as well as forward movement.

The panels distinguish raw prediction, stabilized assessment, requested action,
Pi lease and physical execution. A valid SLOW lease is not an SDK acknowledgement
or proof that motors are moving. Historical data cannot become permission.

On a current processed host image, white outlines the evaluated corridor,
amber/red the near guard and cyan the floor boundary. These lines scale from
actual source coordinates to the preview. Invalid evidence draws no new
geometry. Segmentation colors remain raw model diagnostics.

## Reproducible offline work

The existing analysis request accepts `spec.capture_evidence: true` (default
false). It adds compressed compact maps, source gray image, validity and
calibrated geometry veto metadata as ordinary bounded analysis artifacts.
Rejected image quality is kept as invalid/all-uncertain evidence, not omitted.
The existing archive quota and worker IPC limits still apply. No full
150-channel tensor is stored and no new live recording loop is created.

Export a frozen report and its artifacts with a separately verified source
timeline. The timeline must join image SHA and source sequence to the actual
frame identity and provide finite source time. Import/arrival time cannot be
substituted for source time. Export uses a new directory and a bounded budget.

```sh
PYTHONPATH=../..:. ../../.venv/bin/python scripts/export_navigation_evidence.py \
  --report PRIVATE_REPORT.json --artifacts PRIVATE_ARTIFACT_DIR \
  --timeline PRIVATE_SOURCE_TIMELINE.json --output PRIVATE_NEW_EXPORT --max-mib 128
PYTHONPATH=../..:. ../../.venv/bin/python scripts/replay_navigation_policy.py \
  --manifest PRIVATE_NEW_EXPORT/manifest.jsonl --profile balanced \
  --output PRIVATE_NEW_EXPORT/balanced.jsonl
```

Keep reports, inputs, models and exported maps outside Git. Replay writes frame
and map hashes, source dimensions, policy identity and reasons. Its timings
exclude model inference, decoding and disk access. They are not a live
capture-to-motor latency claim, a calibrated collision distance or accuracy.

## Checks and acceptance

`make check` runs Ruff, typing, Python coverage (90% existing gate), web typing,
lint and unit tests. `tests/browser/navigation-profiles.js` runs against an
isolated fixture and never a physical robot. Optional
`RIDER_TEST_PI_ROOT` points to an explicitly prepared pure-source fixture with
`SOURCES.json`; paired tests load the matching real Pi lease/profile code and
use list-only output callbacks. They never discover or connect to a device.

All three new profiles are experimental candidates. Replay alone has not
qualified physical smoothness, stopping performance or false-stop accuracy.
Independent labelled sessions, body/pose calibration and operator acceptance
are required before approving driving. Semantic model observation-only rules
remain unchanged. Do not increase TTL to hide a slow model.

## Rollback

Stop the loop deliberately, keep the executor stopped and choose Legacy. The
normal CAS save clears motion approval; inspect a fresh observation before any
separate approval. Existing configurations without `behavior` load as Legacy.
Before downgrading Pi code to a validator without `behavior`, preserve the
current configuration and remove only that new field under idle/disarmed
conditions. Reset `mode=observe`, `verified=false`, empty calibration/model
approval and `avoidance.sweep_verified=false`; never restore old movement
permission from a backup. Model identity migration remains separate.


Operator placement, per-model overrides and archive import are documented in [perception models](PERCEPTION_MODELS_V1.md#operator-caution-and-model-tuning).
