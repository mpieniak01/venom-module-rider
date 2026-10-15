# Continuous video and causal DA3 windows — 2026-10-15

## Question and separation
Does continuous video help compared with individual JPEG requests? Test separately:
1. persistent transport and online decode of the existing producer's images;
2. compression/decoder changes;
3. joint inference of previous frames instead of one independent frame.

Decoded video is still a sequence of images. Changing its transport does not
itself add scene information or turn a depth estimator into an object detector.

## Current device and bounded live tests
Rider-Pi has FFmpeg/libcamera tools and a camera producer. The audited production
API serves individual JPEG snapshots; no deployed MJPEG/H.264/RTSP video endpoint
was found. Port 8081 is the motion HTTP-to-ZMQ bridge, not a video server.

For research only, a bounded foreground SSH exporter read the existing atomic
JPEG files and emitted a continuous multipart MJPEG stream. Venom received and
decoded that stream online; the existing camera was not opened a second time.
The exporter/localhost relay and camera were shut down after each approximately
20-second capture. No production endpoint, systemd unit or Pi source was changed.
The initial 10 Hz polling pilot was superseded by a 20 Hz polling comparison.

Matched-rate comparison:
- Continuous stream: **269** source frame IDs in 20.05 s.
- RAW HTTP polling requested at 20 Hz: **379** completed requests, **242** distinct
  frame IDs, no HTTP errors. These are observed receiver counts, not a sensor FPS claim.
- **228** source IDs common to both paths; every paired JPEG SHA-256 matched.
- The stream arrived first for **222/228** common IDs. HTTP first-arrival lag
  relative to the stream: median **24.5 ms**, P95 **64.6 ms**.
- Online JPEG decode P95: **0.31 ms**. HTTP request plus decode P95: **86.9 ms**;
  these scopes differ, so their difference is not pure decoder overhead.
- Stream JPEG bodies total 633324 bytes; HTTP bodies including repeats 880035 bytes.
  Headers, SSH overhead and radio/CPU energy were not measured.
- Online OpenCV imdecode and Pillow produced bit-identical RGB for these frames.

Both paths ran concurrently on a shared system. Stream export skipped duplicate and
older-than-300-ms files; RAW HTTP retains its existing route freshness contract.
First-arrival differences above use common source IDs on the same receiver clock,
not a comparison of unrelated frames or device clocks.

**Absolute capture-to-result freshness is not established.** An attempted clock
calibration using a Pi control-state timestamp produced impossible negative ages
outside RTT uncertainty; these derived ages were rejected. The original private
measurements are preserved, and no deadline claim uses them. File mtime/capture-read
completion is not sensor exposure time. A dedicated fresh timestamp contract
requires a separate investigation.

## Full online model observation
A separate 20-second run exercised **Pi camera -> continuous MJPEG -> Venom decode
-> DA3 Small 280** with a bounded latest-only five-frame buffer. One inference at
a time; windows 1/3/5 alternated across new incoming frames. History was copied at
admission, so newly arriving frames could not become future input to an active query.
No decision was sent back to the robot.

318 frame IDs received, **310 model assessments**, zero inference errors.
Four frames were skipped between consecutive assessment indices by the latest-only
consumer; startup/tail frames are not counted in that skip statistic.
All 310 assessments were UNKNOWN in this dark scene.

| Window | Assessments | Inference+assessment P95 ms | Host receive-to-result P95 ms |
|---:|---:|---:|---:|
| 1 | 104 | 47.0 | 48.9 |
| 3 | 103 | 58.4 | 59.0 |
| 5 | 103 | 61.5 | 62.1 |

Host receive-to-result excludes the upstream Pi/network age. These are diagnostic
observations, not proof of permission to move. Model windows have no automatic ARM
or motion capability. Pi remained DISARMED/output zero; camera was off afterward.

## Controlled replay: identical endpoint frames
On the 269-frame recording, compare 32 shared chronological endpoint indices:
single-frame DA2/Metric and DA3 Small/Base with causal windows of 1/3/5.
Only the last view's depth is scored; reference view is fixed to first to preserve
the input order. No future frame or repeated identical JPEG is inserted into a window.
Previous frames are recomputed on every query: this is bounded joint attention,
**not** the full stateful DA3-Streaming reconstruction pipeline.

Three representations: original JPEG/Pillow, a recorded MJPEG decoded through
OpenCV/FFmpeg, and H.264 CRF23 encoded **locally on Venom** then decoded.
Five model configurations and eleven model/window combinations per representation,
**1056 terminal-frame assessments** excluding setup calls.
All runs use CUDA, at most a 2 GiB process allocator budget, the original near corridor,
plane thresholds and bf16/fp32 rules. One setup query per window; not an isolated
latency SLA. Models with no joint-view attention are not advertised as temporal.

