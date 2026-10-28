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
from typing import Any, cast

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
    state = _AnalysisState()
    state.job = job
    state.output = output
    state.candidate_factory = candidate_factory
    state.semantic_factory = semantic_factory
    from .cuda_execution import require_cuda

    require_cuda()
    emit(state.output, {"type": "compute", "done": 0, "phase": "loading"})
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

    state.context, state.profile = (state.job["context"], state.job["profile"])
    state.spec = state.context["spec"]
    state.inputs = inputs_for(state.job["root"], state.context)
    state.video = isinstance(state.inputs, VideoInputs)
    state.settings = PerceptionSettings.model_validate(state.spec["perception"])
    state.geometry = GroundGeometry.model_validate(state.spec["geometry"])
    from .navigation_profiles import DEFAULT_BEHAVIOR, validate_behavior

    # Retain original dependency initialization before validating the job.
    _ = (
        Candidate,
        DEFAULT_BEHAVIOR,
        GroundGeometry,
        Image,
        PerceptionSettings,
        RiderError,
        StreamingMetrics,
        VideoInputs,
        adjacent,
        assess_depth,
        classification_layer,
        decode_payload,
        file_hash,
        inputs_for,
        np,
        payload_for,
        render_depth,
        summarize,
        validate_behavior,
    )
    state.behavior = validate_behavior(state.spec.get("behavior", DEFAULT_BEHAVIOR))
    state.order = (
        state.context["session"].get("policy", {}).get("order_source", "receipt")
    )
    state.semantic = state.profile["backend"] == "semantic"
    state.capture_evidence = state.spec.get("capture_evidence", False)
    if type(state.capture_evidence) is not bool:
        raise ValueError("analysis_navigation_evidence_opt_in")
    if state.video and state.capture_evidence:
        raise ValueError("analysis_video_evidence_unsupported")
    _validate_worker_job(state)
    state.model = None
    state.rows = []
    state.row_chunks = []
    state.chunk = []
    state.metrics = StreamingMetrics("manifest")
    state.previous_input = None
    state.images = []
    state.model_load_ms = None
    state.model_metadata = None
    for index, sample in enumerate(state.inputs):
        state.index, state.sample = index, sample
        _worker_frame(state)
    _finish_worker_report(state)


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


class _AnalysisState:
    """One synchronous offline run; each frame replaces only its own
    transient fields.
    """

    ack: Any
    artifacts: Any
    assessment: Any
    behavior: Any
    box: Any
    candidate_factory: Any
    capture_evidence: Any
    chunk: Any
    context: Any
    depth: Any
    geometry: Any
    image: Any
    images: Any
    index: Any
    inputs: Any
    job: Any
    layer_metadata: Any
    maps: Any
    mean: Any
    metrics: Any
    model: Any
    model_image: Any
    model_load_ms: Any
    model_metadata: Any
    navigation_evidence: Any
    order: Any
    output: Any
    overlay: Any
    payload: Any
    previous_input: Any
    profile: Any
    quality: Any
    raw: Any
    report: Any
    rgb: Any
    row: Any
    row_chunks: Any
    rows: Any
    sample: Any
    semantic: Any
    semantic_factory: Any
    settings: Any
    spec: Any
    start: Any
    std: Any
    timings: Any
    used_window: Any
    video: Any


def _validate_worker_job(state: _AnalysisState):
    """Validate model identity and temporal-window continuity before loading a model."""
    from .analysis_metrics import adjacent
    from .depth_candidates import file_hash

    _validate_worker_semantic(state)
    if (
        not state.video
        and state.spec["window"] > 1
        and (
            state.profile["backend"] == "da2"
            or any(
                not adjacent(state.inputs[i - 1], state.inputs[i], state.order)
                for i in range(1, len(state.inputs))
            )
        )
    ):
        raise ValueError("analysis_window_unverified")
    for name, expected in state.context["profile"]["files"].items():
        if file_hash(Path(state.profile["model"]) / name) != expected:
            raise ValueError("analysis_model_hash_mismatch")


