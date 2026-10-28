"""Offline geometry qualification on v1 exports; never a map or motion provider."""

from __future__ import annotations

import argparse
import io
import json
import os
import resource
import sys
import time
import uuid
import zipfile
from pathlib import Path

from PIL import Image

from venom_module_rider.analysis_metrics import adjacent
from venom_module_rider.analysis_profiles import ProfileCatalog, code_fingerprint
from venom_module_rider.analysis_worker import deny_network
from venom_module_rider.errors import RiderError
from venom_module_rider.samples_archive import SampleArchive, canonical

MAX_INPUT_BYTES = 16 * 1024**2
MAX_FRAMES = 32
MAX_REPORT_BYTES = 2 * 1024**2


def load_archive(source):
    """Reuse the actual export validator; no extraction, import or Pi requests."""
    if source.is_symlink() or not source.is_file():
        raise RiderError("samples_import_path", 400)
    with zipfile.ZipFile(source) as archive:
        if (
            len(archive.infolist()) > MAX_FRAMES + 1
            or archive.getinfo("manifest.json").file_size > MAX_REPORT_BYTES
        ):
            raise ValueError("mapping_input_limit")
    manifest = SampleArchive.validate_zip(source, MAX_INPUT_BYTES)
    samples = manifest["samples"]
    if not 1 <= len(samples) <= MAX_FRAMES:
        raise ValueError("mapping_input_limit")
    images = []
    with zipfile.ZipFile(source) as archive:
        for sample in samples:
            payload = archive.read(sample["raw"]["file"])
            with Image.open(io.BytesIO(payload)) as image:
                if image.width * image.height > 2_000_000:
                    raise ValueError("mapping_image_limit")
                decoded = image.convert("RGB")
                decoded.thumbnail((640, 640))
                images.append(decoded.copy())
    return manifest, images


def readiness(manifest):
    samples = manifest["samples"]
    order = manifest["session"].get("policy", {}).get("order_source")
    pairs = sum(
        adjacent(a, b, order) for a, b in zip(samples, samples[1:], strict=False)
    )
    ordered = len(samples) > 1 and pairs == len(samples) - 1
    # An ID describing operator acceptance is not camera intrinsics or scale GT.
    calibrated = sum(
        isinstance(sample.get("transform"), dict)
        and isinstance(sample["transform"].get("intrinsics"), list)
        for sample in samples
    )
    return {
        "source_frames": len(samples),
        "verified_adjacent_pairs": pairs,
        "source_order_verified": ordered,
        "order_source": order,
        "transformed_intrinsics_declared": calibrated,
        "transformed_intrinsics_qualified": False,
        "independent_metric_truth": False,
        "labelled_returns": False,
        "camera_body_extrinsics_qualified": False,
        "six_dof_camera_motion": "unmeasured",
        "motion_model_baseline": {
            "state": "unavailable",
            "trajectory": None,
            "reason": "sdk_output_is_not_measured_odometry",
        },
    }


def overlap(images):
    """Bounded ORB/RANSAC overlap probe; homographies are not metric poses."""
    import cv2
    import numpy as np

    cv2.setNumThreads(2)
    cv2.setRNGSeed(17)
    orb = cv2.ORB.create(nfeatures=600)
    views = []
    for image in images:
        grey = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
        points, descriptors = orb.detectAndCompute(
            grey, np.empty((0, 0), dtype=np.uint8)
        )
        views.append((points, descriptors))
    rows = []
    for index in range(1, len(views)):
        start = time.perf_counter()
        first, a = views[index - 1]
        second, b = views[index]
        matches = []
        if a is not None and b is not None and min(len(a), len(b)) >= 2:
            pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(a, b, k=2)
            matches = [
                pair[0]
                for pair in pairs
                if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
            ]
        inliers, residual, displacement = 0, None, None
        if len(matches) >= 8:
            src = np.asarray([first[m.queryIdx].pt for m in matches], dtype=np.float32)
            dst = np.asarray([second[m.trainIdx].pt for m in matches], dtype=np.float32)
            homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
            if (
                homography is not None
                and mask is not None
                and np.isfinite(homography).all()
            ):
                keep = mask.ravel().astype(bool)
                inliers = int(keep.sum())
                if inliers:
                    projected = cv2.perspectiveTransform(src[None], homography)[0]
                    residual = float(
                        np.median(np.linalg.norm(projected[keep] - dst[keep], axis=1))
                    )
                    displacement = float(
                        np.median(np.linalg.norm(dst[keep] - src[keep], axis=1))
                    )
        rows.append(
            {
                "first": index - 1,
                "second": index,
                "matches": len(matches),
                "inliers": inliers,
                "inlier_fraction": inliers / len(matches) if matches else None,
                "median_reprojection_px": residual,
                "median_displacement_px": displacement,
                "overlap_candidate": inliers >= 16
                and inliers / max(1, len(matches)) >= 0.3,
                "near_static_candidate": displacement is not None and displacement < 2,
                "elapsed_ms": (time.perf_counter() - start) * 1000,
            }
        )
    return {
        "backend": "opencv-orb-ransac",
        "version": cv2.__version__,
        "features": [len(points) for points, _ in views],
        "pairs": rows,
    }