Source JPEG/Pillow replay (C/O/U = CLEAR/OBSTACLE/UNKNOWN out of 32):

| Backend | Window | GPU inference+assessment P95 ms | C/O/U | Changes between sampled endpoints |
|---|---:|---:|---:|---:|
| da2 504 | 1 | 32.4 | 0/0/32 | 0 |
| da3-small 280 | 1 | 32.6 | 0/0/32 | 0 |
| da3-small 280 | 3 | 50.6 | 0/0/32 | 0 |
| da3-small 280 | 5 | 64.8 | 0/0/32 | 0 |
| da3-small 504 | 1 | 48.3 | 0/0/32 | 0 |
| da3-small 504 | 3 | 91.9 | 0/0/32 | 0 |
| da3-small 504 | 5 | 130.6 | 0/0/32 | 0 |
| da3-base 504 | 1 | 71.4 | 5/6/21 | 13 |
| da3-base 504 | 3 | 159.5 | 12/6/14 | 18 |
| da3-base 504 | 5 | 253.7 | 20/2/10 | 11 |
| da3metric-large 504 | 1 | 97.4 | 0/0/32 | 0 |

Base windows did not establish improved navigation quality. On this unlabelled dark
scene, five-frame context increased unverifiable CLEAR (5 -> 20 of 32), while
reducing the count of state changes (13 -> 11). Fewer changes alone is not evidence
of better obstacle recognition. Small remained UNKNOWN on source JPEG inputs.

The recording had a file-publication gap as large as **804 ms**. Selected three-frame
windows spanned up to **560 ms**, five-frame windows up to **1196 ms**. Therefore
frame count is not a valid substitute for context age. The smoother online run had
maximum spans of 140 ms/264 ms for its three-/five-frame observations.

## Codec and decoder effects
The transport preserved JPEG bytes; a different decoder can still alter pixels.
Mean RGB absolute difference from original Pillow decoding (8-bit channel units):
- Online OpenCV imdecode: 0.
- OpenCV/FFmpeg MJPEG file decoding: about 0.552.
- H.264 CRF23 file decoding: about 0.577.

Those small changes sometimes changed the threshold-based geometry assessment.
For example, Small 504 on source JPEGs returned UNKNOWN for all 32 endpoints at every
window size. MJPEG/FFmpeg replay produced respectively 1/2/3 CLEAR at windows 1/3/5.
This is not evidence that streaming discovered a clear road.

H.264 stored clip size was 343458 bytes versus 633324 for MJPEG bodies (about 46%
smaller). This was a local codec experiment: **Pi H.264 encoding, live H.264 transport,
encoder power and H.264 reconnect latency were not tested**. No camera H.264 capability
or energy saving is inferred from the presence of ffmpeg on the device.

## Conclusion and next increment
Persistent latest-frame delivery showed earlier receipt and fewer repeated payloads
in this experiment. Neither the codec experiment nor joint DA3 windows established
a recognition-quality improvement. Low-light inputs have no labelled obstacle/free
ground truth; all tests remain observation only, with the live quality gate intact.

Before production adoption: a native streaming contract with source identity and
reliable age, bounded latest-only decode, reconnect/backpressure tests, explicit
context age limits, and labelled changing scenes under usable light. Compare energy
only with measurements. Keep the existing 300 ms decision budget and motion caps.

## Reproduce saved-video inference
Keep private captures/videos/transport manifests outside Git, then:
```sh
PYTHONPATH=.:data/da3-research/sdk/src data/da3-research/venv/bin/python scripts/compare_depth_video.py --backend da3-small --model data/da3-research/small --manifest PRIVATE_MANIFEST --transport PRIVATE_TRANSPORT_JSON --video PRIVATE_MJPEG --output docs_dev/video.json --device cuda --resolution 280 --windows 1 3 5 --quality-mode model_probe
```
Use --decoder jpeg for the exact original-pixel control. Omit model_probe to retain
quality rejection. Tested OpenCV 5.0.0.93, FFmpeg 6.1.1, NumPy 2.3.5, Pillow 12.3.0.
See [base setup](DA3_RESEARCH.md) and the
[official DA3-Streaming reference](https://github.com/ByteDance-Seed/Depth-Anything-3/tree/main/da3_streaming)
for the distinct large reconstruction pipeline; it was not run here.
