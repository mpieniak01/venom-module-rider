"""Causal depth replay of a recorded continuous video; never contacts Pi."""

import argparse
import io
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from scripts.compare_depth_models import load_dataset, percentile
from scripts.depth_candidates import Candidate, file_hash
from venom_module_rider.depth_detector import PerceptionSettings, assess_depth


def window_indices(count, samples):
    if count < 5:
        raise ValueError("video_too_short")
    return np.unique(np.linspace(4, count - 1, min(samples, count - 4), dtype=int))


def decode_video(path, expected):
    import cv2

    if not 1 <= expected <= 600:
        raise ValueError("video_frame_bounds")
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    images: list[Image.Image] = []
    times: list[float] = []
    try:
        if not capture.isOpened():
            raise ValueError("video_open_failed")
        while len(images) <= expected:
            start = time.perf_counter()
            ok, frame = capture.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            images.append(Image.fromarray(rgb))
            times.append((time.perf_counter() - start) * 1000)
    finally:
        capture.release()
    if len(images) != expected:
        raise ValueError("video_frame_count_changed")
    return images, times


def assess_window(candidate, images, times, index, width, mode):
    history = images[index - width + 1 : index + 1]
    if len(history) != width:
        raise ValueError("window_incomplete")
    quality = all(
        float(rgb.mean()) >= 0.04 and float(rgb.std()) >= 0.01
        for rgb in (np.asarray(image, dtype=np.float32) / 255 for image in history)
    )
    started = time.perf_counter()
    result: dict[str, Any]
    if not quality and mode == "production":
        result = {"state": "unknown", "reason": "vision_image_quality"}
    else:
        depth = candidate.infer_window(history)
        result = assess_depth(
            depth, (0.35, 0.80, 0.65, 0.95), PerceptionSettings(engine="depth_plane")
        )
        result.pop("_errors")
    result.update(
        {
            "drive_supported": False,
            "distance_measurement": False,
            "image_quality_passed": quality,
            "index": int(index),
            "history_indices": list(range(index - width + 1, index + 1)),
            "history_span_ms": (times[index] - times[index - width + 1]) * 1000,
            "inference_assessment_ms": (time.perf_counter() - started) * 1000,
        }
    )
    return result


def summarize_sequence(rows):
    latency = [r["inference_assessment_ms"] for r in rows]
    states = [r["state"] for r in rows]
    return {
        "samples": len(rows),
        "inferred_samples": sum(r["reason"] != "vision_image_quality" for r in rows),
        "states": dict(Counter(states)),
        "sampled_state_changes": sum(
            a != b for a, b in zip(states, states[1:], strict=False)
        ),
        "p50_ms": percentile(latency, 0.50),
        "p95_ms": percentile(latency, 0.95),
        "over_300ms": sum(t >= 300 for t in latency),
        "max_history_span_ms": max(r["history_span_ms"] for r in rows),
        "low_quality_clear": sum(
            not r["image_quality_passed"] and r["state"] == "clear" for r in rows
        ),
    }


def replay(args):
    import torch

    torch.set_num_threads(4)
    if args.device == "cuda":
        torch.cuda.set_per_process_memory_fraction(
            args.cuda_memory_mib
            * 2**20
            / torch.cuda.get_device_properties(0).total_memory
        )
    rows = load_dataset(args.manifest)
    original, jpeg_decode_ms = [], []
    for row in rows:
        start = time.perf_counter()
        with Image.open(io.BytesIO(row["path"].read_bytes())) as image:
            original.append(image.convert("RGB"))
        jpeg_decode_ms.append((time.perf_counter() - start) * 1000)
    if args.decoder == "jpeg":
        images, decode_ms = original, jpeg_decode_ms
    else:
        images, decode_ms = decode_video(args.video, len(rows))
    if any(a.size != b.size for a, b in zip(original, images, strict=True)):
        raise ValueError("video_frame_geometry_changed")
    mae = [
        float(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)).mean())
        for a, b in zip(original, images, strict=True)
    ]
    transport = json.loads(args.transport.read_text())
    source = transport["stream"]
    if len(source) != len(rows) or any(
        row["sha256"] != frame["sha256"]
        for row, frame in zip(rows, source, strict=True)
    ):
        raise ValueError("video_source_identity_changed")
    times = [row["captured_at"] for row in source]
    if not all(np.isfinite(t) and t > 0 for t in times):
        raise ValueError("invalid_source_time")
    if len(times) != len(rows) or any(
        b <= a for a, b in zip(times, times[1:], strict=False)
    ):
        raise ValueError("source_timestamps_not_monotonic")
    candidate = Candidate(args.backend, args.model, args.device, args.resolution)
    indices = window_indices(len(images), args.samples)
    runs = []
    for width in args.windows:
        warmup = assess_window(
            candidate, images, times, int(indices[0]), width, args.quality_mode
        )
        measured = [
            assess_window(
                candidate, images, times, int(index), width, args.quality_mode
            )
            for index in indices
        ]
        runs.append(
            {
                "window": width,
                "warmup_inferred": warmup["reason"] != "vision_image_quality",
                "summary": summarize_sequence(measured),
                "rows": measured,
            }
        )
    return {
        "drive_supported": False,
        "quality_mode": args.quality_mode,
        "model": candidate.metadata,
        "video_frames": len(images),
        "video_sha256": file_hash(args.video) if args.decoder == "video" else None,
        "decoder": args.decoder,
        "manifest_sha256": file_hash(args.manifest),
        "video_decode_p95_ms": percentile(decode_ms, 0.95),
        "pixel_mae_mean": float(np.mean(mae)),
        "pixel_mae_max": max(mae),
        "source_timestamp": "Pi file mtime, not sensor exposure",
        "timing": "predecoded causal replay: inference plus assessment, not transport",
        "runs": runs,
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
    parser.add_argument("--transport", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cpu")
    parser.add_argument("--resolution", type=int, choices=[280, 504], default=280)
    parser.add_argument(
        "--windows", type=int, choices=[1, 3, 5], nargs="+", default=[1]
    )
    parser.add_argument(
        "--quality-mode", choices=["production", "model_probe"], default="production"
    )
    parser.add_argument("--samples", type=int, choices=range(1, 65), default=32)
    parser.add_argument(
        "--cuda-memory-mib", type=int, choices=range(256, 4097), default=2048
    )
    parser.add_argument("--decoder", choices=["jpeg", "video"], default="video")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError("report exists")
    report = replay(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents clobbering an output inserted during replay.
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, allow_nan=False)
    print(json.dumps([{"window": r["window"], **r["summary"]} for r in report["runs"]]))


if __name__ == "__main__":
    main()
