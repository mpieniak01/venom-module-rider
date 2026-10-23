# Perception providers and observation models

Perception selection has two levels: the analysis provider and one of that
provider's models. Both operator panels read the same Pi configuration and use
revision checks. A browser draft is never the active configuration.

- The Pi camera pipeline owns `analysis_location` (`local` or `venom`) and
  `local_engine`. Local Canny, MobileNet SSD and EfficientDet Lite0 availability
  comes from actual Pi runtime capabilities and files. Canny is an algorithm.
- Venom owns its model inventory, artifacts, installation jobs and inference.
  The Pi stores only the chosen host model ID and artifact fingerprint inside
  its revisioned navigation policy. It does not install host models.
- Local image analysis and the host navigation loop have different capabilities.
  Selecting a local detector does not certify host navigation or enable motion.

## Operator workflow

Open the perception settings beside the camera. Choose a provider, then a model,
and explicitly apply the choice. The panel distinguishes the selected policy,
the actual loaded model and current image metadata. The camera transport/power
controls remain independent. Changing a host model does not restart the camera.

Host installation/verification is a separate explicit action. It can run while
the robot is offline; a host catalog remains visible with unknown Pi selection.
Changing the active configuration requires a reachable Pi, fresh idle executor
state and stopped navigation/observation goals. The selector never silently
stops a running trial or starts a camera.

After a local-to-Venom transition, applying the host model is a second checked
operation. If it fails, the panels display the actual committed provider and
model with the error; they do not pretend the whole change succeeded or restore
old movement permission. Another panel's save invalidates an outdated draft.

## Host models

| Model ID | Serving | Initial capability |
| --- | --- | --- |
| `depth-anything-v2-indoor-small` | Existing DA2 torch adapter | Relative ground geometry; existing acceptance still required for drive |
| `segformer-b0-ade20k` | Existing verified ONNX, or pinned local safetensors | Observation only for explicit model selection |
| `yolo26n-sem-ade20k` | Pinned raw-logit ONNX export, CPU | Observation only |
| `fast-scnn-floor` | Qualification blocked | No installation/selection until artifact and preprocessing are complete |

The pinned Fast-SCNN ONNX refers to `fast_scnn_floor.onnx.data`, which is absent
from the published revision. Its card also lacks normalization and binary label
mapping details. This is a visible qualification failure, not a ready fourth
model. No arbitrary URLs, paths, Python code or pip arguments are accepted from
the operator.

Installers run as bounded CPU children under the existing shared host task
admission. Downloads use immutable revisions and hashes. YOLO export uses
Ultralytics 8.4.184, ONNX 1.21.0 and official release v8.4.0 asset 444468363,
SHA256 `68ac71bef2868c987fff7cd7c49cb656922f6d8447ad21af3893305577435000`.
Numerical export parity and a synthetic runtime smoke precede the ready receipt.
These checks do not prove obstacle accuracy. Optional export dependencies are
declared in `vision_yolo_export`; they are not installed from an HTTP handler.

New host assets/receipts default to
`~/.local/share/venom/rider/models`, configurable through `RIDER_MODEL_ROOT`.
Existing configured SegFormer and DA2 locations remain read-only inputs.
Partial staging is not ready; retry after cancellation uses an exclusive
installation lease. Existing baseline files are not overwritten.

## Result identity and diagnostics

Venom preview uses the existing camera pipeline and shared inference. It carries
model ID, artifact fingerprint, model generation, configuration revision and
frame/source identity. Replacing the model clears previous masks and clearance
history. A late result cannot become the new model's output. Local processed
frames carry the actual producer engine/model and pipeline identity; stale
producer output has no JPEG fallback.

Green/red/amber denote floor/non-floor/uncertain segmentation, not measured
clearance or distance. DA2 shows its established relative geometry. Preview
freshness and display-only GO never authorize a motor command.

The panel shows runtime backend, inference duration, effective processing rate,
process RAM and available Torch process VRAM. Missing measurements are N/A.
Cold loading happens outside a captured frame's validity budget. Recorded
offline semantic timing is explicitly `perception_total` (decode through overlay),
rather than claiming it is pure model compute.

## Interfaces

