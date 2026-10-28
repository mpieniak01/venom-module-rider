"""Owned CUDA model worker. Reads sources, returns derived bytes, never opens SQLite."""

from __future__ import annotations

import base64
import contextlib
import ctypes
import io
import json
import os
import resource
import signal
import sys
import time
from pathlib import Path
from typing import cast

_PNG_MIME = "image/png"


MAX_MESSAGE = 16 * 1024**2


def receive(stream):
    line = stream.readline(MAX_MESSAGE + 1)
    if not line or len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
        raise ValueError("analysis_protocol")
    result = json.loads(line)
    if not isinstance(result, dict):
        raise ValueError("analysis_protocol")
    return result


def emit(stream, message):
    message["sent_mono"] = time.perf_counter()
    encoded = json.dumps(message, allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_MESSAGE:
        raise ValueError("analysis_protocol_size")
    stream.write(encoded + b"\n")
    stream.flush()
    return receive(sys.stdin.buffer)


def process_limits(parent):
    """A paused child also dies on host loss; it never becomes a new daemon."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        raise RuntimeError("analysis_parent_lost")
    os.nice(10)
    available = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, available[:2])
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def artifact(name, payload, mime):
    return {"name": name, "mime": mime, "data": base64.b64encode(payload).decode()}


def side_of(assessment):
    sectors = assessment.get("sectors", {})
    left = sectors.get("left", {}).get("state") == "obstacle"
    right = sectors.get("right", {}).get("state") == "obstacle"
    if left and right:
        return "both"
    if left or right:
        return "left" if left else "right"
    return (
        "centre"
        if assessment["state"] == "obstacle"
        else "none"
        if assessment["state"] == "clear"
        else "unknown"
    )


def run(job, output, *, candidate_factory=None, semantic_factory=None):
    """Use the retained adapter and exactly the live decoder/geometry/overlay."""
    from .cuda_execution import require_cuda

    require_cuda()
    emit(output, {"type": "compute", "done": 0, "phase": "loading"})
    import numpy as np
    from PIL import Image

    from .analysis_inputs import VideoInputs, inputs_for, payload_for
    from .analysis_layers import classification_layer
    from .analysis_metrics import StreamingMetrics, adjacent, summarize
    from .depth_candidates import Candidate, file_hash
    from .depth_detector import (
        PerceptionSettings,
        assess_depth,
        decode_payload,
        render_depth,
    )
    from .errors import RiderError
    from .ground_config import GroundGeometry

    context, profile = job["context"], job["profile"]
    spec = context["spec"]
    inputs = inputs_for(job["root"], context)
    video = isinstance(inputs, VideoInputs)
    settings = PerceptionSettings.model_validate(spec["perception"])
    geometry = GroundGeometry.model_validate(spec["geometry"])
    from .navigation_profiles import DEFAULT_BEHAVIOR, validate_behavior

    behavior = validate_behavior(spec.get("behavior", DEFAULT_BEHAVIOR))
    order = context["session"].get("policy", {}).get("order_source", "receipt")
    semantic = profile["backend"] == "semantic"
    capture_evidence = spec.get("capture_evidence", False)
    if type(capture_evidence) is not bool:
        raise ValueError("analysis_navigation_evidence_opt_in")
    if video and capture_evidence:
        raise ValueError("analysis_video_evidence_unsupported")
    if semantic:
        if (
            settings.engine != "segformer"
            or spec["window"] != 1
            or geometry.mode != "image"
        ):
            raise ValueError("analysis_semantic_configuration")
        if (
            settings.model_id
            and settings.model_id != context["profile"]["id"]
            or settings.artifact_sha256
            and settings.artifact_sha256 != context["profile"]["model_sha256"]
        ):
            raise ValueError("analysis_model_identity_mismatch")
    if (
        not video
        and spec["window"] > 1
        and (
            profile["backend"] == "da2"
            or any(
                not adjacent(inputs[i - 1], inputs[i], order)
                for i in range(1, len(inputs))
            )
        )
    ):
        raise ValueError("analysis_window_unverified")
    for name, expected in context["profile"]["files"].items():
        if file_hash(Path(profile["model"]) / name) != expected:
            raise ValueError("analysis_model_hash_mismatch")
    model = None
    rows = []
    row_chunks: list[str] = []
    chunk = []
    metrics = StreamingMetrics("manifest")
    previous_input = None
    images: list = []
    model_load_ms = None
    model_metadata = None
    for index, sample in enumerate(inputs):
        emit(output, {"type": "compute", "done": index, "phase": "decoding"})
        timings = {}
        start = time.perf_counter()
        raw = sample["raw"]
        try:
            payload = payload_for(job["root"], sample)
        except RiderError as exc:
            raise ValueError(exc.code) from exc
        if (
            video
            and previous_input is not None
            and not adjacent(previous_input, sample, "manifest")
        ):
            images.clear()
        previous_input = sample
        image = decode_payload(payload)
        from .model_input import input_box, restore_depth
        from .overlay_geometry import analysis_geometry

        box = input_box(image, settings.input_region)
        model_image = image.crop(box) if box != (0, 0, *image.size) else image
        rgb = np.asarray(model_image, dtype=np.float32) / 255
        mean, std = float(rgb.mean()), float(rgb.std())
        quality = mean >= 0.04 and std >= 0.01
        timings["decode"] = (time.perf_counter() - start) * 1000
        artifacts = []
        layer_metadata = None
        used_window = 0
        if not quality and spec["quality_mode"] == "production":
            assessment = {"state": "unknown", "reason": "vision_image_quality"}
            images.clear()
        else:
            if model is None:
                emit(output, {"type": "compute", "done": index, "phase": "loading"})
                if semantic:
                    from .analysis_semantic import SemanticCandidate

                    model = (semantic_factory or SemanticCandidate)(
                        profile, context["profile"], spec["threads"]
                    )
                    # Native adapters load lazily in detect. Its first total
                    # perception call includes cold loading; do not report the
                    # cheap adapter constructor as measured model load time.
                else:
                    import torch

                    torch.set_num_threads(spec["threads"])
                    torch.set_num_interop_threads(1)
                    start = time.perf_counter()
                    model = (candidate_factory or Candidate)(
                        profile["backend"],
                        profile["model"],
                        "cuda",
                        profile["resolution"],
                    )
                    model_load_ms = (time.perf_counter() - start) * 1000
                model_metadata = model.metadata
            images = [*images, image][-spec["window"] :]
            used_window = len(images)
            emit(output, {"type": "compute", "done": index, "phase": "inference"})
            start = time.perf_counter()
            if semantic:
                from .perception_tuning import has_semantic_tuning, semantic_parameters

                semantic_options = {}
                if behavior["profile_id"] != "legacy" or has_semantic_tuning(
                    settings.model_dump(), context["profile"]["id"]
                ):
                    semantic_options["semantic"] = semantic_parameters(
                        settings.model_dump(),
                        behavior,
                        model_id=context["profile"]["id"],
                    )
                assessment, overlay = cast(SemanticCandidate, model).detect(
                    payload,
                    tuple(spec["corridor"]),
                    **semantic_options,
                    **({"evidence": True} if capture_evidence or video else {}),
                )
                timings["perception_total"] = (time.perf_counter() - start) * 1000
                assessment["overlay_geometry"] = analysis_geometry(
                    image.size, assessment, spec["corridor"]
                )
                if video:
                    name = f"{index:04d}-mask.png"
                    layer, layer_metadata = classification_layer(
                        image.size,
                        assessment,
                        spec["corridor"],
                        settings,
                        name,
                        raw["sha256"],
                        semantic=True,
                        vector=True,
                    )
                    artifacts.append(artifact(name, layer, _PNG_MIME))
                else:
                    artifacts.append(
                        artifact(f"{index:04d}-overlay.jpg", overlay, "image/jpeg")
                    )
            else:
                depth = (
                    cast(Candidate, model).infer(model_image)
                    if spec["window"] == 1
                    else cast(Candidate, model).infer_window(images)
                )
                depth = restore_depth(depth, image.size, box)
                timings["inference"] = (time.perf_counter() - start) * 1000
                emit(output, {"type": "compute", "done": index, "phase": "geometry"})
                start = time.perf_counter()
                if geometry.mode == "image":
                    assessment = assess_depth(
                        depth,
                        tuple(spec["corridor"]),
                        settings,
                        input_bounds=box if settings.input_region is not None else None,
                        **({"evidence": True} if capture_evidence or video else {}),
                    )
                else:
                    from .adaptive_ground import assess

                    evidence_options = {}
                    if capture_evidence:
                        from .navigation_profiles import DEFAULT_BEHAVIOR, parameters

                        evidence_options = {
                            "evidence": parameters(
                                {
                                    **DEFAULT_BEHAVIOR,
                                    "profile_id": "balanced",
                                }
                            )
                        }
                    assessment = assess(
                        depth,
                        tuple(spec["corridor"]),
                        settings,
                        geometry,
                        **evidence_options,
                    )
                assessment["overlay_geometry"] = analysis_geometry(
                    image.size, assessment, spec["corridor"], box
                )
                timings["geometry"] = (time.perf_counter() - start) * 1000
                start = time.perf_counter()
                if not video:
                    overlay = render_depth(
                        image,
                        depth,
                        assessment,
                        spec["corridor"],
                        settings,
                        draw_geometry=False,
                    )
                    stream = io.BytesIO()
                    np.save(stream, depth.astype(np.float32), allow_pickle=False)
                    artifacts.append(
                        artifact(
                            f"{index:04d}-depth.npy",
                            stream.getvalue(),
                            "application/octet-stream",
                        )
                    )
                    low, high = np.quantile(depth[np.isfinite(depth)], [0.01, 0.99])
                    grey = (
                        np.nan_to_num(
                            np.clip((depth - low) / max(float(high - low), 1e-8), 0, 1)
                        )
                        * 255
                    ).astype(np.uint8)
                    stream = io.BytesIO()
                    Image.fromarray(grey).save(stream, format="PNG")
                    artifacts += [
                        artifact(
                            f"{index:04d}-depth.png", stream.getvalue(), _PNG_MIME
                        ),
                        artifact(f"{index:04d}-overlay.jpg", overlay, "image/jpeg"),
                    ]
                else:
                    name = f"{index:04d}-mask.png"
                    layer, layer_metadata = classification_layer(
                        image.size,
                        assessment,
                        spec["corridor"],
                        settings,
                        name,
                        raw["sha256"],
                        vector=True,
                    )
                    artifacts.append(artifact(name, layer, _PNG_MIME))
                timings["render"] = (time.perf_counter() - start) * 1000
        maps = assessment.pop("_navigation_maps", None)
        navigation_evidence = None
        if capture_evidence:
            from .navigation_archive import encode_evidence

            start = time.perf_counter()
            evidence_payload, navigation_evidence = encode_evidence(
                maps, image, rejected=not quality
            )
            name = f"{index:04d}-navigation.npz"
            navigation_evidence["artifact"] = name
            artifacts.append(
                artifact(name, evidence_payload, "application/octet-stream")
            )
            timings["navigation_evidence"] = (time.perf_counter() - start) * 1000
        if video:
            # Private map arrays (including gray/confidence) never enter JSON.
            assessment = {
                key: value
                for key, value in assessment.items()
                if not key.startswith("_")
            }
        row = {
            "sample_id": sample["sample_id"],
            "raw_sha256": raw["sha256"],
            **assessment,
            **(
                {"navigation_evidence": navigation_evidence} if capture_evidence else {}
            ),
            "obstacle_side": side_of(assessment),
            "drive_supported": False,
            "distance_measurement": False,
            "image_quality_passed": quality,
            "image_mean_rgb": mean,
            "image_std_rgb": std,
            "rejected_input": not quality and spec["quality_mode"] == "production",
            "requested_window": spec["window"],
            "actual_window": used_window,
            "causal_warmup": 0 < used_window < spec["window"],
            "timings_ms": timings,
            "artifact_names": [a["name"] for a in artifacts],
        }
        if video:
            row["classification_layer"] = layer_metadata
            row.update(
                {
                    key: sample[key]
                    for key in (
                        "source_index",
                        "source_time_s",
                        "source_received_monotonic_s",
                        "source_received_at",
                        "source_sequence",
                    )
                }
            )
        ack = emit(
            output,
            {"type": "frame", "done": index + 1, "row": row, "artifacts": artifacts},
        )
        row["timings_ms"].update(ack.get("timings_ms", {}))
        if video:
            metrics.add(sample, row)
            chunk.append(row)
            if len(chunk) == 128 or index + 1 == len(inputs):
                name = f"rows-{len(row_chunks):05d}.json"
                result = emit(
                    output, {"type": "rows", "done": index + 1, "rows": chunk}
                )
                if result.get("saved") != name:
                    raise ValueError("analysis_worker_protocol")
                row_chunks.append(name)
                chunk = []
        else:
            rows.append(row)
    import torch

    if video:
        inputs.verify()
        summary = metrics.finish()
    else:
        summary = summarize(inputs, rows, order)
    report = {
        "schema_version": 1,
        "kind": "rider-offline-analysis",
        "run_id": job["id"],
        "input_digest": context["input_digest"],
        "context": context,
        "summary": summary,
        "rows": rows,
        "model": model_metadata,
        "model_load_ms": model_load_ms,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "peak_vram_mib": (
            torch.cuda.max_memory_allocated() / 1024**2
            if torch.cuda.is_initialized()
            else 0
        ),
        "device": "cuda",
        "drive_supported": False,
        "distance_measurement": False,
    }
    if video:
        report.update(
            input_kind="video",
            source=context["input_source"],
            row_chunks=row_chunks,
            classification_scope=(
                "per-frame model classification, not closed-loop robot verdict"
            ),
        )
    emit(output, {"type": "complete", "report": report})


def deny_network():
    """An offline adapter cannot contact the Pi, Hub or any internet endpoint."""
    import socket

    class OfflineSocket(socket.socket):
        def connect(self, address):
            if self.family in {socket.AF_INET, socket.AF_INET6}:
                raise RuntimeError("analysis_network_denied")
            return super().connect(address)

        def connect_ex(self, address):
            if self.family in {socket.AF_INET, socket.AF_INET6}:
                raise RuntimeError("analysis_network_denied")
            return super().connect_ex(address)

        def sendto(self, *args):
            if self.family in {socket.AF_INET, socket.AF_INET6}:
                raise RuntimeError("analysis_network_denied")
            return super().sendto(*args)

    socket.__dict__["socket"] = OfflineSocket


def main():
    output = sys.stdout.buffer
    try:
        process_limits(int(sys.argv[1]))
        deny_network()
        job = receive(sys.stdin.buffer)
        # Protocol stdout must not contain SDK progress or model diagnostics.
        with contextlib.redirect_stdout(sys.stderr):
            run(job, output)
    except Exception as error:
        # Do not expose local paths or environment in public errors.
        code = str(error)
        if not code.startswith(("analysis_", "samples_", "vision_")) or len(code) > 96:
            code = "analysis_worker_failed"
        try:
            emit(output, {"type": "error", "error": code})
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