def _worker_frame(state: _AnalysisState):
    """Process one exact input through decoding, assessment and ordered
    worker acknowledgements.
    """
    import numpy as np

    from .analysis_inputs import payload_for
    from .analysis_metrics import adjacent
    from .depth_detector import (
        decode_payload,
    )
    from .errors import RiderError

    emit(state.output, {"type": "compute", "done": state.index, "phase": "decoding"})
    state.timings = {}
    state.start = time.perf_counter()
    state.raw = state.sample["raw"]
    try:
        state.payload = payload_for(state.job["root"], state.sample)
    except RiderError as exc:
        raise ValueError(exc.code) from exc
    if (
        state.video
        and state.previous_input is not None
        and (not adjacent(state.previous_input, state.sample, "manifest"))
    ):
        state.images.clear()
    state.previous_input = state.sample
    state.image = decode_payload(state.payload)
    from .model_input import input_box

    state.box = input_box(state.image, state.settings.input_region)
    state.model_image = (
        state.image.crop(state.box)
        if state.box != (0, 0, *state.image.size)
        else state.image
    )
    state.rgb = np.asarray(state.model_image, dtype=np.float32) / 255
    state.mean, state.std = (float(state.rgb.mean()), float(state.rgb.std()))
    state.quality = state.mean >= 0.04 and state.std >= 0.01
    state.timings["decode"] = (time.perf_counter() - state.start) * 1000
    state.artifacts = []
    state.layer_metadata = None
    state.used_window = 0
    if not state.quality and state.spec["quality_mode"] == "production":
        state.assessment = {"state": "unknown", "reason": "vision_image_quality"}
        state.images.clear()
    else:
        _assess_worker_frame(state)
    _finish_worker_frame(state)


def _finish_worker_report(state: _AnalysisState):
    """Finalize metrics after all frame acknowledgements and source verification."""
    import torch

    from .analysis_metrics import summarize

    if state.video:
        state.inputs.verify()
        summary = state.metrics.finish()
    else:
        summary = summarize(state.inputs, state.rows, state.order)
    state.report = {
        "schema_version": 1,
        "kind": "rider-offline-analysis",
        "run_id": state.job["id"],
        "input_digest": state.context["input_digest"],
        "context": state.context,
        "summary": summary,
        "rows": state.rows,
        "model": state.model_metadata,
        "model_load_ms": state.model_load_ms,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "peak_vram_mib": torch.cuda.max_memory_allocated() / 1024**2
        if torch.cuda.is_initialized()
        else 0,
        "device": "cuda",
        "drive_supported": False,
        "distance_measurement": False,
    }
    if state.video:
        state.report.update(
            input_kind="video",
            source=state.context["input_source"],
            row_chunks=state.row_chunks,
            classification_scope=(
                "per-frame model classification, not closed-loop robot verdict"
            ),
        )
    emit(state.output, {"type": "complete", "report": state.report})


def _assess_worker_frame(state: _AnalysisState):
    """Assess one accepted input with the retained model and current causal window."""
    if state.model is None:
        _load_worker_model(state)
    state.images = [*state.images, state.image][-state.spec["window"] :]
    state.used_window = len(state.images)
    emit(state.output, {"type": "compute", "done": state.index, "phase": "inference"})
    state.start = time.perf_counter()
    if state.semantic:
        _semantic_worker_frame(state)
    else:
        _depth_worker_frame(state)


def _load_worker_model(state: _AnalysisState):
    """Load exactly one semantic or CUDA depth adapter and preserve cold-load timing."""
    from .depth_candidates import Candidate

    emit(state.output, {"type": "compute", "done": state.index, "phase": "loading"})
    if state.semantic:
        from .analysis_semantic import SemanticCandidate

        state.model = (state.semantic_factory or SemanticCandidate)(
            state.profile, state.context["profile"], state.spec["threads"]
        )
    else:
        import torch

        torch.set_num_threads(state.spec["threads"])
        torch.set_num_interop_threads(1)
        state.start = time.perf_counter()
        state.model = (state.candidate_factory or Candidate)(
            state.profile["backend"],
            state.profile["model"],
            "cuda",
            state.profile["resolution"],
        )
        state.model_load_ms = (time.perf_counter() - state.start) * 1000
    state.model_metadata = state.model.metadata


