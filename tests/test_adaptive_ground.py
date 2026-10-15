"""Geometric correctness across height/tilt, not a claim of hardware acceptance."""

import math
from copy import deepcopy

import numpy as np
import pytest

from venom_module_rider.adaptive_ground import assess
from venom_module_rider.avoidance import AvoidanceSettings
from venom_module_rider.depth_detector import PerceptionSettings
from venom_module_rider.ground_config import GroundGeometry
from venom_module_rider.ground_fit import fit, pose_change
from venom_module_rider.steering import plan

CAMERA = GroundGeometry(fx=0.5, fy=0.5, radius_ratio=0.35, lookahead_ratio=1.5)
SETTINGS = PerceptionSettings(engine="depth_plane")


def scene(pitch=45.0, roll=0.0, height=1.0, box=None):
    h, w = 96, 128
    y, x = np.mgrid[:h, :w]
    pitch, roll = math.radians(pitch), math.radians(roll)
    normal = np.array(
        [
            math.sin(roll) * math.cos(pitch),
            math.cos(roll) * math.cos(pitch),
            math.sin(pitch),
        ]
    )
    normal /= np.linalg.norm(normal)
    forward = np.array([0.0, 0.0, 1.0]) - normal[2] * normal
    forward /= np.linalg.norm(forward)
    right = np.cross(normal, forward)
    rays = np.stack(((x / w - 0.5) / 0.5, (y / h - 0.5) / 0.5, np.ones((h, w))))
    denom = np.einsum("i,ihw->hw", normal, rays)
    depth = np.divide(height, denom, out=np.full((h, w), 100.0), where=denom > 0.01)
    if box:
        # Exact ray / vertical box slabs in ground coordinates.
        directions = np.stack(
            (
                np.einsum("i,ihw->hw", right, rays),
                np.einsum("i,ihw->hw", forward, rays),
                -denom,
            )
        )
        origin = (0.0, 0.0, height)
        near = np.zeros((h, w))
        far = np.full((h, w), np.inf)
        for index, (low, high) in enumerate(box):
            d = directions[index]
            with np.errstate(divide="ignore", invalid="ignore"):
                t0 = (low - origin[index]) / d
                t1 = (high - origin[index]) / d
            zero = abs(d) < 1e-10
            outside = zero & ((origin[index] < low) | (origin[index] > high))
            low_t = np.where(zero, -np.inf, np.minimum(t0, t1))
            high_t = np.where(zero, np.inf, np.maximum(t0, t1))
            near = np.maximum(near, low_t)
            far = np.minimum(far, high_t)
            far = np.where(outside, -np.inf, far)
        hit = (far >= near) & (near > 0) & (near < depth)
        depth = np.where(hit, near, depth)
    return depth


