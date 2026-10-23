"""Pinned YOLO semantic export with numerical parity, never used by serving."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os

from .perception_catalog import SOURCES, YOLO, digest_file

EXPORTER = "8.4.184"


def provision_yolo(catalog):
    import httpx
    import numpy as np
    import onnxruntime as ort
    import torch
    import ultralytics

    if ultralytics.__version__ != EXPORTER:
        raise ValueError("model_exporter_mismatch")
    output = catalog.path(YOLO)
    if output.exists():
        catalog.qualify_files(YOLO)
        return
    staging = output.with_name(output.name + ".staging")
    staging.mkdir(parents=True, exist_ok=False)
    try:
        source = staging / "source.pt"
        digest = hashlib.sha256()
        with (
            httpx.stream(
                "GET",
                "https://github.com/ultralytics/assets/releases/download/"
                "v8.4.0/yolo26n-sem-ade20k.pt",
                follow_redirects=True,
                timeout=60,
            ) as response,
            source.open("wb") as stream,
        ):
            response.raise_for_status()
            size = 0
            for block in response.iter_bytes():
                size += len(block)
                if size > 8 * 1024**2:
                    raise ValueError("model_file_bounds")
                digest.update(block)
                stream.write(block)
        if digest.hexdigest() != SOURCES[YOLO]["sha256"]:
            raise ValueError("model_hash_mismatch")
        os.environ["YOLO_AUTOINSTALL"] = "false"
        from typing import cast

        from ultralytics import YOLO as Loader

        model = cast(torch.nn.Module, Loader(str(source), task="semantic").model).eval()
        if model.names.get(3) != "floor" or len(model.names) != 150:
            raise ValueError("model_labels_invalid")
        # Export raw logits rather than upstream's class-only export: uncertainty
        # remains available, and postprocessing preserves original coordinates.
        pixels = torch.from_numpy(
            np.random.default_rng(7).random((1, 3, 640, 640), dtype=np.float32)
        )
        target = staging / "model.onnx"
        with (
            torch.inference_mode(),
            contextlib.redirect_stdout(__import__("sys").stderr),
        ):
            reference = model(pixels).numpy()
            torch.onnx.export(
                model,
                (pixels,),
                str(target),
                input_names=["images"],
                output_names=["logits"],
                opset_version=17,
                dynamo=False,
            )
        session = ort.InferenceSession(str(target), providers=["CPUExecutionProvider"])
        actual = session.run(None, {"images": pixels.numpy()})[0]
        if (
            reference.shape != actual.shape
            or not np.allclose(reference, actual, rtol=1e-3, atol=1e-3)
            or float((reference.argmax(1) == actual.argmax(1)).mean()) < 0.999
        ):
            raise ValueError("model_export_parity")
        files = {
            name: digest_file(staging / name) for name in ("model.onnx", "source.pt")
        }
        (staging / "manifest.json").write_text(
            json.dumps(
                {
                    "model_id": YOLO,
                    "revision": SOURCES[YOLO]["revision"],
                    "files": files,
                    "exporter": EXPORTER,
                    "opset": 17,
                    "input_size": 640,
                    "normalization": "rgb_0_1",
                    "resize": "letterbox_114",
                    "output": "logits_150_floor_3",
                    "parity": True,
                }
            )
        )
        staging.rename(output)
    finally:
        if staging.exists():
            import shutil

            shutil.rmtree(staging)