Operator host routes under `/api/v1/rider`:
- GET `/perception/models`
- POST `/perception/models/{id}/install` and `/install/cancel`
- POST `/perception/selection` with `{revision, model_id}`

Installation uses the current host panel session and existing mutation policy.
Authenticated selected-Pi aliases are GET `/compute/vision/models` and POST
`/compute/vision/model`. They invoke the same service, not a copied registry.
Pi endpoints and compatibility are documented in Rider-Pi's
`docs/apps/perception-models.md`.

## Comparison and rollback

The existing offline analysis panel accepts the qualified semantic profiles on
the same archived frames and reviewed labels as depth profiles. Semantic runs
use one frame and image geometry. Results keep the same input digest, reports
and comparison path. Missing labels do not turn into accuracy measurements.
CPU offline work yields to live perception and never changes the live selection.

To restore a previous host model, stop the loop explicitly, select the qualified
baseline and apply it. The Pi writes a fresh observation policy, clears model/
calibration/sweep approval and never restores ARM or an earlier run. Inspect a
fresh result before any separate operator acceptance.

Downgrading Pi code to a version predating optional model identity is a separate
configuration migration: old validators reject the new fields. A Git rollback
alone is insufficient after explicit selection. Preserve configuration and use
the Pi migration procedure under idle/disarmed conditions; never restore an old
configuration containing movement approval as a shortcut.

## Qualification sources

- https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512
- https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf
- https://huggingface.co/Tanishjain9/fast-scnn-floor-segmentation
- https://docs.ultralytics.com/tasks/semantic/
- https://github.com/ultralytics/assets/releases/tag/v8.4.0

## Operator caution and model tuning

The **Caution level** selector is visible beside the perception selector, above
the camera, in Manual, Automatic and Journey views. Choices are Legacy,
Cautious, Balanced and Fluid. Only **Apply** persists a choice; a draft does not
change the active profile. Both panels poll the Pi's revisioned configuration.
A local Canny/SSD/TFLite provider retains its own executor; new host temporal
profiles are unavailable there, with an explicit explanation and Legacy rollback.

Open **Selected model settings** below the caution selector. Depth Anything V2
exposes its existing residual/outlier/slope thresholds. SegFormer B0 and YOLO
semantic expose floor-only or floor-and-rug plus optional overrides for pixel
confidence, floor fraction, uncertain fraction and region fraction. Inheritance
uses the selected caution profile; an explicit value belongs to that model only.
ADE20K floor=3 and rug=28 are separate classes. Rug is traversable only when
floor-and-rug is explicitly selected. Road/sidewalk are never added implicitly.
Legacy SegFormer without an explicit model ID uses the same editor.

The Pi persists `perception.semantic_models`, keyed by canonical host model ID.
Each record has `version=1`, `surface=floor_only|floor_rug` and nullable
`pixel_confidence`, `min_floor_fraction`, `max_uncertain_fraction`,
`min_region_fraction`. Null means profile inheritance. An empty dictionary may
be omitted for compatibility. Switching models preserves the other records.
`semantic_model_policy_v1` is negotiated separately from temporal profiles;
old hosts refuse configured policies instead of ignoring them.

Changes require stopped navigation goals and fresh zero-motion executor state,
use CAS and revoke previous navigation/calibration/sweep approval. They persist
observation mode and never start a camera, model run or robot. Explicit semantic
model selection remains observation-only. The surface mapping and resolved
thresholds are shared by classification, preview, compact maps and offline
analysis. YOLO resizes class logits in bounded channel batches; numerical parity
is checked without changing its exported model.

The archive panel starts in offline Legacy and needs no Pi. **Load saved Rider
settings** explicitly reads the current Pi config, freezing caution, per-model
settings, ROI and depth thresholds for that experiment. It does not change the
selected offline checkpoint or copy live model identity/movement approval.
The panel classifies individual frames/maps; complete temporal evaluation uses
`scripts/replay_navigation_policy.py --profile ... --perception settings.json`.
Explicit replay settings require matching canonical model identity (and artifact
hash if supplied). Semantic tuning requires labels plus confidence; old boolean
masks cannot apply confidence or class changes. Depth requires depth masks.
Returning to Legacy does not restore earlier driving approval. Downgrading code
that rejects optional fields needs an idle, observation-only config migration.