def _semantic_worker_frame(state: _AnalysisState):
    """Apply frozen semantic tuning and retain the matching layer or overlay."""
    from .analysis_layers import classification_layer
    from .analysis_semantic import SemanticCandidate
    from .overlay_geometry import analysis_geometry
    from .perception_tuning import has_semantic_tuning, semantic_parameters

    semantic_options = {}
    if state.behavior["profile_id"] != "legacy" or has_semantic_tuning(
        state.settings.model_dump(), state.context["profile"]["id"]
    ):
        semantic_options["semantic"] = semantic_parameters(
            state.settings.model_dump(),
            state.behavior,
            model_id=state.context["profile"]["id"],
        )
    state.assessment, state.overlay = cast(SemanticCandidate, state.model).detect(
        state.payload,
        tuple(state.spec["corridor"]),
        **semantic_options,
        **{"evidence": True} if state.capture_evidence or state.video else {},
    )
    state.timings["perception_total"] = (time.perf_counter() - state.start) * 1000
    state.assessment["overlay_geometry"] = analysis_geometry(
        state.image.size, state.assessment, state.spec["corridor"]
    )
    if state.video:
        name = f"{state.index:04d}-mask.png"
        layer, state.layer_metadata = classification_layer(
            state.image.size,
            state.assessment,
            state.spec["corridor"],
            state.settings,
            name,
            state.raw["sha256"],
            semantic=True,
            vector=True,
        )
        state.artifacts.append(artifact(name, layer, _PNG_MIME))
    else:
        state.artifacts.append(
            artifact(f"{state.index:04d}-overlay.jpg", state.overlay, "image/jpeg")
        )


def _depth_worker_frame(state: _AnalysisState):
    """Infer source-aligned depth before geometry and rendering timings."""
    from .depth_candidates import Candidate
    from .model_input import restore_depth
    from .overlay_geometry import analysis_geometry

    state.depth = (
        cast(Candidate, state.model).infer(state.model_image)
        if state.spec["window"] == 1
        else cast(Candidate, state.model).infer_window(state.images)
    )
    state.depth = restore_depth(state.depth, state.image.size, state.box)
    state.timings["inference"] = (time.perf_counter() - state.start) * 1000
    emit(state.output, {"type": "compute", "done": state.index, "phase": "geometry"})
    state.start = time.perf_counter()
    _worker_depth_geometry(state)
    state.assessment["overlay_geometry"] = analysis_geometry(
        state.image.size, state.assessment, state.spec["corridor"], state.box
    )
    state.timings["geometry"] = (time.perf_counter() - state.start) * 1000
    state.start = time.perf_counter()
    _worker_depth_artifacts(state)
    state.timings["render"] = (time.perf_counter() - state.start) * 1000


def _worker_depth_geometry(state: _AnalysisState):
    """Preserve image versus ground assessment and explicit evidence options."""
    from .depth_detector import (
        assess_depth,
    )

    if state.geometry.mode == "image":
        state.assessment = assess_depth(
            state.depth,
            tuple(state.spec["corridor"]),
            state.settings,
            input_bounds=state.box if state.settings.input_region is not None else None,
            **{"evidence": True} if state.capture_evidence or state.video else {},
        )
    else:
        from .adaptive_ground import assess

        evidence_options = {}
        if state.capture_evidence:
            from .navigation_profiles import DEFAULT_BEHAVIOR, parameters

            evidence_options = {
                "evidence": parameters({**DEFAULT_BEHAVIOR, "profile_id": "balanced"})
            }
        state.assessment = assess(
            state.depth,
            tuple(state.spec["corridor"]),
            state.settings,
            state.geometry,
            **evidence_options,
        )


def _worker_depth_artifacts(state: _AnalysisState):
    """Retain depth arrays and overlays only for images, compact masks for video."""
    import numpy as np
    from PIL import Image

    from .analysis_layers import classification_layer
    from .depth_detector import (
        render_depth,
    )

    if not state.video:
        state.overlay = render_depth(
            state.image,
            state.depth,
            state.assessment,
            state.spec["corridor"],
            state.settings,
            draw_geometry=False,
        )
        stream = io.BytesIO()
        np.save(stream, state.depth.astype(np.float32), allow_pickle=False)
        state.artifacts.append(
            artifact(
                f"{state.index:04d}-depth.npy",
                stream.getvalue(),
                "application/octet-stream",
            )
        )
        low, high = np.quantile(state.depth[np.isfinite(state.depth)], [0.01, 0.99])
        grey = (
            np.nan_to_num(
                np.clip((state.depth - low) / max(float(high - low), 1e-08), 0, 1)
            )
            * 255
        ).astype(np.uint8)
        stream = io.BytesIO()
        Image.fromarray(grey).save(stream, format="PNG")
        state.artifacts += [
            artifact(f"{state.index:04d}-depth.png", stream.getvalue(), _PNG_MIME),
            artifact(f"{state.index:04d}-overlay.jpg", state.overlay, "image/jpeg"),
        ]
    else:
        name = f"{state.index:04d}-mask.png"
        layer, state.layer_metadata = classification_layer(
            state.image.size,
            state.assessment,
            state.spec["corridor"],
            state.settings,
            name,
            state.raw["sha256"],
            vector=True,
        )
        state.artifacts.append(artifact(name, layer, _PNG_MIME))


