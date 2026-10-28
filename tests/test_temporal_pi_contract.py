"""Opt-in integration of real Venom decisions and exported pure Rider-Pi permits.

RIDER_TEST_PI_ROOT must point at the companion source export with SOURCES.json.
The only outputs are lists. No Pi package initializers, client, transport, service
or hardware modules execute. drive_supported is an explicitly isolated selection
fixture compiled from the real companion function, not a replacement lease.
"""

import ast
import hashlib
import importlib.util
import json
import os
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from venom_module_rider.navigation_profiles import (
    DEFAULT_BEHAVIOR,
    parameters,
    policy_digest,
)
from venom_module_rider.vision_contract import VisionFrame
from venom_module_rider.vision_loop import VisionLoop

pytestmark = pytest.mark.skipif(
    not os.getenv("RIDER_TEST_PI_ROOT"),
    reason="Opt-in companion repo: set RIDER_TEST_PI_ROOT to pure Pi source export",
)
MODEL_SHA = "a" * 64


def _verified_sources(root):
    manifest = json.loads((root / "SOURCES.json").read_text())
    required = {
        "apps/compute/decision_lease.py",
        "apps/compute/journey.py",
        "services/navigation_profiles.py",
        "services/navigation_behavior.py",
        "services/perception_selection.py",
    }
    assert isinstance(manifest, dict)
    assert required <= manifest.keys()
    for relative, digest in manifest.items():
        path = (root / relative).resolve()
        assert path.is_relative_to(root), "Companion manifest escaped its root"
        assert isinstance(digest, str)
        assert len(digest) == 64
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative
    return manifest


def _package(monkeypatch, name, root):
    package = ModuleType(name)
    package.__package__ = name
    package.__path__ = [str(root / name.replace(".", "/"))]
    monkeypatch.setitem(sys.modules, name, package)


def _module(monkeypatch, root, name):
    path = root / (name.replace(".", "/") + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pi(monkeypatch):
    root = Path(os.environ["RIDER_TEST_PI_ROOT"]).resolve()
    manifest = _verified_sources(root)
    for name in tuple(sys.modules):
        if name in {"apps", "services"} or name.startswith(("apps.", "services.")):
            monkeypatch.delitem(sys.modules, name)
    for name in ("apps", "apps.compute", "services"):
        _package(monkeypatch, name, root)
    profiles = _module(monkeypatch, root, "services.navigation_profiles")
    behavior = _module(monkeypatch, root, "services.navigation_behavior")
    selection_path = root / "services/perception_selection.py"
    tree = ast.parse(selection_path.read_text(), filename=str(selection_path))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "drive_supported"
    ]
    assert len(functions) == 1
    selection = ModuleType("services.perception_selection")
    # Explicit selection fixture: retain real function body while excluding the
    # module's unrelated settings, service imports and persistence operations.
    exec(
        compile(
            ast.Module(body=functions, type_ignores=[]), str(selection_path), "exec"
        ),
        selection.__dict__,
    )
    monkeypatch.setitem(sys.modules, selection.__name__, selection)
    journey = _module(monkeypatch, root, "apps.compute.journey")
    lease = _module(monkeypatch, root, "apps.compute.decision_lease")
    assert "apps.compute.client" not in sys.modules
    assert "apps.motion" not in sys.modules
    assert "services.safety_contract" not in sys.modules
    return SimpleNamespace(
        profiles=profiles,
        behavior=behavior,
        journey=journey,
        DecisionLease=lease.DecisionLease,
        manifest=manifest,
    )


@dataclass
class Clock:
    now: float = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds=0.06):
        self.now += seconds


@dataclass
class Outputs:
    """The complete actuation boundary; these callbacks only append tuples."""

    calls: list = field(default_factory=list)

    def drive(self, speed, duration):
        self.calls.append(("drive", speed, duration))

    def zero(self, duration):
        self.calls.append(("zero", 0.0, duration))

    def stop(self):
        self.calls.append(("stop", 0.0, 0.0))

    @property
    def moves(self):
        return [row for row in self.calls if row[0] == "drive"]


class MapDetector:
    """Known image evidence, with the real spatial/temporal evaluator above it."""

    def __init__(self):
        self.case = "clear"

    @staticmethod
    def status():
        return {"sha256": MODEL_SHA}

    def detect(self, *_args, **_options):
        floor = np.ones((100, 100), dtype=bool)
        obstacle = np.zeros_like(floor)
        uncertain = np.zeros_like(floor)
        if self.case == "far_uncertain":
            uncertain[:10] = True
        elif self.case == "near_wire":
            obstacle[85, 30:70] = True
        floor[obstacle | uncertain] = False
        return {
            "state": "unknown"
            if uncertain.any()
            else "obstacle"
            if obstacle.any()
            else "clear",
            "reason": "raw_model",
            "floor_fraction": float(floor.mean()),
            "_navigation_maps": {
                "floor": floor,
                "obstacle": obstacle,
                "uncertain": uncertain,
                "valid": True,
            },
        }


