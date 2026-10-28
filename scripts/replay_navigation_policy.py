"""Offline replay of source-resolution compact masks, never camera/actuation.

Manifest JSONL rows: frame_id, sequence, captured_s, context, maps (relative NPZ
path). NPZ contains boolean floor/obstacle/uncertain or source uint8 labels
and float16/float32 confidence (rethresholded using pixel_confidence); critical
and uint8 gray are optional. An explicit raw_state can preserve the original model
assessment. Without it, the strict map baseline is labelled reconstructed.
"""

import argparse
import io
import json
from collections import Counter
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from venom_module_rider.navigation_maps import assess_evidence
from venom_module_rider.temporal_evidence import TemporalEvidence


def _asset(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("replay_relative_maps")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("replay_maps_path")
    if path.suffix != ".npz":
        raise ValueError("replay_maps_format")
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("replay_maps_bounds")
    return path


def _read_maps(path: Path) -> tuple[dict[str, Any], str]:
    import numpy as np

    from venom_module_rider.navigation_archive import _read_regular_file

    payload = _read_regular_file(path, 32 * 1024 * 1024, "replay_maps_path")
    # Check and load the same immutable bytes; never reopen a replaceable path.
    import zipfile

    with zipfile.ZipFile(io.BytesIO(payload)) as members:
        entries = members.infolist()
        if (
            len(entries) > 16
            or sum(item.file_size for item in entries) > 12 * 1024 * 1024
            or any(item.file_size > 4 * 1024 * 1024 for item in entries)
        ):
            raise ValueError("replay_maps_bounds")
    with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
        if not (
            {"floor", "obstacle", "uncertain"}.issubset(archive.files)
            or {"labels", "confidence"}.issubset(archive.files)
        ):
            raise ValueError("replay_maps_keys")
        maps = {
            key: archive[key]
            for key in (
                "floor",
                "obstacle",
                "uncertain",
                "critical",
                "gray",
                "labels",
                "confidence",
                "corridor",
            )
            if key in archive.files
        }
        for key in (
            "valid",
            "geometry_guard_clear",
            "geometry_collision",
            "geometry_blocked",
            "geometry_complete",
        ):
            if key in archive.files:
                value = archive[key]
                if value.shape != () or value.dtype != np.bool_:
                    raise ValueError("replay_geometry_metadata")
                maps[key] = bool(value.item())
    return maps, sha256(payload).hexdigest()


def _model_identity(row: Mapping[str, Any], perception: Mapping[str, Any]) -> None:
    """Explicit model settings must never be scored against another model's maps."""
    from venom_module_rider.perception_tuning import active_model_id

    context = row.get("context")
    if not isinstance(context, dict) or context.get("model_id") != active_model_id(
        perception
    ):
        raise ValueError("replay_model_identity")
    artifact = perception.get("artifact_sha256")
    if artifact and context.get("model_sha256") != artifact:
        raise ValueError("replay_model_artifact")


def replay(
    manifest: Path,
    params: Mapping[str, Any],
    output: Path,
    *,
    behavior: Mapping[str, Any] | None = None,
    perception: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write streamed results and return counts; no generated truth labels."""
    import numpy as np

    evaluator = TemporalEvidence()
    states: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    baseline: Counter[str] = Counter()
    durations: list[float] = []
    count = 0
    if output.resolve() == manifest.resolve():
        raise ValueError("replay_output_overwrites_input")
    if output.suffix != ".jsonl":
        raise ValueError("replay_output_format")
    maps_digest = sha256()
    manifest_digest = sha256()
    dimensions: set[tuple[int, int]] = set()
    profile: dict[str, Any] = {"id": "research", "qualified": False}
    if behavior is not None:
        from venom_module_rider.navigation_profiles import policy_digest

        profile = {
            "id": behavior["profile_id"],
            "behavior": dict(behavior),
            "effective_policy_sha256": policy_digest(
                dict(behavior), dict(perception) if perception is not None else None
            ),
            "qualified": False,
            **({"perception": dict(perception)} if perception is not None else {}),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        manifest.open("rb") as source,
        output.open("x", encoding="utf-8") as target,
    ):
        for raw_line in source:
            manifest_digest.update(raw_line)
            line = raw_line.decode("utf-8")
            if len(line) > 64 * 1024:
                raise ValueError("replay_manifest_bounds")
            if not line.strip():
                continue
            row = json.loads(line)
            if perception is not None:
                _model_identity(row, perception)
            path = _asset(manifest.parent, row["maps"])
            maps, map_sha256 = _read_maps(path)
            if ("labels" in maps) != ("confidence" in maps):
                raise ValueError("replay_semantic_pair")
            if perception is not None:
                semantic = perception.get("engine") == "segformer"
                if semantic and not {"labels", "confidence"}.issubset(maps):
                    raise ValueError("replay_semantic_maps_required")
                if not semantic and (
                    "labels" in maps
                    or not {"floor", "obstacle", "uncertain"}.issubset(maps)
                ):
                    raise ValueError("replay_depth_maps_required")
            maps_digest.update(map_sha256.encode())
            shape = maps["floor"].shape if "floor" in maps else maps["labels"].shape
            dimensions.add(tuple(shape))
            corridor = row.get("corridor", [0.35, 0.65, 0.65, 0.95])
            started = perf_counter()
            spatial = assess_evidence(
                maps, corridor, params, raw_reason=row.get("raw_reason")
            )
            for key in ("valid", "source_gap"):
                if key in row:
                    spatial[key] = row[key]
            if row.get("valid") is False:
                spatial["reason"] = row.get("reason", "navigation_invalid_evidence")
            temporal = evaluator.update(
                spatial,
                maps.get("gray"),
                context=row["context"],
                sequence=row["sequence"],
                frame_id=row["frame_id"],
                captured_s=row["captured_s"],
                params=params,
            )
            duration = (perf_counter() - started) * 1000
            durations.append(duration)
            reconstructed = row.get("raw_state") is None
            state = row.get("raw_state")
            if reconstructed:
                if spatial["uncertain_fraction"] > 0.01:
                    state = "unknown"
                elif (
                    spatial["counts"]["obstacle_pixels"]
                    + spatial["counts"]["ignored_obstacle_pixels"]
                ):
                    state = "obstacle"
                elif spatial["floor_fraction"] < 1:
                    state = "unknown"
                else:
                    state = "clear"
            if state not in {"clear", "unknown", "obstacle"}:
                raise ValueError("replay_raw_state")
            result = {
                "frame_id": row["frame_id"],
                "sequence": row["sequence"],
                "captured_s": row["captured_s"],
                "context": row["context"],
                "maps_sha256": map_sha256,
                "source_sha256": row.get("source_sha256"),
                "source_dimensions": {
                    "width": int(shape[1]),
                    "height": int(shape[0]),
                },
                "profile": profile,
                "raw_state": state,
                "baseline_reconstructed": reconstructed,
                "spatial": {
                    key: value
                    for key, value in spatial.items()
                    if not key.startswith("_")
                },
                "temporal": temporal,
                "evaluation_ms": duration,
            }
            target.write(json.dumps(result, allow_nan=False) + "\n")
            count += 1
            states[temporal["action"]] += 1
            reasons[temporal["reason"]] += 1
            baseline[str(state)] += 1
    return {
        "frames": count,
        "profile": profile,
        "source_dimensions": [
            {"width": width, "height": height} for height, width in sorted(dimensions)
        ],
        "manifest_sha256": manifest_digest.hexdigest(),
        "maps_digest_sha256": maps_digest.hexdigest(),
        "params_sha256": sha256(
            json.dumps(dict(params), sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
        "actions": dict(states),
        "reasons": dict(reasons),
        "baseline": dict(baseline),
        "evaluation_ms": {
            "p50": float(np.percentile(durations, 50)) if durations else None,
            "p95": float(np.percentile(durations, 95)) if durations else None,
            "max": max(durations, default=None),
        },
        "ground_truth": "not_inferred",
        "robot_connected": False,
        "time_basis": "source captured_s; no TTL renewal or metric TTC",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--params", type=Path, help="Research parameter JSON")
    selection.add_argument(
        "--profile",
        choices=("cautious", "balanced", "fluid"),
        help="Immutable experimental profile from the shared registry",
    )
    parser.add_argument(
        "--perception",
        type=Path,
        help="Saved per-model perception settings JSON; used with --profile",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    import cv2

    cv2.setNumThreads(1)
    behavior = None
    perception = None
    if args.perception:
        if not args.profile or args.perception.stat().st_size > 4096:
            raise ValueError("replay_perception")
        from venom_module_rider.depth_detector import PerceptionSettings

        perception = PerceptionSettings.model_validate_json(
            args.perception.read_text()
        ).model_dump()
    if args.profile:
        from venom_module_rider.navigation_profiles import parameters

        behavior = {"version": 1, "profile_id": args.profile, "profile_revision": 1}
        params = parameters(behavior, perception)
    else:
        if args.output.resolve() == args.params.resolve():
            raise ValueError("replay_output_overwrites_input")
        params = json.loads(args.params.read_text(encoding="utf-8"))
        if not isinstance(params, dict):
            raise ValueError("replay_params")
    summary_path = args.output.with_suffix(".summary.json")
    if summary_path.exists() or summary_path.is_symlink():
        raise FileExistsError("replay_summary_exists")
    summary = replay(
        args.manifest, params, args.output, behavior=behavior, perception=perception
    )
    with summary_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
