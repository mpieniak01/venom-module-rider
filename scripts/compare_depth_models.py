"""Compare real depth weights on explicit labelled recordings; never contact Pi."""

import argparse
import io
import json
import math
import resource
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.depth_candidates import Candidate, file_hash
from venom_module_rider.depth_detector import PerceptionSettings, assess_depth


def load_dataset(manifest):
    """Require explicit labels/hashes; transition frames cannot become CLEAR."""
    manifest = Path(manifest).resolve()
    rows = json.loads(manifest.read_text())
    if not isinstance(rows, list) or not rows:
        raise ValueError("dataset_empty")
    seen = set()
    for row in rows:
        path = (manifest.parent / row["file"]).resolve()
        if row["expected"] not in {"clear", "obstacle", "unknown", "unlabelled"}:
            raise ValueError("invalid_label")
        if path in seen or not path.is_relative_to(manifest.parent):
            raise ValueError("dataset_path_or_duplicate")
        seen.add(path)
        if file_hash(path) != row["sha256"]:
            raise ValueError("dataset_hash_mismatch")
        row["path"] = path
    return rows


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def summarize(rows):
    values = [ms for row in rows for ms in row["latency_ms"]]
    labelled = [r for r in rows if r["expected"] != "unlabelled"]
    inferred = [
        ms
        for row in rows
        for result, ms in zip(
            row.get(
                "assessments", [{"reason": "not_recorded"}] * len(row["latency_ms"])
            ),
            row["latency_ms"],
            strict=True,
        )
        if result["reason"] != "vision_image_quality"
    ]
    return {
        "inferred_samples": len(inferred),
        "inferred_p95_ms": percentile(inferred, 0.95) if inferred else None,
        "frames": len(rows),
        "samples": len(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "over_300ms": sum(v >= 300 for v in values),
        "false_clear_samples": sum(
            r["expected"] in {"obstacle", "unknown"} and s == "clear"
            for r in labelled
            for s in r["states"]
        ),
        "floor_non_clear_samples": sum(
            r["expected"] == "clear" and s != "clear"
            for r in labelled
            for s in r["states"]
        ),
        "unstable_frames": sum(len(set(r["states"])) > 1 for r in rows),
        "confusion": dict(
            Counter(r["expected"] + "->" + s for r in rows for s in r["states"])
        ),
    }


def benchmark(args):
    import torch

    torch.set_num_threads(args.threads)
    if args.device == "cuda":
        torch.cuda.set_per_process_memory_fraction(
            args.cuda_memory_mib
            * 2**20
            / torch.cuda.get_device_properties(0).total_memory
        )
    dataset = load_dataset(args.manifest)
    settings = PerceptionSettings(engine="depth_plane")
    corridor = (0.35, 0.80, 0.65, 0.95)
    started = time.perf_counter()
    model = Candidate(args.backend, args.model, args.device, args.resolution)
    if args.device == "cuda":
        torch.cuda.synchronize()
    load_ms = (time.perf_counter() - started) * 1000
    if args.device == "cuda":
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()

    def evaluate(row):
        started = time.perf_counter()
        payload = row["path"].read_bytes()
        with Image.open(io.BytesIO(payload)) as opened:
            if (
                opened.format not in {"JPEG", "PNG"}
                or not (32 <= opened.width <= 1280 and 32 <= opened.height <= 720)
                or len(payload) > 512 * 1024
            ):
                raise ValueError("vision_image_bounds")
            image = opened.convert("RGB")
        rgb = np.asarray(image, dtype=np.float32) / 255
        if rgb.mean() < 0.04 or rgb.std() < 0.01:
            result = {"state": "unknown", "reason": "vision_image_quality"}
        else:
            depth = model.infer(image)
            result = assess_depth(depth, corridor, settings)
            result.pop("_errors")
        if args.device == "cuda":
            torch.cuda.synchronize()
        return result, (time.perf_counter() - started) * 1000

    warm = next((row for row in dataset if row["expected"] == "clear"), dataset[0])
    start = time.perf_counter()
    for _ in range(args.warmup):
        evaluate(warm)
    warmup_ms = (time.perf_counter() - start) * 1000
    results = []
    # Same input repeated checks deterministic stability, not live video freshness.
    for row in dataset:
        samples = [evaluate(row) for _ in range(args.repeats)]
        results.append(
            {
                "file": row["file"],
                "sha256": row["sha256"],
                "expected": row["expected"],
                "latency_ms": [round(ms, 3) for _, ms in samples],
                "states": [result["state"] for result, _ in samples],
                "assessments": [result for result, _ in samples],
            }
        )
    return {
        "status": "completed",
        "model": model.metadata,
        "load_ms": load_ms,
        "warmup_ms": warmup_ms,
        "warmup_samples": args.warmup,
        "threads": args.threads,
        "cuda_memory_limit_mib": args.cuda_memory_mib
        if args.device == "cuda"
        else None,
        "gpu": torch.cuda.get_device_name(0) if args.device == "cuda" else None,
        "input_manifest_sha256": file_hash(args.manifest),
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "peak_cuda_allocated_mib": torch.cuda.max_memory_allocated() / 2**20
        if args.device == "cuda"
        else None,
        "peak_cuda_reserved_mib": torch.cuda.max_memory_reserved() / 2**20
        if args.device == "cuda"
        else None,
        "settings": settings.model_dump(),
        "corridor": corridor,
        "drive_supported": False,
        "limitations": [
            "Repeated recordings are not a live freshness or transport test.",
            "Model timing excludes Pi capture, network, ACK and motor execution.",
            "Recorded labels are scene labels, not metric ground truth.",
            "No model-specific threshold tuning or calibrated metre clearance.",
            "DA2 production preprocessing and DA3 resolutions are reported separately.",
        ],
        "summary": summarize(results),
        "rows": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=["da2", "da3-small", "da3-base", "da3metric-large"],
        required=True,
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--resolution", type=int, choices=[280, 504], default=504)
    parser.add_argument("--threads", type=int, choices=range(1, 17), default=4)
    parser.add_argument("--repeats", type=int, choices=range(1, 101), default=5)
    parser.add_argument("--warmup", type=int, choices=range(1, 11), default=2)
    parser.add_argument(
        "--cuda-memory-mib", type=int, choices=range(256, 12289), default=1024
    )
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("report already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = benchmark(args)
    except Exception as error:
        args.output.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "backend": args.backend,
                    "device": args.device,
                    "drive_supported": False,
                    "error_type": type(error).__name__,
                    "error": str(error)[:2000],
                },
                indent=2,
            )
        )
        raise
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