@pytest.mark.parametrize("pitch", [45.0, 52.0, 60.0])
@pytest.mark.parametrize("roll", [-8.0, 0.0, 8.0])
@pytest.mark.parametrize("height", [0.7, 1.0, 1.4])
def test_free_ground_remains_clear_through_accepted_pose_variations(
    pitch, roll, height
):
    row = assess(scene(pitch, roll, height), (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    assert row["state"] == "clear"
    assert row["ground_support"] > 0.9
    assert row["pose_source"] == "current_depth_ground_plane"


@pytest.mark.parametrize("pitch", [35.0, 45.0, 55.0])
@pytest.mark.parametrize("height", [0.8, 1.0, 1.3])
def test_central_box_never_becomes_ground(pitch, height):
    row = assess(
        scene(pitch=pitch, height=height, box=((-0.2, 0.2), (0.55, 0.85), (0.0, 0.6))),
        (0.35, 0.8, 0.65, 0.95),
        SETTINGS,
        CAMERA,
    )
    assert row["state"] != "clear"
    assert row.get("geometry") == "ground_capsule"


def test_missing_calibration_is_explicit_and_not_an_invented_focal_length():
    row = assess(scene(), (0.35, 0.8, 0.65, 0.95), SETTINGS, GroundGeometry())
    assert row["state"] == "unknown" and row["reason"] == "geometry_calibration_missing"


def test_vertical_plane_cannot_be_selected_as_ground():
    assert fit(np.ones((96, 128)), CAMERA, 0.06) is None


def test_depth_scale_changes_do_not_change_projected_footprint():
    depth = scene(box=((-0.2, 0.2), (0.55, 0.85), (0.0, 0.6)))
    a = assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    b = assess(depth * 4, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    assert a["state"] == b["state"]
    assert [r["state"] for r in a.get("heading_candidates", [])] == [
        r["state"] for r in b.get("heading_candidates", [])
    ]


def test_thin_obstacle_points_are_not_missed_between_path_samples():
    depth = scene()
    # A one-pixel height deviation on the central ray must block the corridor.
    depth[66, 64] *= 0.65
    row = assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    assert row["state"] == "obstacle"


def test_pose_hysteresis_allows_small_changes_and_resets_large_changes():
    previous = {"normal": [0.0, math.sqrt(0.5), math.sqrt(0.5)], "height": 1.0}
    assert not pose_change(previous, {**previous, "height": 1.02})
    assert pose_change(previous, {**previous, "height": 1.3})
    assert pose_change(previous, {"normal": [0.0, 0.5, math.sqrt(0.75)], "height": 1.0})


def planning_row(score=0.2):
    clear = {
        "state": "clear",
        "coverage_complete": True,
        "outlier_fraction": 0.0,
        "gap_width_deg": 20.0,
        "heading_deg": 10.0,
    }
    return {
        "state": "obstacle",
        "farther_outlier_pixels": 0,
        "ground_reference": {
            "normal": [0.0, math.sqrt(0.5), math.sqrt(0.5)],
            "height": 1.0,
        },
        "forward_clear_fraction": score,
        "sectors": {"left": {**clear, "state": "obstacle"}, "right": clear},
        "paths": {"left": clear, "right": clear, "guard": clear},
    }


def test_stagnation_is_measured_and_stops_before_the_hard_turn_budget():
    settings = AvoidanceSettings(enabled=True, sweep_verified=True, max_turn_pulses=5)
    state = None
    terminal = False
    for _ in range(12):
        action, reason, state, *rest = plan(
            planning_row(), state, settings, "ready", True
        )
        terminal = rest[-1]
        if terminal:
            break
    assert terminal and reason == "avoidance_no_progress"
    assert state["stalled"] == 2 and state["turns"] == 2


def test_pose_change_does_not_count_as_improvement_or_restore_turn_budget():
    row = planning_row(0.8)
    old = {
        "turns": 2,
        "pending_progress": 0.1,
        "geometry_reference": {**row["ground_reference"], "height": 0.5},
    }
    _, _, state, *_ = plan(
        row,
        deepcopy(old),
        AvoidanceSettings(enabled=True, sweep_verified=True),
        "ready",
        True,
    )
    assert state["turns"] == 2 and "progress_delta" not in state
    assert state["pose_resets"] == 1


def test_improvement_is_measured_on_a_fresh_comparable_ground_reference():
    row = planning_row(0.8)
    old = {
        "turns": 1,
        "pending_progress": 0.2,
        "pending_reference": row["ground_reference"],
        "geometry_reference": row["ground_reference"],
        "stalled": 1,
    }
    _, _, state, *_ = plan(
        row, old, AvoidanceSettings(enabled=True, sweep_verified=True), "ready", True
    )
    assert state["progress_delta"] == pytest.approx(0.6) and state["stalled"] == 0


@pytest.mark.parametrize("roll", [-8.0, 0.0, 8.0])
def test_unseen_near_sweep_at_shallow_pitch_is_unknown_not_free(roll):
    depth = scene(pitch=30.0, roll=roll)
    row = assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    assert row["state"] == "unknown"
    assert row["ground_support"] > 0.9
    assert row["paths"]["guard"]["coverage_complete"] is False


@pytest.mark.parametrize("side,expected", [(-1, "steer_right"), (1, "steer_left")])
def test_actual_projected_gap_selects_opposite_side(side, expected):
    x = side * 0.4
    row = assess(
        scene(box=((x - 0.12, x + 0.12), (0.9, 1.1), (0, 0.5))),
        (0.35, 0.8, 0.65, 0.95),
        SETTINGS,
        CAMERA,
    )
    assert row["state"] == "obstacle"
    assert row["gaps"] and row["paths"]["guard"]["state"] == "clear"
    state = None
    for _ in range(3):
        action, _, state, *_ = plan(
            row,
            state,
            AvoidanceSettings(enabled=True, sweep_verified=True, geometry=CAMERA),
            "ready",
            True,
        )
    assert action == expected


def test_inflated_body_cannot_fit_the_same_narrow_gap():
    depth = scene(box=((-0.52, -0.28), (0.9, 1.1), (0, 0.5)))
    small = assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    large = assess(
        depth,
        (0.35, 0.8, 0.65, 0.95),
        SETTINGS,
        CAMERA.model_copy(update={"radius_ratio": 0.9}),
    )
    assert small["paths"]["right"]["state"] == "clear"
    assert large["paths"]["right"]["state"] != "clear"


def test_refit_cannot_accept_localized_or_unsupported_ground():
    from venom_module_rider.ground_fit import supported

    y, x = np.mgrid[0.35:0.98:20j, 0:1:24j]
    inliers = (x > 0.35) & (x < 0.65)
    assert not supported(inliers.ravel(), x.ravel(), y.ravel())
    inliers = y < 0.85
    assert not supported(inliers.ravel(), x.ravel(), y.ravel())
    assert not supported(np.zeros(x.size, dtype=bool), x.ravel(), y.ravel())


@pytest.mark.parametrize(
    "depth", [np.ones((16, 16)), np.full((96, 128), np.nan), np.zeros((96, 128))]
)
def test_invalid_depth_is_not_a_floor(depth):
    with pytest.raises(ValueError, match="vision_model_output"):
        assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)


def test_projected_outline_changes_with_live_pose_and_body_size():
    a = assess(scene(), (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    b = assess(scene(pitch=55), (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    assert a["_path_overlay"]["center"] != b["_path_overlay"]["center"]
    assert any(p is None for p in a["_path_overlay"]["center"])
    assert all(
        p is None or (0 <= p[0] < 1 and 0 <= p[1] < 1)
        for p in a["_path_overlay"]["center"]
    )


@pytest.mark.parametrize("calibrated", [True, False])
def test_detector_ground_uses_one_inference_and_renders_current_projection(calibrated):
    import io
    from types import SimpleNamespace

    import torch
    from PIL import Image

    from venom_module_rider.depth_detector import DepthDetector

    calls = []

    class Inputs(dict):
        def to(self, _device):
            return self

    class Processor:
        def __call__(self, **_kw):
            return Inputs()

        def post_process_depth_estimation(self, _result, **_kw):
            return [{"predicted_depth": torch.tensor(scene(), dtype=torch.float32)}]

    detector = DepthDetector()
    detector.processor = Processor()
    detector.model = lambda **_kw: calls.append("infer") or SimpleNamespace()
    detector.digest = "a" * 64
    image = Image.fromarray(
        np.random.default_rng(4).integers(60, 200, (96, 128, 3), dtype=np.uint8)
    )
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    row = detector.detect_ground(
        stream.getvalue(),
        (0.35, 0.8, 0.65, 0.95),
        SETTINGS,
        CAMERA if calibrated else GroundGeometry(),
    )
    assert calls == ["infer"] and "_path_overlay" not in row
    assert row["state"] == ("clear" if calibrated else "unknown")
    assert Image.open(io.BytesIO(row["_preview"])).size == (128, 96)


def test_old_protocol_v3_request_without_geometry_keeps_image_semantics():
    settings = AvoidanceSettings.model_validate(
        {"enabled": True, "protocol_version": 3}
    )
    assert settings.geometry.mode == "image"


def test_thin_recess_between_sampled_rays_stays_a_recess_not_a_steer():
    depth = scene()
    depth[66, 64] *= 1.5
    row = assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    assert row["state"] == "obstacle" and row["farther_outlier_pixels"] > 0
    state = None
    for _ in range(3):
        action, reason, state, *_ = plan(
            row,
            state,
            AvoidanceSettings(enabled=True, sweep_verified=True, geometry=CAMERA),
            "ready",
            True,
        )
    assert action == "stop" and reason == "avoidance_recess"


@pytest.mark.parametrize(
    "pixel", [(80, 192), (85, 193), (89, 195), (90, 190), (93, 193)]
)
def test_tilted_camera_keeps_thin_obstacle_above_old_image_fit_band(pixel):
    depth = np.repeat(np.repeat(scene(pitch=60), 3, axis=0), 3, axis=1)
    assert assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)["state"] == "clear"
    depth[pixel] *= 0.9
    row = assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    assert row["state"] == "obstacle"
