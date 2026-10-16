"""Event policy never interprets model output as actuator authority or truth."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from venom_module_rider.event_policy import EventProfile, EventSelector, Observation


def observation(sequence, now, state="obstacle", **values):
    return Observation(
        "robot",
        "host",
        "run",
        1,
        "a" * 64,
        "depth_plane",
        str(sequence),
        sequence,
        f"{sequence + 1:064x}",
        now,
        now + 0.2,
        state,
        "depth_ground_deviation",
        255,
        **values,
    )


def selector(**profile):
    return EventSelector("robot", "host", profile, 10.0)


def test_persistence_uses_distinct_source_frames_and_same_scene_is_bounded():
    policy = selector(stable_frames=3, stable_ms=200, cooldown_ms=500)
    assert policy.evaluate(observation(0, 10), 10) is None
    assert policy.evaluate(observation(0, 10.1), 10.1) is None
    assert policy.evaluate(observation(1, 10.1), 10.1) is None
    event = policy.evaluate(observation(2, 10.3), 10.3)
    assert event and event.reasons == ("new_obstacle", "new_scene")
    assert event.source.sequence == 2 and event.trigger == event.source
    for index in range(3, 100):
        now = 10.3 + (index - 2) * 0.1
        assert policy.evaluate(observation(index, now), now) is None
    assert policy.attempts == 1


def test_flickering_result_does_not_meet_persistence_or_generate_many_photos():
    policy = selector(stable_frames=2, stable_ms=100)
    for index in range(100):
        now = 10 + index * 0.1
        assert (
            policy.evaluate(
                observation(index, now, "clear" if index % 2 else "obstacle"), now
            )
            is None
        )
    assert policy.attempts == 0


def test_burst_uses_future_real_frames_with_same_event_and_distinct_source():
    policy = selector(stable_frames=1, stable_ms=0, burst_frames=3)
    first = policy.evaluate(observation(0, 10), 10)
    assert first
    assert policy.evaluate(observation(1, 10.1), 10.1) is None
    second = policy.evaluate(observation(2, 10.3), 10.3)
    third = policy.evaluate(observation(3, 10.6), 10.6)
    assert [first.burst_index, second.burst_index, third.burst_index] == [0, 1, 2]
    assert first.event_id == second.event_id == third.event_id
    assert second.source.sequence == 2 and second.trigger.sequence == 0
    assert second.source.sha256 != second.trigger.sha256
    assert policy.attempts == 3


def test_stop_stale_feed_limits_and_changed_model_never_resume():
    policy = selector(stable_frames=1, stable_ms=0, max_samples=1)
    assert policy.evaluate(observation(0, 10), 10)
    assert policy.evaluate(observation(1, 10.1), 10.1) is None
    assert policy.stopped == "auto_sample_limit"
    policy = selector()
    assert policy.tick(12) == "auto_feed_lost"
    assert policy.evaluate(observation(1, 12), 12) is None
    policy = selector()
    assert policy.evaluate(observation(0, 10, historical=True), 10) is None
    assert policy.stopped == "auto_source_stale"
    policy = selector()
    policy.evaluate(observation(0, 10), 10)
    assert policy.evaluate(replace(observation(1, 10.1), model="b" * 64), 10.1) is None
    assert policy.stopped == "auto_source_changed"


def test_foreign_old_unqualified_or_duplicate_results_do_not_trigger():
    policy = selector(stable_frames=1, stable_ms=0)
    for item in [
        replace(observation(0, 10), device="other"),
        replace(observation(0, 10), model=""),
        replace(observation(0, 10), sequence=True),
        replace(observation(0, 10), state="fake"),
    ]:
        assert policy.evaluate(item, 10) is None
    assert policy.evaluate(observation(1, 10), 10)
    assert policy.rejected == 4
    assert policy.evaluate(observation(1, 10.1), 10.1) is None


def test_profile_cannot_request_a_semantic_detector_or_zero_bounds():
    for value in [
        {"events": ["chair"]},
        {"events": ["new_scene", "new_scene"]},
        {"burst_frames": 6},
        {"cooldown_ms": 0},
        {"max_samples": True},
    ]:
        with pytest.raises(ValidationError):
            EventProfile.model_validate(value)


@pytest.mark.parametrize(
    "now,reason",
    [
        (float("nan"), "auto_clock_invalid"),
        (9.0, "auto_clock_invalid"),
        (11.0, "auto_duration_limit"),
    ],
)
def test_clock_and_duration_are_terminal(now, reason):
    policy = selector(duration_s=1.0)
    assert policy.tick(now) == reason
    assert policy.evaluate(observation(1, 10.1), 10.1) is None


def test_expired_result_cannot_be_relabelled_as_new_uncertainty():
    policy = selector(stable_frames=1, stable_ms=0)
    assert policy.evaluate(observation(0, 10), 10.3) is None
    assert policy.stopped == "auto_source_stale"
    assert policy.evaluate(observation(1, 10.4, "unknown"), 10.4) is None


def test_state_change_scene_threshold_cooldown_and_scene_dedup_are_independent():
    policy = selector(
        events=["new_scene", "state_change"],
        stable_frames=1,
        stable_ms=0,
        cooldown_ms=500,
    )
    first = policy.evaluate(observation(0, 10, "clear"), 10)
    assert first and first.reasons == ("new_scene",)
    assert policy.evaluate(observation(1, 10.1, "obstacle"), 10.1) is None
    assert policy.suppressed == 1
    # A pending stable transition is retained until cooldown permits saving.
    second = policy.evaluate(
        replace(observation(2, 10.7, "obstacle"), scene_hash=(1 << 32) - 1), 10.7
    )
    assert second and second.reasons == ("state_change", "new_scene")
    # Return to identical earlier scene/state: no repeated photo.
    assert policy.evaluate(observation(3, 11.3, "clear"), 11.3) is None
    assert policy.deduplicated == 1


def test_burst_never_duplicates_raw_and_budget_counts_admission_attempts():
    policy = selector(stable_frames=1, stable_ms=0, burst_frames=5, max_samples=2)
    first = policy.evaluate(observation(0, 10), 10)
    repeated = replace(observation(1, 10.3), sha256=first.source.sha256)
    assert policy.evaluate(repeated, 10.3) is None
    assert policy.attempts == 1 and policy.deduplicated == 1
    assert policy.evaluate(observation(2, 10.6), 10.6).burst_index == 1
    assert policy.evaluate(observation(3, 10.9), 10.9) is None
    assert policy.stopped == "auto_sample_limit"


def test_unknown_without_scene_hash_still_has_one_pending_prediction_event():
    policy = selector(events=["uncertainty"], stable_frames=1, stable_ms=0)
    event = policy.evaluate(replace(observation(0, 10, "unknown"), scene_hash=None), 10)
    assert event and event.reasons == ("uncertainty",)
    assert not hasattr(event, "confidence") and not hasattr(event, "operator_label")
    assert (
        policy.evaluate(replace(observation(1, 10.1, "unknown"), scene_hash=None), 10.1)
        is None
    )


def test_enabled_profile_isolated_from_later_caller_mutation():
    source = EventProfile(stable_frames=1, stable_ms=0)
    policy = EventSelector("robot", "host", source, 10)
    source.events.clear()
    assert policy.evaluate(observation(0, 10), 10)