def _behavior(profile="balanced"):
    return {**DEFAULT_BEHAVIOR, "profile_id": profile}


def _policy(profile="balanced", mode="drive"):
    return {
        "mode": mode,
        "revision": 1,
        "speed": 0.1,
        "max_motion_ms": 150,
        "decision_budget_ms": 300,
        "verified": True,
        "calibration_id": "mock-accepted",
        "model_sha256": MODEL_SHA,
        "behavior": _behavior(profile),
        "perception": {},
    }


def _frame(sequence, *, mode="drive", profile="balanced"):
    return VisionFrame(
        protocol_version=1,
        run_id="pure-pi-contract",
        frame_id=f"logical-{sequence}",
        sequence=sequence,
        config_revision=1,
        age_ms=0.0,
        deadline_ms=300,
        mode=mode,
        verified=True,
        calibration_id="mock-accepted",
        model_sha256=MODEL_SHA,
        corridor=(0.0, 0.0, 1.0, 1.0),
        behavior=_behavior(profile),
    )


def _lease(pi, clock, outputs, **policy):
    return pi.DecisionLease(
        _policy(**policy),
        "pure-pi-contract",
        outputs.drive,
        outputs.stop,
        clock,
        pause=outputs.zero,
    )


async def _decide(loop, clock, sequence, **kwargs):
    clock.advance()
    request = _frame(
        sequence,
        mode=kwargs.pop("mode", "drive"),
        profile=kwargs.pop("profile", "balanced"),
    )
    result = await loop.decide("fixture-pi", request, b"inert-image-evidence", **kwargs)
    return request, result, clock()


def _accept(lease, request, result, captured):
    return lease.accept(result, request.frame_id, request.sequence, captured)


@pytest.mark.parametrize("profile", ["legacy", "cautious", "balanced", "fluid"])
def test_real_peers_share_identical_immutable_policy_digest(pi, profile):
    value = _behavior(profile)
    assert pi.profiles.parameters(value) == parameters(value)
    assert pi.profiles.policy_digest(value) == policy_digest(value)
    assert pi.profiles.validate_behavior(value) == value


async def test_real_host_go_slow_near_stop_reaches_only_bounded_mock_outputs(pi):
    clock, outputs, detector = Clock(), Outputs(), MapDetector()
    loop = VisionLoop(detector, clock=clock)
    lease = _lease(pi, clock, outputs)
    try:
        accepted = []
        for sequence in range(1, 4):
            request, result, captured = await _decide(loop, clock, sequence)
            accepted.append(_accept(lease, request, result, captured))
        assert accepted == [False, False, True]
        assert outputs.moves == [("drive", 0.1, pytest.approx(0.15))]
        assert result["action"] == "go"
        assert result["temporal"]["evidence_frames"] == 3
        detector.case = "far_uncertain"
        request, result, captured = await _decide(loop, clock, 4)
        assert result["raw_state"] == "unknown"
        assert result["action"] == "slow"
        assert pi.behavior.reply_valid(_policy(), result)
        assert _accept(lease, request, result, captured)
        factor = parameters(_behavior())["slow_speed_factor"]
        assert outputs.moves[-1] == ("drive", 0.1 * factor, pytest.approx(0.15))
        assert 0 < lease.deadline - clock() <= result["valid_for_ms"] / 1000
        assert lease.deadline <= captured + 0.3
        detector.case = "near_wire"
        request, result, captured = await _decide(loop, clock, 5)
        assert result["action"] == "stop"
        assert result["temporal"]["spatial"]["hard_stop"] is True
        assert not _accept(lease, request, result, captured)
        assert outputs.calls[-1][0] == "zero"
        assert len(outputs.moves) == 2
        assert not lease.moving
    finally:
        await loop.close()


