"""Offline replay of recorded Pi frames. No sockets or hardware commands."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from venom_module_rider.depth_detector import DepthDetector, PerceptionSettings
from venom_module_rider.vision_contract import VisionFrame
from venom_module_rider.vision_loop import VisionLoop


async def replay(dataset, output):
    output.mkdir(parents=True, exist_ok=True)
    detector = DepthDetector()
    settings = PerceptionSettings(engine="depth_plane")
    files = sorted(dataset.glob("*.jpg"))
    if not files:
        raise ValueError("dataset_empty")
    # Provisioning and first CUDA warmup are outside the steady-state deadline.
    cold_start = time.perf_counter()
    detector.detect(files[0].read_bytes(), (0.35, 0.65, 0.65, 0.95), settings)
    cold_ms = (time.perf_counter() - cold_start) * 1000
    loop = VisionLoop()
    loop.depth_detector = detector
    rows = []
    try:
        for index, path in enumerate(files):
            elapsed = []
            for sequence in range(1, 4):
                frame = VisionFrame(
                    protocol_version=1,
                    run_id="offline-" + str(index),
                    frame_id="frame-" + str(sequence),
                    sequence=sequence,
                    config_revision=0,
                    age_ms=0.0,
                    deadline_ms=300,
                    mode="observe",
                    perception=settings,
                )
                result = await loop.decide(
                    "offline", frame, path.read_bytes(), with_preview=True
                )
                elapsed.append(result["inference_ms"])
            preview = result.pop("_preview", None)
            if preview:
                (output / (path.stem + "-geometry.jpg")).write_bytes(preview)
            expected = (
                "clear"
                if path.name.startswith("clear-after-box-")
                else "stop"
                if path.name.startswith("box-20cm-")
                else "transition"
            )
            rows.append(
                {
                    "file": path.name,
                    "expected": expected,
                    "latency_ms": elapsed,
                    **result,
                }
            )
    finally:
        await loop.close()
    report = {
        "method": "relative_inverse_depth_plane",
        "cold_start_ms": cold_ms,
        "physical_clearance": "unverified",
        "replay_semantics": (
            "Each recording is submitted three times with synthetic frame IDs; "
            "this checks decision gating, not capture freshness or temporal accuracy."
        ),
        "drive_supported": False,
        "rows": rows,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "cold_start_ms": cold_ms,
                "frames": len(rows),
                "clear_images_go": sum(
                    r["expected"] == "clear" and r["vision_action"] == "go"
                    for r in rows
                ),
                "box_images_stop": sum(
                    r["expected"] == "stop" and r["vision_action"] == "stop"
                    for r in rows
                ),
                "pi_go": sum(r["action"] == "go" for r in rows),
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(replay(args.dataset, args.output))


if __name__ == "__main__":
    main()