def pose_hypothesis(images, *, catalog=None):
    """Retained DA3 Small, official native forward; CPU, no new checkpoint."""
    selected = images[:5]
    if len(selected) < 2 or len({image.size for image in selected}) != 1:
        raise ValueError("mapping_pose_views")
    import numpy as np
    import torch

    from scripts.depth_candidates import Candidate

    profile = (catalog or ProfileCatalog()).resolve("da3-small-280")
    provenance = profile.qualify()
    assert profile.sdk is not None
    sys.path.insert(0, str(profile.sdk))
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    start = time.perf_counter()
    candidate = Candidate(profile.backend, profile.model, "cpu", profile.resolution)
    load_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    assert candidate.processor is not None
    batch, _, _ = candidate.processor(
        selected,
        process_res=profile.resolution,
        process_res_method="upper_bound_resize",
        num_workers=1,
        sequential=True,
        print_progress=False,
    )
    inputs = batch.unsqueeze(0).to("cpu")
    with torch.inference_mode():
        result = candidate.model(
            inputs, infer_gs=False, use_ray_pose=False, ref_view_strategy="first"
        )
    duration = (time.perf_counter() - start) * 1000
    if result.get("extrinsics") is None or result.get("intrinsics") is None:
        raise ValueError("mapping_pose_unavailable")
    extrinsics = result["extrinsics"][0].float().cpu().numpy()
    intrinsics = result["intrinsics"][0].float().cpu().numpy()
    if (
        extrinsics.shape not in {(len(selected), 3, 4), (len(selected), 4, 4)}
        or intrinsics.shape != (len(selected), 3, 3)
        or not np.isfinite(extrinsics).all()
        or not np.isfinite(intrinsics).all()
        or (intrinsics[:, [0, 1], [0, 1]] <= 0).any()
    ):
        raise ValueError("mapping_pose_invalid")
    rotation, translation = extrinsics[:, :3, :3], extrinsics[:, :3, 3]
    centers = -np.einsum("nji,nj->ni", rotation, translation)
    return {
        "kind": "joint_view_pose_hypothesis",
        "qualified_localization": False,
        "model": provenance,
        "selected_indices": list(range(len(selected))),
        "model_load_ms": load_ms,
        "joint_inference_ms": duration,
        "processed_shape": list(inputs.shape),
        "extrinsics_convention": "sdk_world_to_camera",
        "extrinsics": extrinsics.tolist(),
        "intrinsics_processed_pixels": intrinsics.tolist(),
        "camera_centres_model_units": centers.tolist(),
        "metric_scale_available": False,
        "ground_truth_alignment": None,
        "ate_rmse_m": None,
        "rpe_m": None,
        "heading_error_deg": None,
        "loop_closure_precision": None,
        "tracking_loss": "not_measured_joint_model",
        "reference_view": "first",
        "device": "cpu",
        "peak_vram_mib": 0,
    }


