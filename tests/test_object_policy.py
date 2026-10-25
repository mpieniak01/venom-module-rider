"""Whole-object room policy fixtures never contact a camera or actuator."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np

from venom_module_rider.depth_detector import PerceptionSettings
from venom_module_rider.navigation_policy import evaluate
from venom_module_rider.object_policy import RoomObjectPolicy
from venom_module_rider.temporal_evidence import TemporalEvidence

ROOM = {"version": 1, "profile_id": "room_fluid", "profile_revision": 2}
ROUTE = (0.5, 0.75, 0.95, 0.98)


class FixedMotion:
    def __init__(self, scale=1.0):
        self.scale = scale

    def reset(self):
        pass

    def update(self, _gray, _ratio):
        return {
            "status": "ok",
            "_transform": np.array([[self.scale, 0.0, 0.0], [0.0, self.scale, 0.0]]),
        }


def scene(box=None, *, shape=(64, 64)):
    obstacle = np.zeros(shape, bool)
    if box is not None:
        x0, y0, x1, y1 = box
        obstacle[y0:y1, x0:x1] = True
    return {
        "floor": ~obstacle,
        "obstacle": obstacle,
        "uncertain": np.zeros_like(obstacle),
        "critical": np.zeros_like(obstacle),
        "valid": True,
        "gray": np.random.default_rng(31).integers(0, 256, shape, dtype=np.uint8),
    }


def observer():
    value = TemporalEvidence()
    value._motion = FixedMotion()
    value._room_objects = RoomObjectPolicy()
    value._room_objects.tracker.motion = FixedMotion()
    from venom_module_rider.obstacle_persistence import ObstaclePersistence

    value._persistence = ObstaclePersistence()
    value._persistence.motion = FixedMotion()
    return value


def run(
    detector,
    data,
    n,
    *,
    wide=None,
    selection=None,
    context=None,
    route=ROUTE,
    perception=None,
):
    settings = ROOM if selection is None else selection
    frame = SimpleNamespace(
        behavior=SimpleNamespace(model_dump=lambda: settings),
        perception=perception or PerceptionSettings(engine="depth_plane"),
        corridor=route,
        sequence=n,
        frame_id=f"frame{n}",
    )
    result = {
        "state": "clear",
        "reason": "depth_floor_supported",
        "_navigation_maps": deepcopy(data),
        "_object_maps": deepcopy(data if wide is None else wide),
    }
    temporal = evaluate(
        result,
        frame,
        detector,
        context=context or {"fixture": "room", "policy": "room_fluid"},
        captured_s=n * 0.06,
    )
    return temporal


def test_whole_object_confirmation_precedes_tiny_route_intersection():
    detector = observer()
    # The complete region is stable outside STOP before one pixel enters it.
    outside = scene((10, 10, 33, 48))
    first = run(detector, outside, 1)
    second = run(detector, outside, 2)
    assert not first["objects"]["objects"][0]["intersects_route"]
    assert second["objects"]["objects"][0]["confirmed"]
    intersecting = scene((10, 10, 33, 49))
    current = run(detector, intersecting, 3)
    assert current["spatial"]["candidate"] == "obstacle"
    assert current["objects"]["objects"][0]["route_pixels"] == 1
    assert current["spatial"]["regions"][-1]["whole_object"]
    assert current["persistence"]["confirmed_pixels"] > 0


def test_persistent_stationary_near_object_never_needs_growth_to_stop():
    detector = observer()
    data = scene((30, 40, 48, 63))
    values = [run(detector, data, n) for n in range(1, 9)]
    assert all(value["action"] == "stop" for value in values)
    assert values[-1]["objects"]["objects"][0]["confirmed"]
    assert not values[-1]["objects"]["objects"][0]["approaching_candidate"]
    assert values[-1]["spatial"]["hard_stop"]


def test_brief_missing_near_mask_holds_stop_with_age_then_expires():
    detector = observer()
    data = scene((30, 40, 48, 63))
    run(detector, data, 1)
    run(detector, data, 2)
    first_missing = run(detector, scene(), 3)
    second_missing = run(detector, scene(), 4)
    assert first_missing["action"] == second_missing["action"] == "stop"
    held = second_missing["objects"]["objects"][0]
    assert held["evidence"] == "held" and not held["observed_now"]
    assert 119 <= held["age_ms"] <= 121
    assert second_missing["reason"] == "navigation_object_held"
    expired = run(detector, scene(), 5)
    assert not expired["objects"]["objects"]
    assert expired["reason"] != "navigation_object_held"


def test_jumping_small_predictions_do_not_acquire_object_support_or_floor():
    detector = observer()
    clean = scene()
    for n in range(1, 4):
        run(detector, clean, n)
    for n, x in [(4, 35), (5, 48), (6, 39)]:
        current = run(detector, scene((x, 54, x + 1, 55)), n)
        assert not current["objects"]["objects"]
        assert current["persistence"]["confirmed_pixels"] == 0
        assert current["persistence"]["suppression_is_floor_evidence"] is False
        assert current["spatial"]["uncertain_fraction"] > 0
        assert current["spatial"]["floor_fraction"] < 1
        assert current["action"] == "go"


def test_distant_repeated_approach_can_stop_before_near_zone():
    detector = observer()
    selection = {
        **ROOM,
        "overrides": {
            "enter_frames": 8,
            "object_growth_min": 1.05,
            "object_lower_min_px": 1,
        },
    }
    values = [
        run(
            detector,
            scene((25, 20, 40 + n, 46 + n)),
            n,
            selection=selection,
        )
        for n in range(1, 6)
    ]
    approaching = values[-1]
    item = approaching["objects"]["objects"][0]
    assert item["approaching_candidate"] and not item["near"]
    assert approaching["reason"] == "navigation_object_approaching"
    assert approaching["action"] == "stop"


def test_independent_critical_and_geometry_stop_survive_first_observation():
    for key in ("critical", "geometry_guard_clear", "geometry_collision"):
        data = scene()
        if key == "critical":
            data[key][60, 40] = True
        else:
            data[key] = key == "geometry_collision"
        current = run(observer(), data, 1)
        assert current["action"] == "stop"
        assert current["spatial"]["candidate"] == "obstacle"


def test_duplicate_and_context_changes_reset_support_and_never_authorize_go():
    detector = observer()
    data = scene((30, 40, 48, 63))
    run(detector, data, 1)
    run(detector, data, 2)
    duplicate = run(detector, data, 2)
    assert duplicate["action"] == "stop"
    assert not duplicate["objects"]["objects"]
    changed = run(detector, data, 3, context={"fixture": "changed"})
    assert changed["action"] == "stop"
    assert all(not item["confirmed"] for item in changed["objects"]["objects"])


def test_invalid_wide_maps_fail_closed_without_leaking_private_arrays():
    import json

    data = scene((30, 40, 48, 63))
    invalid = deepcopy(data)
    invalid["valid"] = False
    current = run(observer(), data, 1, wide=invalid)
    assert current["action"] == "stop"
    assert not current["spatial"]["valid"]
    assert current["objects"]["state"] == "unavailable"
    json.dumps(current, allow_nan=False)


def test_reliable_pixels_only_low_confidence_proposal_cannot_confirm_obstacle():
    detector = observer()
    data = scene()
    data["labels"] = np.full((64, 64), 3, np.uint8)
    data["labels"][20:60, 20:50] = 12
    data["confidence"] = np.full((64, 64), 0.99, np.float32)
    data["confidence"][20:60, 20:50] = 0.2
    values = [run(detector, data, n) for n in range(1, 5)]
    for value in values:
        item = value["objects"]["objects"][0]
        assert not item["confirmed"]
        assert item["reliable_area_pixels"] == 0
        assert value["spatial"]["obstacle_fraction"] == 0
        assert value["action"] == "stop"


def test_camera_scale_is_not_object_approach_and_public_result_is_json():
    import json

    detector = observer()
    detector._room_objects.tracker.motion = FixedMotion(1.1)
    selection = {**ROOM, "overrides": {"object_appearance_min": 0.0}}
    values = [
        run(
            detector,
            scene(box),
            n,
            selection=selection,
            route=(0.0, 0.0, 1.0, 1.0),
        )
        for n, box in enumerate(
            [(10, 10, 25, 25), (11, 11, 28, 28), (12, 12, 31, 31), (13, 13, 34, 34)],
            1,
        )
    ]
    last = values[-1]["objects"]["objects"][0]
    assert last["confirmed"]
    assert not last["approaching_candidate"]
    assert last["growth"] < 1.1
    json.dumps(values[-1], allow_nan=False)


def test_cut_edge_object_stays_an_obstacle_without_claiming_growth():
    detector = observer()
    values = [run(detector, scene((0, 20, 40 + n, 58 + n)), n) for n in range(1, 5)]
    item = values[-1]["objects"]["objects"][0]
    assert item["clipped"]
    assert item["confirmed"]
    assert not item["approaching_candidate"]
    assert values[-1]["action"] == "stop"


def test_mask_jump_is_new_unconfirmed_proposal_not_inherited_growth():
    detector = observer()
    data = scene((20, 20, 40, 45))
    run(detector, data, 1)
    established = run(detector, data, 2)
    previous = established["objects"]["objects"][0]["id"]
    jump = run(detector, scene((0, 0, 64, 64)), 3)
    proposed = next(item for item in jump["objects"]["objects"] if item["observed_now"])
    assert proposed["id"] != previous
    assert not proposed["confirmed"]
    assert not proposed["approaching_candidate"]


def test_surface_policy_controls_whether_rug_is_tracked_as_obstacle():
    data = scene((30, 40, 48, 63))
    data["labels"] = np.where(data["obstacle"], 28, 3).astype(np.uint8)
    data["confidence"] = np.full((64, 64), 0.99, np.float32)
    for surface in ["floor_only", "floor_rug"]:
        detector = observer()
        perception = PerceptionSettings(
            engine="segformer",
            model_id="segformer-b0-ade20k",
            semantic_models={"segformer-b0-ade20k": {"surface": surface}},
        )
        run(detector, data, 1, perception=perception)
        current = run(detector, data, 2, perception=perception)
        if surface == "floor_only":
            assert current["objects"]["objects"][0]["confirmed"]
            assert current["spatial"]["hard_stop"]
        else:
            assert not current["objects"]["objects"]
            assert current["spatial"]["candidate"] == "clear"


def test_degenerate_corridor_and_invalid_partition_are_json_safe_stop():
    import json

    for corruption in ["corridor", "partition"]:
        detector = observer()
        data = scene((30, 40, 48, 63))
        run(detector, data, 1)
        if corruption == "corridor":
            data["corridor"] = np.zeros((64, 64), bool)
        else:
            data["uncertain"][:] = True
        current = run(detector, data, 2)
        assert current["action"] == "stop"
        assert current["state"] == "unknown"
        json.dumps(current, allow_nan=False)


def test_actual_geometry_corridor_is_used_for_retained_near_object():
    detector = observer()
    mask = np.zeros((64, 64), bool)
    mask[40:63, 5:15] = True
    data = scene((3, 30, 17, 63))
    data["corridor"] = mask
    run(detector, data, 1)
    current = run(detector, data, 2)
    assert current["objects"]["objects"][0]["near"]
    missing = scene()
    missing["corridor"] = mask
    held = run(detector, missing, 3)
    assert held["reason"] == "navigation_object_held"


def test_float_compensated_object_overlay_is_rendered():
    import io

    from PIL import Image

    from venom_module_rider.object_diagnostics import render_objects

    detector = observer()
    data = scene((30, 40, 48, 63))
    run(detector, data, 1)
    current = run(detector, data, 2)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buffer, format="JPEG")
    payload = buffer.getvalue()
    rendered = render_objects(payload, current["objects"])
    assert rendered != payload
    with Image.open(io.BytesIO(rendered)) as image:
        assert image.size == (64, 64)


def test_capture_gap_resets_room_support_and_cannot_resume_from_memory():
    detector = observer()
    data = scene((30, 40, 48, 63))
    run(detector, data, 1)
    run(detector, data, 2)
    stale = run(detector, data, 10)
    assert stale["action"] == "stop"
    assert stale["reason"] == "navigation_frame_gap"
    assert stale["objects"]["state"] == "unavailable"


def test_clipped_full_width_band_cannot_boost_tiny_near_intersection_or_arm_hold():
    route = (0.35, 0.8, 0.65, 0.95)
    for pixels in [2, 3, 6, 8]:
        detector = observer()
        data = scene((0, 228, 320, 236), shape=(240, 320))
        data["obstacle"][227, 150 : 150 + pixels] = True
        data["floor"] = ~data["obstacle"]
        for n in range(1, 5):
            current = run(detector, data, n, route=route)
        item = current["objects"]["objects"][0]
        assert item["clipped"] and item["confirmed"]
        assert not item["whole_object_qualified"]
        assert item["raw_route_pixels"] == pixels
        assert item["route_pixels"] == 0
        assert not current["spatial"]["hard_stop"]
        assert current["action"] == "go"
        missing = run(detector, scene(shape=(240, 320)), 5, route=route)
        assert not missing["objects"]["objects"]
        assert missing["action"] == "go"


def test_large_clipped_object_still_qualifies_ordinary_near_and_can_hold_stop():
    detector = observer()
    data = scene((0, 40, 48, 64))
    run(detector, data, 1)
    current = run(detector, data, 2)
    item = current["objects"]["objects"][0]
    assert item["clipped"] and not item["whole_object_qualified"]
    assert item["route_pixels"] > 100
    assert current["reason"] == "navigation_near_obstacle"
    assert current["action"] == "stop"
    missing = run(detector, scene(), 3)
    assert missing["reason"] == "navigation_object_held"
    assert missing["action"] == "stop"