def _finish_worker_frame(state: _AnalysisState):
    """Strip private maps and acknowledge frame output before accumulating metrics."""
    state.maps = state.assessment.pop("_navigation_maps", None)
    state.navigation_evidence = None
    _worker_navigation_evidence(state)
    if state.video:
        state.assessment = {
            key: value
            for key, value in state.assessment.items()
            if not key.startswith("_")
        }
    state.row = {
        "sample_id": state.sample["sample_id"],
        "raw_sha256": state.raw["sha256"],
        **state.assessment,
        **(
            {"navigation_evidence": state.navigation_evidence}
            if state.capture_evidence
            else {}
        ),
        "obstacle_side": side_of(state.assessment),
        "drive_supported": False,
        "distance_measurement": False,
        "image_quality_passed": state.quality,
        "image_mean_rgb": state.mean,
        "image_std_rgb": state.std,
        "rejected_input": not state.quality
        and state.spec["quality_mode"] == "production",
        "requested_window": state.spec["window"],
        "actual_window": state.used_window,
        "causal_warmup": 0 < state.used_window < state.spec["window"],
        "timings_ms": state.timings,
        "artifact_names": [a["name"] for a in state.artifacts],
    }
    if state.video:
        state.row["classification_layer"] = state.layer_metadata
        state.row.update(
            {
                key: state.sample[key]
                for key in (
                    "source_index",
                    "source_time_s",
                    "source_received_monotonic_s",
                    "source_received_at",
                    "source_sequence",
                )
            }
        )
    state.ack = emit(
        state.output,
        {
            "type": "frame",
            "done": state.index + 1,
            "row": state.row,
            "artifacts": state.artifacts,
        },
    )
    state.row["timings_ms"].update(state.ack.get("timings_ms", {}))
    _retain_worker_row(state)


def _worker_navigation_evidence(state: _AnalysisState):
    """Encode only explicitly requested navigation evidence and retain its timing."""
    if state.capture_evidence:
        from .navigation_archive import encode_evidence

        state.start = time.perf_counter()
        evidence_payload, state.navigation_evidence = encode_evidence(
            state.maps, state.image, rejected=not state.quality
        )
        name = f"{state.index:04d}-navigation.npz"
        state.navigation_evidence["artifact"] = name
        state.artifacts.append(
            artifact(name, evidence_payload, "application/octet-stream")
        )
        state.timings["navigation_evidence"] = (
            time.perf_counter() - state.start
        ) * 1000


def _retain_worker_row(state: _AnalysisState):
    """Preserve per-video metrics and bounded row chunks after the frame ACK."""
    if state.video:
        state.metrics.add(state.sample, state.row)
        state.chunk.append(state.row)
        if len(state.chunk) == 128 or state.index + 1 == len(state.inputs):
            name = f"rows-{len(state.row_chunks):05d}.json"
            result = emit(
                state.output,
                {"type": "rows", "done": state.index + 1, "rows": state.chunk},
            )
            if result.get("saved") != name:
                raise ValueError("analysis_worker_protocol")
            state.row_chunks.append(name)
            state.chunk = []
    else:
        state.rows.append(state.row)


def _validate_worker_semantic(state: _AnalysisState):
    """Reject incompatible semantic geometry and frozen model identity."""
    if state.semantic:
        if (
            state.settings.engine != "segformer"
            or state.spec["window"] != 1
            or state.geometry.mode != "image"
        ):
            raise ValueError("analysis_semantic_configuration")
        if (
            state.settings.model_id
            and state.settings.model_id != state.context["profile"]["id"]
            or (
                state.settings.artifact_sha256
                and state.settings.artifact_sha256
                != state.context["profile"]["model_sha256"]
            )
        ):
            raise ValueError("analysis_model_identity_mismatch")


if __name__ == "__main__":
    raise SystemExit(main())
