"""Explicit bounded provisioning child: no actuator, browser or shared GPU access."""

from __future__ import annotations

import argparse
import json
import os
import shutil

from .analysis_profiles import ProfileCatalog
from .analysis_worker import process_limits
from .perception_catalog import DEPTH, SEGFORMER, SOURCES, YOLO, digest_file
from .perception_runtime import build_detector


def report(state, progress):
    print(json.dumps({"state": state, "progress": progress}), flush=True)


def download(catalog, model_id):
    """Only immutable allowlisted repositories; no caller URL, code or pickle."""
    from huggingface_hub import hf_hub_download

    output = catalog.path(model_id)
    if output.exists():
        # Never replace a legacy or damaged installation without operator review.
        catalog.qualify_files(model_id)
        return
    source = SOURCES[model_id]
    staging = output.with_name(output.name + ".staging")
    if staging.exists():
        raise ValueError("model_install_interrupted")
    staging.mkdir(parents=True)
    try:
        files = {}
        for index, name in enumerate(
            ("config.json", "preprocessor_config.json", "model.safetensors")
        ):
            report("downloading", 5 + index * 20)
            cached = hf_hub_download(source["repo"], name, revision=source["revision"])
            target = staging / name
            shutil.copyfile(cached, target)
            if target.stat().st_size > 256 * 1024**2:
                raise ValueError("model_file_bounds")
            files[name] = digest_file(target)
        if files["model.safetensors"] != source["sha256"]:
            raise ValueError("model_hash_mismatch")
        metadata = (
            {"model": source["repo"], "revision": source["revision"], "files": files}
            if model_id == DEPTH
            else {"model_id": model_id, "revision": source["revision"], "files": files}
        )
        (staging / "manifest.json").write_text(json.dumps(metadata))
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def smoke(catalog, model_id):
    """A decode/output smoke proves runtime shape, never obstacle accuracy."""
    import io

    import numpy as np
    import torch
    from PIL import Image

    from .depth_detector import PerceptionSettings

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    artifact = catalog.qualify_files(model_id)
    detector = build_detector(catalog, model_id, artifact)
    rgb = np.random.default_rng(7).integers(40, 220, (96, 160, 3), dtype=np.uint8)
    stream = io.BytesIO()
    Image.fromarray(rgb).save(stream, format="JPEG")
    for iteration in range(2):
        report("verifying", 75 + iteration * 10)
        args = (stream.getvalue(), (0.35, 0.60, 0.65, 0.95))
        result = (
            detector.detect(*args, PerceptionSettings(engine="depth_plane"))
            if model_id == DEPTH
            else detector.detect(*args)
        )
        if result.get("state") not in {
            "clear",
            "unknown",
            "obstacle",
        } or not result.get("_preview"):
            raise ValueError("model_smoke_failed")
    catalog.save_receipt(model_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_id", choices=[DEPTH, SEGFORMER, YOLO])
    args = parser.parse_args()
    process_limits(os.getppid())
    catalog = ProfileCatalog().live()
    report("verifying", 1)
    try:
        catalog.qualify_files(args.model_id)
    except Exception:
        # Existing files with mismatches must fail, not be silently overwritten.
        if catalog.path(args.model_id).exists():
            raise
        if args.model_id == YOLO:
            from .perception_export import provision_yolo

            provision_yolo(catalog)
        else:
            download(catalog, args.model_id)
    report("verifying", 70)
    smoke(catalog, args.model_id)
    report("ready", 100)


if __name__ == "__main__":
    main()
