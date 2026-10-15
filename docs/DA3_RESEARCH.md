# Depth Anything 3 research lane
This offline lane compares production DA2 depth with DA3 Small, Base and Metric Large.
It has no Pi client, ARM, motion command or automatic production fallback.
Model selection is explicit through `--backend`; the running panel keeps its current model.

## Reproduce
Use a separate venv with the host vision_depth dependencies plus
`requirements-da3-research.txt`. Keep SDK, weights, datasets and reports in ignored
`data/` and `docs_dev/`. Do not commit household images.

Checkout the official SDK at
`3d835ec1a5802d64a8b8b15f817a1ab54809bfe4`:
https://github.com/ByteDance-Seed/Depth-Anything-3
Use the pinned model revisions in `scripts/depth_candidates.py`, e.g.:
```sh
hf download depth-anything/DA3-SMALL config.json model.safetensors   --revision e08cab65ca0ec38e7826075418411ab90cab4da3   --local-dir data/da3-research/small
PYTHONPATH=.:data/da3-research/sdk/src data/da3-research/venv/bin/python   scripts/compare_depth_models.py --backend da3-small   --model data/da3-research/small --manifest data/da3-research/dataset/manifest.json   --output docs_dev/da3-small-cpu.json --device cpu --resolution 504
```
The dataset manifest is a JSON list of `file`, `expected`, `sha256`.
Use explicit `clear`, `obstacle`, `unknown` or `unlabelled` labels.
Paths are relative to the manifest; the runner verifies hashes and rejects duplicates
and path escapes. Label transition recordings unlabelled, never infer CLEAR from a filename.

## Method and limitations
One model per process, warmup excluded, synchronized CUDA timing, fixed CPU thread count.
Every sample includes file read, decode, preprocessing, inference, resizing and assessment.
Cold load and warmup, P50/P95/P99, deadline misses, peak RSS/CUDA allocation and confusion
counts are separate. Repeats of an identical JPEG measure repeatability, not live temporal
consistency or camera freshness. Dark images go through the production quality gate.
No per-model threshold tuning: production near STOP corridor and inverse-depth plane.
DA2 retains its production processor; DA3 uses 280 or 504 whole-view resize. Always report
resolution with timing; this is an operational comparison, not equal-compute benchmarking.
The native SDK network/input processor avoids importing unrelated 3D export dependencies.
Safetensors strict loading restores shared parameter aliases.

Small/Base are relative-depth models, not centimetre sensors. Metric Large's raw depth
needs the camera focal length conversion before interpreting metres; this lane only
evaluates relative geometry and claims no measured clearance.
Candidate quality does not itself grant motion. Model latency alone does not establish
the existing 300 ms capture-to-decision budget. Do not increase watchdog deadlines.
A candidate may enter an active selectable production lane only after scene and full
latency qualification; the existing DA2 route remains the baseline.

## Official sources
- [Model family, licences and metric focal conversion](https://github.com/ByteDance-Seed/Depth-Anything-3)
- [Small checkpoint](https://huggingface.co/depth-anything/DA3-SMALL)
- [Base checkpoint](https://huggingface.co/depth-anything/DA3-BASE)
- [Metric Large checkpoint](https://huggingface.co/depth-anything/DA3METRIC-LARGE)


## Setup and execution
A clean separate environment can install the module's `vision_depth` extra and
`requirements-da3-research.txt`. This workspace instead reused its existing host
site-packages through a local .pth file and installed missing packages only in
`data/da3-research/venv`; that local environment file is not distributed.
The native network path does not require xformers, open3d, pycolmap or Gradio.
Clone the SDK to `data/da3-research/sdk`, checkout the pinned commit, then add its
`src` to PYTHONPATH as shown above. Runtime source/version and checkpoint checks fail
closed. The SDK checkout must have no tracked source modifications.

`make research-depth ARGS="--backend ... --model ... --manifest ... --output ..."`
runs the same offline command. Choose `--device cuda` explicitly; its default allocator
limit is 1024 MiB, overridable with `--cuda-memory-mib`. Model selection never occurs
automatically. Run models sequentially to avoid measuring mutual contention.
Reports are never overwritten; failures write `status: failed` and exit nonzero.

[Measured results and next-step criteria](DA3_RESULTS.md).


## Low-light replay
The default `--quality-mode production` keeps the exact production image-quality
rejection before inference. Its sub-millisecond rejection timings are not model speed.
An explicitly selected `--quality-mode model_probe` runs the same saved dark image
through the depth backend despite failing that offline gate. This is diagnostic only:
the top-level report and per-frame assessment never support motion; it does not
change any live image gate, threshold or Pi contract.
Reports include RGB mean/std, quality result, labelled-frame count, actual inference
count and low-quality CLEAR count. Unlabelled low-light scenes have no measured
obstacle/free-floor accuracy; a zero false-CLEAR counter with zero labelled frames
must not be interpreted as perfect accuracy.


## Continuous video and temporal windows
[Online MJPEG, codec and causal-window experiments](VIDEO_SEQUENCE_RESEARCH.md)
separate delivery latency from changes in pixel decoding and joint DA3 attention.


## Retained configuration after evaluation
The live panel keeps DA2 at `data/depth-small`. The versioned
`config/vision-research.json` records this baseline and the retained
`da3-small-280` offline option. It is a research configuration, not a new
production backend or automatic fallback. Use:

```sh
make research-depth ARGS="--profile da3-small-280 --device cuda --manifest PATH/manifest.json --output docs_dev/small-retained.json"
```

The profile supplies backend, local model directory and 280-pixel resolution;
do not combine it with explicit model/backend/resolution arguments. Device
selection and the production quality gate keep their existing defaults.

Keep DA3 Small weights and the pinned SDK. Base and Metric Large local weights
were removed after comparison to reclaim disk; their benchmark reports, pinned
revisions and comparison code remain reproducible. Larger candidates require
an explicit download before reuse. No models are downloaded at startup.

The video runner, causal 1/3/5-frame windows and documented streaming experiments
remain available for further development. They do not enable a live video lane
or robot motion. See [video experiments](VIDEO_SEQUENCE_RESEARCH.md).
