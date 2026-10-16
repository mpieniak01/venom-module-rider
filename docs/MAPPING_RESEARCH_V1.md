# Offline geometry research v1

The retained Rider photographs support a useful overlap/pose experiment.
They do **not** qualify a metric map, localization or panorama of the home.
Collection remains in Venom; this experiment reads a v1 session export and
does not contact Pi, start a camera, scan, arm or drive.

## Reproducible offline tool

Use the existing project runtime with OpenCV and, for the optional pose probe,
the retained DA3 runtime. No installation or new model is performed.

    PYTHONPATH=../..:. ../../.venv/bin/python -m scripts.mapping_research \
      --archive /absolute/session.zip --output /absolute/derived/research/run.json

Add --pose to request the retained da3-small-280 profile. Set RIDER_VISION_ASSETS
to the existing data asset root and use that profile's Python runtime.
The resolver and model/SDK hash checks are shared with offline depth analysis.
DA2 remains the live default; no new live model option is introduced.

The command reuses the sample-export validator, including dataset revision,
paths and exact RAW hashes. Limits: 32 frames, 16 MiB of input RAW, 2 MiB
manifest/report, two CPU threads, lower process priority and 120 s CPU budget.
Images are bounded and thumbnails preserve aspect ratio. Python network
connections are denied; offline Hub flags are set. Ctrl-C cancels this standalone
experiment; an interrupted run cannot publish a completed report.
Use a new output filename per run; publication is atomic and never overwrites.
Store reports in the existing registry's derived area so shared usage accounting
includes them. No sources are copied or annotated.

The typed research artifact records source session/revision, every selected
sample/RAW hash, code fingerprint, settings, time, RSS, overlap results and
optional model provenance/pose. Panorama and map outcomes remain separate.
It is a qualification tool, not a production task/UI or another data registry.

## Current measured result

Same retained v1 exports: floor (6 images), box (4), near-black image (1).
All are historical, filename-unverified, with no qualified camera intrinsics,
camera-body transform, synchronized pose/IMU or independently measured scale.
Operator-reviewed return/trajectory truth is absent.

CPU measurements on this environment; one run each, not p50/p95 or live latency:

| Input | ORB audit total | Peak RSS | Overlap candidates | DA3 joint forward | DA3 load | DA3 peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Floor | 67.63 ms | 61.96 MiB | 5/5 pairs | 1013.26 ms / 5 views | 1015.83 ms | 1137.96 MiB |
| Box | 57.67 ms | 61.42 MiB | 3/3 pairs | 618.64 ms / 4 views | 945.31 ms | 1104.57 MiB |
| Near-black | 46.79 ms | 55.92 MiB | 0 pairs; 0 features | N/A: one view | Not loaded | N/A |

ORB found 371–512 features per floor image and 175–276 per box image.
RANSAC overlap shows shared visual content; it does not prove travelled distance,
parallax suitability, pure camera rotation or correct temporal order.
DA3 produced finite camera extrinsics/intrinsics using existing Small weights.
These are a joint-view hypothesis in model units, not measured odometry, a
persistent tracker or ground-truth calibration. ATE, relative pose/heading error,
loop-closure precision and metric scale remain **N/A**.
The existing depth benchmark discards camera outputs; it had not previously
evaluated localization. Depth quality alone cannot establish mapping accuracy.

## Candidate decision and primary references

| Candidate | Relevant capability / constraint | Decision |
| --- | --- | --- |
| Retained DA3 Small | Main-series Small has relative depth, pose estimation and pose conditioning; Apache 2.0. Its model table does not claim metric depth or Gaussian output for Small. | First joint-view candidate on new qualified data; no new weights. |
| ORB-SLAM3 | Monocular/stereo/RGB-D and visual-inertial SLAM; camera calibration and native dependencies; GPLv3. | Reference tracker once ordered, calibrated sequences exist; not installed in this study. |
| DA3-Streaming | Chunked reconstruction; upstream explicitly says it is not a SLAM system. Published 8.51 FPS used A100 and excludes warm-up/model loading/saving; listed VRAM varies with sequence/window. | Preserve research direction; not a local performance promise or Pi backend. |
| MASt3R-SLAM | Dense SLAM, CUDA/checkpoints; project code CC BY-NC-SA 4.0 and checkpoint terms are separate. Upstream notes a WSL multiprocessing workaround. | Defer dependency/model expansion and suitability review. |
| RTAB-Map | Appearance-based loop closure/graph mapping is relevant to recognizing revisited places. | Secondary reference; not qualified by this dataset. |
| OpenCV stitching | Feature matching and panorama construction assume an appropriate image/motion model. | ORB overlap baseline only; do not publish a panorama from unknown motion/calibration. |

Sources checked 2026-10-16:
[DA3 model table](https://github.com/ByteDance-Seed/Depth-Anything-3#model-zoo),
[ORB-SLAM3](https://github.com/UZ-SLAMLab/ORB_SLAM3),
[DA3-Streaming](https://github.com/ByteDance-Seed/Depth-Anything-3/tree/main/da3_streaming),
[MASt3R-SLAM](https://github.com/rmurai0610/MASt3R-SLAM),
[MASt3R-SLAM license](https://github.com/rmurai0610/MASt3R-SLAM/blob/main/LICENSE.md),
[RTAB-Map](https://introlab.github.io/rtabmap/),
[OpenCV stitching](https://docs.opencv.org/4.x/d1/d46/group__stitching.html).

## Data needed before a mapping implementation

Collect whole sessions with overlapping views in original order and source
times. Preserve raw timestamps/clock domain, run/frame/sequence IDs and actual
drop/gap information. Do not reconstruct order from filenames or file-write time.

Record intrinsics/distortion for the delivered resolution/crop/rotation,
camera-body/IMU relationship and changes in camera height, pitch and roll.
A moving/tilting robot has a six-degree-of-freedom camera. A single fixed floor
horizon is unsuitable. Pure rotation may aid a panorama but cannot establish
triangulated translation/scale.

The operator's next dataset should separately cover stationary views, overlapping
rotation, slow measured translation, a return to a marked place, height/tilt
changes and low light. Keep tuning and acceptance as whole separate sessions.
A physical traversal is a later operator action, not initiated by this tool.

For evaluation, retain the algorithm trajectory, tracking loss and relocalization
events. Use one SE(3) alignment for a metric estimator or one declared Sim(3)
alignment for unscaled monocular geometry; report the scale and do not realign
every segment to conceal drift. Compare ATE, relative pose/direction error and
labelled loop closures independently from how attractive a reconstruction looks.
Do not treat accepted SDK output, depth confidence or inferred camera scale as
independent motion/distance truth.

Panorama acceptance needs actual angular coverage and inspection of seams,
ghosting and parallax. Map/localization acceptance needs calibrated geometry and
independent scale/trajectory or clearly unscaled evaluation. This research
concludes **collect qualified data first**, with DA3 Small and ORB-SLAM3 as the
next comparison. No production map, autonomous exploration or HOME is claimed.
