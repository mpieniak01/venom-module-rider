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