async def test_real_slow_permit_expires_and_latched_lease_never_revives(pi):
    clock, outputs, detector = Clock(), Outputs(), MapDetector()
    loop = VisionLoop(detector, clock=clock)
    lease = _lease(pi, clock, outputs)
    try:
        for sequence in range(1, 4):
            request, result, captured = await _decide(loop, clock, sequence)
            _accept(lease, request, result, captured)
        detector.case = "far_uncertain"
        request, result, captured = await _decide(loop, clock, 4)
        assert _accept(lease, request, result, captured)
        previous_moves = deepcopy(outputs.moves)
        clock.now = lease.deadline + 0.001
        assert not lease.tick()
        assert outputs.calls[-1][0] == "stop"
        assert lease.latched
        assert not _accept(lease, request, result, captured)
        assert outputs.moves == previous_moves
    finally:
        await loop.close()


@pytest.mark.parametrize("corruption", ["expired", "hash", "unknown_profile"])
async def test_real_pi_rejects_expired_or_changed_host_evidence_without_motion(
    pi, corruption
):
    clock, outputs = Clock(), Outputs()
    loop = VisionLoop(MapDetector(), clock=clock)
    lease = _lease(pi, clock, outputs)
    try:
        for sequence in range(1, 4):
            request, result, captured = await _decide(loop, clock, sequence)
        assert result["action"] == "go"
        result = deepcopy(result)
        if corruption == "expired":
            clock.advance(0.301)
        elif corruption == "hash":
            result["effective_policy_sha256"] = "f" * 64
        else:
            result["behavior"]["profile_id"] = "unsupported-profile"
        assert not _accept(lease, request, result, captured)
        assert outputs.moves == []
        assert outputs.calls[-1][0] == "stop"
        assert lease.latched
    finally:
        await loop.close()


async def test_canonical_duplicate_video_frame_never_becomes_a_pi_move(pi):
    clock, outputs = Clock(), Outputs()
    loop = VisionLoop(MapDetector(), clock=clock)
    lease = _lease(pi, clock, outputs)
    canonical = {
        "camera_session": "mock-camera",
        "transport_epoch": "mock-epoch",
        "pipeline_revision": 1,
        "frame_id": "canonical-302",
        "sequence": 302,
    }
    try:
        for sequence in range(1, 5):
            request, result, captured = await _decide(
                loop, clock, sequence, source_reference=canonical
            )
            assert result["action"] == "stop"
            assert not _accept(lease, request, result, captured)
        assert result["reason"] == "navigation_duplicate_frame"
        assert outputs.moves == []
    finally:
        await loop.close()


async def test_observation_from_real_host_never_grants_a_real_pi_lease(pi):
    clock, outputs = Clock(), Outputs()
    loop = VisionLoop(MapDetector(), clock=clock)
    lease = _lease(pi, clock, outputs, mode="observe")
    try:
        for sequence in range(1, 5):
            request, result, captured = await _decide(
                loop, clock, sequence, mode="observe"
            )
            assert result["action"] == "stop"
            assert not _accept(lease, request, result, captured)
        assert result["vision_action"] == "go"
        assert outputs.moves == []
    finally:
        await loop.close()


async def test_legacy_peer_rejects_unknown_slow_action_even_with_valid_capture(pi):
    clock, outputs = Clock(), Outputs()
    loop = VisionLoop(MapDetector(), clock=clock)
    lease = _lease(pi, clock, outputs, profile="legacy")
    try:
        for sequence in range(1, 4):
            request, result, captured = await _decide(
                loop, clock, sequence, profile="legacy"
            )
        assert result["action"] == "go"
        result = {**result, "action": "slow"}
        assert not pi.behavior.reply_valid(_policy(profile="legacy"), result)
        assert not _accept(lease, request, result, captured)
        assert outputs.moves == []
        assert lease.latched
    finally:
        await loop.close()


@pytest.mark.parametrize("profile", ["cautious", "balanced", "fluid"])
def test_real_peers_share_corrected_v2_policy_and_defaults(pi, profile):
    value = {
        "version": 1,
        "profile_id": profile,
        "profile_revision": 2,
        "overrides": {"obstacle_support_frames": 3, "object_growth_min": 1.2},
    }
    assert pi.profiles.parameters(value) == parameters(value)
    assert pi.profiles.policy_digest(value) == policy_digest(value)
    assert pi.profiles.validate_behavior(value) == value


@pytest.mark.parametrize("profile", ["cautious", "balanced", "fluid", "room_fluid"])
@pytest.mark.parametrize("custom", [False, True])
def test_revision_two_peer_parameters_and_custom_corrections_match(pi, profile, custom):
    value = {"version": 1, "profile_id": profile, "profile_revision": 2}
    if custom:
        value["overrides"] = {"obstacle_support_frames": 3}
    assert pi.profiles.parameters(value) == parameters(value)
    assert pi.profiles.policy_digest(value) == policy_digest(value)
    assert pi.profiles.validate_behavior(value) == value
