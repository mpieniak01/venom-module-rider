# DA3 recorded-scene evaluation — 2026-10-15
Decision: retain DA2 as production default. Keep DA3 Small as a selectable **offline**
second path. No DA3 motion qualification or production deployment is claimed.

## Data and procedure
23 unique historical Rider JPEGs: five operator-confirmed free-floor frames, four
operator-confirmed white-box frames (~20 cm), eight dark frames and six unlabelled
placement transitions. The household images and per-frame reports remain private.
These are frames of a small number of scenes, not 23 independent environments.
Same bytes, 15% STOP corridor, broader fitting context and unchanged plane thresholds.
No model-specific fitting or threshold search was performed.

Each GPU configuration repeated every JPEG five times (115 samples); CPU three times
(69). There are 920 replay samples across ten configurations, not 920 distinct scenes.
Two warmup runs preceded each configuration. Every frame is assessed independently;
this is not a live run, clear-frame admission or network/permit test.

RTX 3060 12 GiB, WSL Ubuntu-Venom, Torch 2.11.0+cu130, four CPU threads. Shared running
workstation, not an isolated performance lab. Other tests/services can affect timings.
DA2 uses its production processor and fp32; DA3 GPU uses the SDK-style bf16 autocast,
CPU fp32. Both resize output back to the original view before scoring. Whole-view DA3
280/504 differ in compute from DA2; do not attribute all differences to architecture.

## Measured inference path
P95 in ms **excludes the eight dark-frame groups**, which exit before model execution.
Timing includes JPEG read/decode, preprocessing, inference, output resize and assessment.
It excludes Pi camera capture, network, dispatch/ACK and motor execution.
Memory is Torch's peak live tensor allocation, not total process/GPU occupancy.

| Backend | GPU P95 ms | CPU P95 ms | GPU tensor peak MiB |
|---|---:|---:|---:|
| DA2 production | 33.4 | 299.4 | 185 |
| DA3 Small 280 | 44.6 | 128.9 | 175 |
| DA3 Small 504 | 48.4 | 404.5 | 256 |
| DA3 Base 504 | 71.9 | 965.9 | 748 |
| DA3 Metric Large 504 | 95.7 | 2846.5 | 1504 |

DA3 Small/Base and DA2 used a 1024 MiB process allocator limit. Metric Large failed
with OutOfMemoryError under that limit and completed with a separately declared
2048 MiB limit. This is a resource-budget result, not a claim that Metric cannot run
on a 12 GiB card. CPU Base/Metric and Small 504 exceed the current 300 ms budget.
Low model latency alone does not prove the whole live loop meets that budget.

The initial CUDA attempt failed because libnvrtc-builtins.so.13.0 was not on the
process library search path. Setting LD_LIBRARY_PATH only for the research process
to the existing nvidia/cu13/lib fixed it. Production environment was not modified.
The isolated research venv reuses the existing host vision dependencies; no SDK
exporter/3D dependency stack was installed into the host environment.

## Quality
- All five confirmed free-floor JPEGs were CLEAR on every backend/repeat.
- None of the four labelled box JPEGs or eight dark JPEGs was CLEAR.
- Most box assessments were UNKNOWN due to plane orientation, rather than an
  explicit OBSTACLE classification. DA3 Small 504 classified one of four as OBSTACLE;
  DA2 and the other tested DA3 configurations returned UNKNOWN for all four.
  This preserves STOP for these inputs but does not demonstrate obstacle recognition.
- No repeated identical JPEG changed state within a configuration. This does not
  establish temporal consistency on a live camera or under robot motion.
- Several unlabelled placement frames were CLEAR. They must not be counted as
  confirmed safe floor; the current labelled sample cannot establish general safety.

The current inverse-depth plane heuristic is therefore part of the limitation.
Changing only the depth backbone has not established a general navigation improvement.
No metric distance errors were computed: this dataset has no dense calibrated metric
ground truth. The operator's approximate box distance is not such ground truth.

## Model-family choice
Official DA3 Small/Base provide relative depth and camera geometry; Metric Large
requires focal-length scaling before interpreting metres. All three chosen variants
are Apache-2.0 according to their official cards. The larger Any-view Large/Giant and
Nested family (including refreshed -1.1 checkpoints) add substantially more compute,
and their cards list CC BY-NC 4.0; they were researched but not benchmarked here.
DA3 Mono Large and streaming reconstruction are separate future experiments.
Streaming is not automatically suitable for bounded one-frame control latency.

Use the native SDK network and input processor at the recorded commit, with strict
safetensors shared-weight loading. Hashes, checkpoint revisions and SDK revision are
recorded by the runner. Report actual loaded parameter count separately from rounded
model-family parameter figures in marketing tables.

## Follow-up before a production second path
1. Independent floors, low/strong light, slopes, thin/white obstacles and recesses;
   label sequences separately from tuning. Treat unsafe CLEAR as a failure.
2. Compare orientation/plane failure and available DA3 confidence; investigate the
   assessment heuristic before raising acceptance thresholds.
3. Measure real temporal stability and full capture-to-decision age, including ACK,
   with replay/controlled observation. Preserve the existing deadline and motion caps.
4. Only after qualification add a PL/EN/DE panel choice through the existing domain
   service, with Pi revision/model identity and unchanged watchdogs. One authoritative
   selected model, no silent fallback and no parallel GPU lane that starves decisions.

See [reproduction and official sources](DA3_RESEARCH.md).


## New current-camera low-light sequence
See [the separate low-light benchmark](DA3_LOW_LIGHT_RESULTS.md): 20 new RAW frames,
production quality rejection versus explicit offline inference probes.
