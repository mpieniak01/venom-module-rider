from copy import deepcopy

import pytest

from venom_module_rider.avoidance import AvoidanceSettings, plan

SETTINGS = AvoidanceSettings(enabled=True, sweep_verified=True)


def obstacle():
    return {
        "state": "obstacle",
        "farther_outlier_pixels": 0,
        "sectors": {
            "left": {
                "state": "obstacle",
                "coverage_complete": True,
                "outlier_fraction": 0.2,
            },
            "right": {
                "state": "clear",
                "coverage_complete": True,
                "outlier_fraction": 0.0,
            },
        },
    }


def test_stop_then_stable_side_turn_then_new_orientation_clear():
    state = None
    actions = []
    for _ in range(3):
        action, _, state, _ = plan(obstacle(), state, SETTINGS, "ready", True)
        actions.append(action)
    assert actions == ["stop", "stop", "turn_right"]
    action, _, state, _ = plan(obstacle(), state, SETTINGS, "turn_settling", True)
    assert action == "stop"
    clear = {**obstacle(), "state": "clear"}
    for index in range(3):
        action, _, state, _ = plan(clear, state, SETTINGS, "ready", True)
        assert action == ("go" if index == 2 else "stop")


@pytest.mark.parametrize("accepted", [False, True])
def test_uncertain_or_unaccepted_never_turns(accepted):
    row = obstacle()
    row["sectors"]["right"]["state"] = "unknown"
    state = None
    for _ in range(8):
        action, _, state, _ = plan(row, state, SETTINGS, "ready", accepted)
        assert action == "stop"


def test_recess_blocks_turn_and_mirroring_changes_robot_side():
    row = obstacle()
    row["farther_outlier_pixels"] = 1
    state = None
    for _ in range(5):
        action, reason, state, _ = plan(row, state, SETTINGS, "ready", True)
        assert action == "stop"
    assert reason == "avoidance_recess"
    row["farther_outlier_pixels"] = 0
    state = None
    for _ in range(3):
        action, _, state, _ = plan(
            deepcopy(row),
            state,
            SETTINGS.model_copy(update={"mirrored": True}),
            "ready",
            True,
        )
    assert action == "turn_left"


def test_per_run_turn_budget_never_resets_on_clear():
    state = {"turns": 3}
    for _ in range(3):
        action, reason, state, _ = plan(obstacle(), state, SETTINGS, "ready", True)
        assert action == "stop"
    assert reason == "avoidance_turn_limit"


def test_updated_turn_limits_reject_values_above_full_scale_and_500_ms():
    import pytest
    from pydantic import ValidationError

    assert AvoidanceSettings(turn_speed=1.0, turn_ms=500).turn_ms == 500
    for values in ({"turn_speed": 1.001}, {"turn_ms": 501}):
        with pytest.raises(ValidationError):
            AvoidanceSettings(**values)