def audit(source, *, with_pose=False, catalog=None):
    start = time.perf_counter()
    if source.stat().st_size > MAX_INPUT_BYTES + MAX_REPORT_BYTES:
        raise ValueError("mapping_input_limit")
    from scripts.depth_candidates import file_hash

    digest = file_hash(source)
    manifest, images = load_archive(source)
    inputs = manifest["samples"]
    ready = readiness(manifest)
    matching = overlap(images)
    # No stitching/map is published from unqualified motion or camera geometry.
    result = {
        "schema_version": 1,
        "kind": "rider-offline-geometry-research",
        "run_id": uuid.uuid4().hex,
        "archive_sha256": digest,
        "session_id": manifest["session"]["session_id"],
        "dataset_revision": manifest["dataset_revision"],
        "inputs": [
            {
                "sample_id": row["sample_id"],
                "raw_sha256": row["raw"]["sha256"],
                "frame_id": row.get("frame_id"),
                "sequence": row.get("sequence"),
            }
            for row in inputs
        ],
        "code_fingerprint": code_fingerprint(),
        "parameters": {
            "orb_features": 600,
            "ratio_test": 0.75,
            "ransac_threshold_px": 3.0,
            "minimum_inliers": 16,
            "minimum_inlier_fraction": 0.3,
            "thumbnail_max_side": 640,
            "pose_max_views": 5,
            "pose_reference_view": "first",
            "pose_resolution": 280,
            "input_transform": "aspect_preserving_thumbnail",
        },
        "readiness": ready,
        "overlap": matching,
        "panorama": {
            "state": "needs_capture_and_calibration",
            "artifact": None,
            "seam_error_px": None,
            "angular_coverage_deg": None,
            "reason": "overlap_alone_does_not_qualify_rotation_or_parallax",
        },
        "map": {
            "state": "needs_capture_and_metric_truth",
            "artifact": None,
            "ate_rmse_m": None,
            "loop_closure_precision": None,
        },
        "pose": {"state": "not_requested"},
        "decision": "collect_ordered_calibrated_sequences",
        "drive_supported": False,
        "distance_measurement": False,
        "pi_access": False,
    }
    if with_pose:
        try:
            result["pose"] = pose_hypothesis(images, catalog=catalog)
        except Exception as exc:
            # Other baselines and exact input provenance survive model failure.
            code = str(exc)
            result["pose"] = {
                "state": "failed",
                "error": code
                if code.startswith(("mapping_", "analysis_")) and len(code) < 96
                else "mapping_pose_failed",
            }
    if file_hash(source) != digest:
        raise ValueError("mapping_input_changed")
    result["elapsed_ms"] = (time.perf_counter() - start) * 1000
    result["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return result


def _open_publish_directory(directory: Path) -> int:
    """Create and open an absolute path without following directory symlinks."""
    if not directory.is_absolute() or ".." in directory.parts:
        raise ValueError("mapping_output_outside_git_required")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("mapping_secure_directory_unsupported")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.open(directory.anchor, flags)
    try:
        for part in (*directory.parts[1:], None):
            try:
                os.stat(".git", dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("mapping_output_outside_git_required")
            if part is None:
                break
            try:
                os.mkdir(part, mode=0o700, dir_fd=current)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def publish(output, report):
    """Publish a new report relative to a verified, pinned directory handle."""
    output = Path(output)
    if (
        not output.is_absolute()
        or ".." in output.parts
        or ".git" in output.parts
        or not output.name
    ):
        raise ValueError("mapping_output_outside_git_required")
    payload = canonical(report)
    if len(payload) > MAX_REPORT_BYTES:
        raise ValueError("mapping_report_limit")
    try:
        directory = _open_publish_directory(output.parent)
    except OSError as exc:
        raise ValueError("mapping_output_outside_git_required") from exc
    try:
        if __import__("shutil").disk_usage(directory).free < 256 * 1024**2 + len(
            payload
        ):
            raise ValueError("mapping_disk_full")
        temporary = ".rider-mapping-" + uuid.uuid4().hex
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(
                temporary,
                output.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            try:
                named = os.stat(output.parent, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("mapping_output_directory_changed") from exc
            pinned = os.fstat(directory)
            if (named.st_dev, named.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise ValueError("mapping_output_directory_changed")
            os.fsync(directory)
        finally:
            os.unlink(temporary, dir_fd=directory)
    finally:
        os.close(directory)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--pose",
        action="store_true",
        help="Explicit retained DA3 Small joint-view probe, not SLAM",
    )
    args = parser.parse_args(argv)
    os.environ.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1"
    )
    os.nice(10)
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    deny_network()
    report = audit(args.archive, with_pose=args.pose)
    publish(args.output, report)
    print(
        json.dumps(
            {
                "run_id": report["run_id"],
                "decision": report["decision"],
                "pose": report["pose"].get("kind", report["pose"].get("state")),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
