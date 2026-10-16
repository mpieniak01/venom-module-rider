"""Live inference never waits for offline CPU work or a database write lock."""

import asyncio
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from test_vision_loop import Detector, frame

from venom_module_rider.analysis_priority import AnalysisPriority
from venom_module_rider.errors import RiderError
from venom_module_rider.vision_loop import VisionLoop


class Child:
    def __init__(self):
        self.returncode = None
        self.signals = []
        self.killed = 0
        self.fail = False

    def send_signal(self, value):
        if self.fail:
            raise PermissionError("preemption unavailable")
        self.signals.append(value)

    def kill(self):
        self.killed += 1
        if self.fail:
            raise ProcessLookupError("already gone")


def test_live_suspends_only_owned_compute_and_io_barrier_can_finish():
    clock = [10.0]
    busy = [False]
    guard = AnalysisPriority(lambda: busy[0], clock=lambda: clock[0])
    child = Child()
    guard.attach(child)
    guard.live()  # IO must not be SIGSTOPped while a SQLite write may be open.
    assert child.signals == []
    assert not guard.phase_compute(child)
    clock[0] += 2
    busy[0] = True
    assert not guard.available()
    busy[0] = False
    assert guard.phase_compute(child)
    guard.live()
    guard.live()  # duplicate live notifications do not pile up signals
    assert child.signals == [signal.SIGSTOP] and guard.paused
    guard.phase_io(child)  # actual worker handshake says computation is complete
    assert child.signals == [signal.SIGSTOP, signal.SIGCONT]
    assert not guard.paused and not guard.available()
    assert guard.detach(child) is False
    guard.live()
    assert len(child.signals) == 2


def test_suspended_model_compute_resumes_after_the_live_engine_is_quiet():
    clock = [10.0]
    guard = AnalysisPriority(clock=lambda: clock[0])
    child = Child()
    guard.attach(child)
    assert guard.phase_compute(child)
    guard.live()
    assert guard.paused
    clock[0] += 2
    assert guard.phase_compute(child)
    assert child.signals == [signal.SIGSTOP, signal.SIGCONT]
    assert not guard.paused


def test_only_matching_process_can_transition_or_detach_and_closed_child_is_ignored():
    guard = AnalysisPriority()
    child, foreign = Child(), Child()
    guard.attach(child)
    with pytest.raises(RiderError, match="analysis_executor_busy"):
        guard.attach(foreign)
    for action in [guard.phase_io, guard.phase_compute, guard.detach]:
        with pytest.raises(RiderError, match="analysis_executor_owner"):
            action(foreign)
    assert guard.phase_compute(child)
    child.returncode = 0
    guard.live()
    assert child.signals == []
    guard.detach(child)


def test_failed_preemption_never_raises_in_live_lane_and_kills_only_offline_child():
    guard = AnalysisPriority()
    child = Child()
    guard.attach(child)
    guard.phase_compute(child)
    child.fail = True
    guard.live()
    assert child.killed == 1 and guard.failures == 2


async def test_optional_scheduler_failure_cannot_revoke_live_clear_decision():
    class Failed:
        def live(self):
            raise RuntimeError("offline hook failed")

    loop = VisionLoop(Detector(), priority=Failed())
    try:
        outputs = [await loop.decide("pi", frame(i), b"image") for i in range(1, 4)]
        assert [row["action"] for row in outputs] == ["stop", "stop", "go"]
        assert loop.status()["offline_priority_errors"] == 3
    finally:
        await loop.close()


async def test_replayed_or_invalid_frames_do_not_keep_offline_worker_paused():
    calls = []

    class Observer:
        def live(self):
            calls.append("live")

    loop = VisionLoop(Detector(), priority=Observer())
    try:
        await loop.decide("pi", frame(), b"image")
        with pytest.raises(RiderError, match="vision_replay"):
            await loop.decide("pi", frame(), b"image")
        with pytest.raises(RiderError, match="vision_frame_expired"):
            await loop.decide(
                "pi", frame(2).model_copy(update={"age_ms": 500.0}), b"image"
            )
        assert calls == ["live"]
    finally:
        await loop.close()


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="POSIX WSL/Linux worker")
async def test_actual_owned_child_stops_compute_and_resumes_for_io():
    clock = [10.0]
    guard = AnalysisPriority(clock=lambda: clock[0])
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        guard.attach(child)
        assert guard.phase_compute(child)
        guard.live()
        status = Path(f"/proc/{child.pid}/status")
        for _ in range(100):
            if "State:\tT" in status.read_text():
                break
            await asyncio.sleep(0.005)
        else:
            raise AssertionError("owned child was not stopped")
        guard.phase_io(child)
        for _ in range(100):
            if "State:\tT" not in status.read_text():
                break
            await asyncio.sleep(0.005)
        else:
            raise AssertionError("owned child did not resume for IO")
        assert not guard.phase_compute(child)
        clock[0] += 2
        assert guard.phase_compute(child)
        guard.live()
        assert guard.detach(child)
    finally:
        child.kill()
        child.wait(timeout=2)
