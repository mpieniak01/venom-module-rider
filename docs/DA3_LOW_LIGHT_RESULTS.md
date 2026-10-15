# Current-camera low-light benchmark — 2026-10-15

## Capture
20 newly captured Rider-Pi RAW JPEGs, 320x240, acquired at **17:16:59–17:17:09
Europe/Warsaw (UTC+02:00)**. Twenty distinct SHA-256 hashes and frame IDs;
all source headers reported stale=false and placeholder=false. These headers
identify completed file reads, not sensor exposure timestamps.
Manifest SHA-256: `e3c460692dead85733ace1e35d1c5245f2b8b9f638c0c50ffd8901ffcccc3f2b`.

Mean normalized RGB brightness ranges from 0.01059 to 0.01276 (about 1.1–1.3%).
This is image brightness, not a measured lux value. The floor is largely invisible.
The condition is labelled low light; all scene labels remain **unlabelled** because
neither free space nor obstacles were independently confirmed for this capture.
Images and exact per-frame reports stay private in local fixtures.

Pi was DISARMED, can_move=false and output zero before/after capture. Only the
existing camera-preview service was enabled; camera and preview were stopped
after acquisition. No ARM, navigation trial or motor command was sent.

## Two separate tests
- **production**: the unchanged production image-quality gate runs before inference.
  All 20 images were UNKNOWN with vision_image_quality for every configuration.
  No model forward was executed. Gate latency must not be advertised as model speed.
- **model_probe**: explicitly bypass only the offline quality rejection to measure
  the depth models and unchanged plane heuristic on exactly these dark bytes.
  This diagnostic is never eligible to grant motion and changes no live gate.

Five configurations on CPU and GPU, both modes, give 20 completed runs:
GPU five repeats per JPEG, CPU three; **1600 replay samples**, including 800
actual-inference samples and 800 quality-gate-only samples. This is one short
scene sequence, not 1600 independent scenes. No denoising, brightening or per-model
threshold tuning. Same hardware/precision/resolution qualifications as
[the initial study](DA3_RESULTS.md).

## Diagnostic model-probe results
P95 includes file/decode, preprocessing, forward, resize and plane assessment.
Output C/O/U counts **unique captured frames** using the first repeat of each
(20 total). Changes count consecutive captured-frame state transitions; they are
not flips between repeats of the exact same JPEG.

| Backend | GPU P95 ms | CPU P95 ms | GPU C/O/U out of 20 | CPU C/O/U out of 20 | GPU sequence changes |
|---|---:|---:|---:|---:|---:|
| DA2 | 30.2 | 268.3 | 20/0/0 | 20/0/0 | 0 |
| DA3 Small 280 | 43.8 | 95.4 | 0/0/20 | 0/0/20 | 0 |
| DA3 Small 504 | 49.6 | 331.0 | 0/0/20 | 2/0/18 | 0 |
| DA3 Base 504 | 71.2 | 998.7 | 3/12/5 | 6/8/6 | 14 |
| DA3 Metric Large 504 | 97.2 | 2511.3 | 0/0/20 | 0/0/20 | 0 |

No configuration flipped state across repeats of an identical JPEG. Some did
change state between successive distinct captures of this static dark scene.
CPU fp32 and GPU bf16 can also differ; these remain separate result sets.

## Interpretation
DA2's unguarded plane assessment returned CLEAR for all 20 dark captures.
That is **unverifiable CLEAR**, not measured false-positive accuracy: there is no
scene ground truth. DA3 Small 280 and Metric Large stayed UNKNOWN on both CPU
and GPU. Small 504 was UNKNOWN on GPU but CLEAR on two CPU captures; Base produced
mixed states. These differences are not proof of obstacle recognition or a newly
qualified movement policy.

The production quality gate prevents all these diagnostic CLEAR values from
becoming motion decisions. This experiment supports keeping that gate, not
lowering its threshold to force a model result. Better lighting/exposure and
independent labelled scenes are needed before claiming low-light navigation.
CPU Base/Metric remain unsuitable for the existing 300 ms decision budget;
GPU timing alone still excludes camera/network/ACK age.

## Reproduce
Use the local private manifest with the existing comparison runner:
```sh
make research-depth ARGS="--backend da3-small --model data/da3-research/small --manifest PRIVATE_MANIFEST --output docs_dev/low-light.json --device cuda --resolution 280 --quality-mode model_probe"
```
Omit model_probe to retain production quality rejection. Use the model/revision
and dependency setup in [DA3_RESEARCH](DA3_RESEARCH.md). Metric uses a separately
declared 2048 MiB allocator cap; the other configurations use 1024 MiB.
Raw reports preserve hardware, input hashes, thresholds, quality statistics,
latencies and all per-frame assessments.
