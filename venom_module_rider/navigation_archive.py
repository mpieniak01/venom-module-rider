"""Bounded compact navigation evidence, independent of models and transports."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .secure_output import (
    exclusive_file,
    new_directory,
    output_directory,
    verify_directory,
)

MAX_EVIDENCE_BYTES = 4 * 1024**2
MAX_EXPORT_BYTES = 256 * 1024**2
_GEOMETRY = (
    "geometry_guard_clear",
    "geometry_collision",
    "geometry_blocked",
    "geometry_complete",
)
_ARRAYS = {
    "floor",
    "obstacle",
    "uncertain",
    "labels",
    "confidence",
    "gray",
    "critical",
    "negative_support",
    "corridor",
    "valid",
    "input_bounds",
    *_GEOMETRY,
}


def _boolean(value: Any) -> bool:
    import numpy as np

    if type(value) is bool:
        return value
    if isinstance(value, np.ndarray) and value.shape == () and value.dtype == np.bool_:
        return bool(value.item())
    raise ValueError("analysis_navigation_evidence_boolean")


def _validated(maps: Mapping[str, Any]) -> dict[str, Any]:
    """Validate bounded navigation evidence and optional original input bounds."""
    import numpy as np

    gray = maps.get("gray")
    if (
        not isinstance(gray, np.ndarray)
        or gray.ndim != 2
        or gray.dtype != np.uint8
        or min(gray.shape) < 1
        or gray.shape[0] > 720
        or gray.shape[1] > 1280
    ):
        raise ValueError("analysis_navigation_evidence_shape")
    shape = gray.shape
    result: dict[str, Any] = {"gray": gray, "valid": _boolean(maps.get("valid"))}
    _validated_masks(maps, shape, result)
    _validated_geometry(maps, shape, result)
    return result


def encode_evidence(maps: Any, image: Any, *, rejected: bool = False):
    """Persist source scores/masks and gray, never full class logits or previews."""
    import numpy as np

    from .navigation_maps import source_gray

    gray = source_gray(image)
    if rejected or maps is None:
        empty = np.zeros(gray.shape, dtype=bool)
        maps = {
            "floor": empty,
            "obstacle": empty.copy(),
            "uncertain": ~empty,
            "valid": False,
        }
    values = _validated({**maps, "gray": gray})
    stream = io.BytesIO()
    np.savez_compressed(stream, **values)
    payload = stream.getvalue()
    if not 0 < len(payload) <= MAX_EVIDENCE_BYTES:
        raise ValueError("analysis_navigation_evidence_size")
    height, width = gray.shape
    return payload, {
        "version": 1,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "width": width,
        "height": height,
        "valid": values["valid"],
    }


def decode_evidence(payload: bytes) -> dict[str, Any]:
    """Validate the archive before trusting a flat-file export or replay input."""
    import numpy as np

    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_EVIDENCE_BYTES:
        raise ValueError("analysis_navigation_evidence_size")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if (
                not 1 <= len(entries) <= len(_ARRAYS)
                or len(set(names)) != len(names)
                or any(name not in {key + ".npy" for key in _ARRAYS} for name in names)
                or any(item.file_size > MAX_EVIDENCE_BYTES for item in entries)
                or sum(item.file_size for item in entries) > 12 * 1024**2
            ):
                raise ValueError("analysis_navigation_evidence_size")
        with np.load(io.BytesIO(payload), allow_pickle=False) as data:
            values = {key: data[key] for key in data.files}
        return _validated(values)
    except (zipfile.BadZipFile, EOFError, OSError, TypeError) as exc:
        raise ValueError("analysis_navigation_evidence_format") from exc


def _read_regular_file(path: Path, maximum: int, error: str) -> bytes:
    if path.is_symlink():
        raise ValueError(error)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(error) from exc
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError(error)
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError(error)
    return payload


def _json_file(path: Path, maximum: int):
    payload = _read_regular_file(path, maximum, "analysis_navigation_export_source")
    return json.loads(payload), hashlib.sha256(payload).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _sequence(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ValueError("analysis_navigation_export_identity")
    return value


def _sha(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch("[a-f0-9]{64}", value) is None:
        raise ValueError("analysis_navigation_export_hash")
    return value


def _timeline_index(timeline):
    if not isinstance(timeline, list) or not 1 <= len(timeline) <= 10000:
        raise ValueError("analysis_navigation_export_timeline")
    result: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in timeline:
        if not isinstance(row, dict):
            raise ValueError("analysis_navigation_export_timeline")
        digest = _sha(row.get("raw_sha256", row.get("sha256")))
        sequence = _sequence(row.get("sequence", row.get("source_sequence")))
        captured = row.get("captured_s", row.get("source_s"))
        if (
            isinstance(captured, bool)
            or not isinstance(captured, (int, float))
            or not math.isfinite(captured)
        ):
            raise ValueError("analysis_navigation_export_source_time")
        result.setdefault((digest, sequence), []).append(
            {**row, "captured_s": captured}
        )
    return result


def _source(sample, index):
    digest = _sha(sample["raw"]["sha256"])
    source_context = sample.get("source_context") or {}
    if not isinstance(source_context, dict):
        raise ValueError("analysis_navigation_export_identity")
    identity = source_context.get("source_identity") or {}
    if not isinstance(identity, dict):
        raise ValueError("analysis_navigation_export_identity")
    raw_sequence = sample.get("sequence")
    sequence = _sequence(
        raw_sequence if raw_sequence is not None else identity.get("sequence")
    )
    matches = index.get((digest, sequence), [])
    if len(matches) != 1:
        raise ValueError("analysis_navigation_export_timeline_match")
    source = matches[0]
    frozen_frame = sample.get("frame_id") or identity.get("frame_id")
    frame = source.get("frame_id", frozen_frame)
    if (
        not isinstance(frame, str)
        or not frame
        or len(frame) > 128
        or (frozen_frame and frame != frozen_frame)
    ):
        raise ValueError("analysis_navigation_export_identity")
    if identity and (
        identity.get("sha256") != digest
        or type(identity.get("sequence")) is not int
        or identity.get("sequence") != sequence
        or identity.get("frame_id") != frame
    ):
        raise ValueError("analysis_navigation_export_identity")
    return {
        **source,
        "frame_id": frame,
        "sequence": sequence,
        "source_sha256": digest,
        "source_identity": identity
        or {
            "frame_id": frame,
            "sequence": sequence,
            "sha256": digest,
        },
        "source_context": source_context,
        "identity_provenance": (
            "explicit_timeline_and_frozen_input"
            if "frame_id" in source
            else "sha_sequence_join_to_frozen_input"
        ),
    }


def _artifact(directory: Path, evidence: Mapping[str, Any]) -> Path:
    name = evidence.get("artifact")
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}", name) is None
        or ".." in name
        or not name.endswith(".npz")
    ):
        raise ValueError("analysis_navigation_export_artifact")
    path = directory / name
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_EVIDENCE_BYTES
    ):
        raise ValueError("analysis_navigation_export_artifact")
    _sha(evidence.get("sha256"))
    return path


def export_evidence(
    report_path: Path,
    artifacts: Path,
    timeline_path: Path,
    output: Path,
    *,
    max_mib: float = 128,
) -> dict[str, Any]:
    """Export a validated prefix under a hard byte cap; no models or network."""
    if (
        isinstance(max_mib, bool)
        or not isinstance(max_mib, (int, float))
        or not math.isfinite(max_mib)
        or not 0 < max_mib <= 256
    ):
        raise ValueError("analysis_navigation_export_budget")
    maximum = int(max_mib * 1024**2)
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("analysis_navigation_export_output")
    if artifacts.is_symlink() or not artifacts.is_dir():
        raise ValueError("analysis_navigation_export_artifact")
    report, report_sha = _json_file(report_path, 8 * 1024**2)
    timeline, timeline_sha = _json_file(timeline_path, 16 * 1024**2)
    if not isinstance(report, dict) or not isinstance(report.get("context"), dict):
        raise ValueError("analysis_navigation_export_report")
    context = report["context"]
    rows, inputs = report.get("rows"), context.get("inputs")
    if (
        not isinstance(rows, list)
        or not 1 <= len(rows) <= 1000
        or not isinstance(inputs, list)
        or len(rows) != len(inputs)
        or report.get("input_digest") != context.get("input_digest")
    ):
        raise ValueError("analysis_navigation_export_report")
    index = _timeline_index(timeline)
    prepared = []
    fingerprint = context.get("profile", {}).get("fingerprint") or _digest(
        context.get("profile", {})
    )
    previous = None
    for row, sample in zip(rows, inputs, strict=True):
        item, line, path, evidence = _prepare_export_item(
            row, sample, index, artifacts, context, fingerprint, previous
        )
        prepared.append((item, line.encode(), path, evidence))
        previous = item
    provenance = {
        key: context.get(key)
        for key in (
            "schema_version",
            "kind",
            "session_id",
            "dataset_revision",
            "input_digest",
            "spec",
            "profile",
            "code",
        )
    }
    summary = {
        "version": 1,
        "status": "complete",
        "reason": None,
        "requested_frames": len(rows),
        "exported_frames": 0,
        "skipped_frames": 0,
        "max_bytes": maximum,
        "artifact_bytes": 0,
        "report_sha256": report_sha,
        "timeline_sha256": timeline_sha,
        "context_sha256": _digest(context),
        "context": provenance,
        "qualified": False,
        "ground_truth": "not_inferred",
        "source_time": "explicit_source_timeline_only",
        "identity_provenance": sorted(
            {item[0]["identity_provenance"] for item in prepared}
        ),
    }
    # Reserve enough space for the complete immutable provenance and final status.
    reserve = len(json.dumps(summary, indent=2, allow_nan=False).encode()) + 8192
    if reserve > maximum:
        raise ValueError("analysis_navigation_export_budget")
    with (
        output_directory(output.parent) as parent,
        new_directory(parent, output.name) as directory,
        new_directory(directory, "maps") as maps_directory,
    ):
        _write_export(directory, maps_directory, prepared, summary, maximum, reserve)
        verify_directory(output, directory)
    return summary


def _write_export(directory, maps_directory, prepared, summary, maximum, reserve):
    """Write validated artifacts and their byte-exact manifest to pinned handles."""
    manifest_digest = hashlib.sha256()
    used = 0
    copied: set[str] = set()
    try:
        with exclusive_file(directory, "manifest.jsonl") as manifest:
            for _item, line_bytes, path, evidence in prepared:
                payload, digest = _validated_export_payload(path, evidence)
                additional = len(payload) if digest not in copied else 0
                if used + additional + len(line_bytes) + reserve > maximum:
                    summary.update(status="partial", reason="byte_limit")
                    break
                if digest not in copied:
                    with exclusive_file(maps_directory, digest + ".npz") as target:
                        target.write(payload)
                    copied.add(digest)
                    summary["artifact_bytes"] += len(payload)
                manifest.write(line_bytes)
                manifest_digest.update(line_bytes)
                used += additional + len(line_bytes)
                summary["exported_frames"] += 1
    except (ValueError, OSError) as exc:
        summary.update(status="failed", reason="invalid_or_unreadable_evidence")
        summary["skipped_frames"] = (
            summary["requested_frames"] - summary["exported_frames"]
        )
        with exclusive_file(directory, "EXPORT.json", binary=False) as stream:
            stream.write(json.dumps(summary, indent=2) + "\n")
        raise ValueError("analysis_navigation_export_failed") from exc
    summary["skipped_frames"] = summary["requested_frames"] - summary["exported_frames"]
    summary["manifest_sha256"] = manifest_digest.hexdigest()
    summary["total_bytes"] = used
    while True:
        encoded_summary = (
            json.dumps(summary, indent=2, allow_nan=False) + "\n"
        ).encode()
        total = used + len(encoded_summary)
        if total == summary["total_bytes"]:
            break
        summary["total_bytes"] = total
    if total > maximum:
        raise ValueError("analysis_navigation_export_budget")
    with exclusive_file(directory, "EXPORT.json") as stream:
        stream.write(encoded_summary)


def _validated_export_payload(path, evidence):
    """Bind exported bytes and metadata to the recorded artifact digest."""
    payload = _read_regular_file(
        path,
        MAX_EVIDENCE_BYTES,
        "analysis_navigation_export_artifact",
    )
    digest = hashlib.sha256(payload).hexdigest()
    if digest != evidence["sha256"]:
        raise ValueError("analysis_navigation_export_hash")
    values = decode_evidence(payload)
    height, width = values["gray"].shape
    if (
        type(evidence.get("width")) is not int
        or evidence["width"] != width
        or type(evidence.get("height")) is not int
        or evidence["height"] != height
        or type(evidence.get("valid")) is not bool
        or evidence["valid"] != values["valid"]
    ):
        raise ValueError("analysis_navigation_export_evidence")
    return payload, digest


def _validated_masks(maps, shape, result):
    """Validate semantic pairs or a complete disjoint depth partition."""
    import numpy as np

    from .spatial_evidence import semantic_masks

    if "labels" in maps or "confidence" in maps:
        if not {"labels", "confidence"}.issubset(maps):
            raise ValueError("analysis_navigation_evidence_pair")
        semantic_masks(maps["labels"], maps["confidence"], 0.8)
        if maps["labels"].shape != shape:
            raise ValueError("analysis_navigation_evidence_shape")
        result.update(labels=maps["labels"], confidence=maps["confidence"])
    else:
        masks: list[Any] = [maps.get(key) for key in ("floor", "obstacle", "uncertain")]
        if any(
            not isinstance(mask, np.ndarray)
            or mask.dtype != np.bool_
            or mask.shape != shape
            for mask in masks
        ):
            raise ValueError("analysis_navigation_evidence_masks")
        floor, obstacle, uncertain = masks
        if (
            (floor & obstacle).any()
            or (floor & uncertain).any()
            or (obstacle & uncertain).any()
            or not (floor | obstacle | uncertain).all()
        ):
            raise ValueError("analysis_navigation_evidence_partition")
        result.update(floor=floor, obstacle=obstacle, uncertain=uncertain)


def _validated_geometry(maps, shape, result):
    """Validate optional aligned masks, input bounds and geometry flags."""
    import numpy as np

    for key in ("critical", "negative_support", "corridor"):
        if key in maps:
            value = maps[key]
            if (
                not isinstance(value, np.ndarray)
                or value.dtype != np.bool_
                or value.shape != shape
            ):
                raise ValueError("analysis_navigation_evidence_masks")
            result[key] = value
    _validated_input_bounds(maps, shape, result)
    for key in _GEOMETRY:
        if key in maps:
            result[key] = _boolean(maps[key])


def _prepare_export_item(row, sample, index, artifacts, context, fingerprint, previous):
    """Validate one immutable replay item before adding it to the export prefix."""
    if (
        row.get("sample_id") != sample.get("sample_id")
        or row.get("raw_sha256") != sample.get("raw", {}).get("sha256")
        or row.get("state") not in {"clear", "obstacle", "unknown"}
    ):
        raise ValueError("analysis_navigation_export_report")
    source = _source(sample, index)
    evidence = row.get("navigation_evidence")
    if (
        not isinstance(evidence, dict)
        or type(evidence.get("version")) is not int
        or evidence["version"] != 1
    ):
        raise ValueError("analysis_navigation_export_evidence_missing")
    path = _artifact(artifacts, evidence)
    identity = source["source_identity"]
    replay_context = {
        "source_session": identity.get("camera_session")
        or source["source_context"].get("source_session")
        or context.get("session_id"),
        "source_device_id": sample.get("source_device_id"),
        "transport_epoch": identity.get("transport_epoch"),
        "pipeline_revision": identity.get("pipeline_revision"),
        "model_id": (
            "depth-anything-v2-indoor-small"
            if context.get("profile", {}).get("id") == "da2-default"
            else context.get("profile", {}).get("id")
        ),
        "model_fingerprint": fingerprint,
        "model_sha256": context.get("profile", {}).get("model_sha256"),
        "code": context.get("code"),
        "spec_sha256": _digest(context.get("spec")),
        "dataset_revision": context.get("dataset_revision"),
    }
    if previous and previous["context"] == replay_context:
        if source["captured_s"] <= previous["captured_s"]:
            raise ValueError("analysis_navigation_export_source_time")
    item = {
        "frame_id": source["frame_id"],
        "sequence": source["sequence"],
        "captured_s": source["captured_s"],
        "source_sha256": source["source_sha256"],
        "source_identity": identity,
        "source_context": source["source_context"],
        "source_time_provenance": "explicit_source_timeline",
        "identity_provenance": source["identity_provenance"],
        "context": replay_context,
        "valid": evidence.get("valid"),
        "reason": row.get("reason"),
        "raw_reason": row.get("reason"),
        "raw_state": row["state"],
        "corridor": context["spec"]["corridor"],
        "maps": "maps/" + evidence["sha256"] + ".npz",
        "source_gap": bool(
            previous
            and previous["context"] == replay_context
            and source["sequence"] != previous["sequence"] + 1
        )
        or source.get("source_gap") is True,
    }
    line = json.dumps(item, separators=(",", ":"), allow_nan=False) + "\n"
    if len(line.encode()) > 64 * 1024:
        raise ValueError("analysis_navigation_export_manifest_size")
    return item, line, path, evidence


def _validated_input_bounds(maps, shape, result):
    """Validate integer input bounds after optional mask alignment."""
    import numpy as np

    if "input_bounds" in maps:
        value = np.asarray(maps["input_bounds"])
        if value.shape != (4,) or value.dtype.kind not in "iu":
            raise ValueError("analysis_navigation_evidence_input_bounds")
        a, b, c, d = value.tolist()
        if not 0 <= a < c <= shape[1] or not 0 <= b < d <= shape[0]:
            raise ValueError("analysis_navigation_evidence_input_bounds")
        result["input_bounds"] = value.astype(np.int16)
