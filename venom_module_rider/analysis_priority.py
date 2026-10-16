"""Live perception has priority over the single owned CPU-only offline child."""

import signal
import time

from .errors import RiderError


class AnalysisPriority:
    """Preempt compute only; the worker handshakes before entering filesystem IO."""

    def __init__(self, busy=lambda: False, *, clock=time.monotonic, quiet_s=2.0):
        self.busy, self.clock, self.quiet_s = busy, clock, quiet_s
        self.last_live = float("-inf")
        self.process = None
        self.phase = "idle"
        self.paused = False
        self.failures = 0

    def live(self):
        """Notify in the live lane without awaiting disk, models or job cleanup."""
        self.last_live = self.clock()
        process = self.process
        if (
            process is None
            or process.returncode is not None
            or self.phase != "compute"
            or self.paused
        ):
            return
        try:
            process.send_signal(signal.SIGSTOP)
            self.paused = True
        except (OSError, RuntimeError):
            self.failures += 1
            # A failed preemption ends only our offline child. Live inference
            # never waits for it, and its model/permission remain unchanged.
            try:
                process.kill()
            except (OSError, RuntimeError):
                self.failures += 1

    def available(self):
        """A busy or recently used live engine cannot admit offline compute."""
        return not self.busy() and self.clock() - self.last_live >= self.quiet_s

    def attach(self, process):
        """Only the controller that created the child can attach it."""
        if self.process is not None and self.process is not process:
            raise RiderError("analysis_executor_busy", 429)
        self.process = process
        self.phase = "io"
        self.paused = False

    def phase_io(self, process):
        """Called after a worker barrier: IO may finish even while live is busy."""
        self._owned(process)
        self.phase = "io"
        if self.paused and process.returncode is None:
            process.send_signal(signal.SIGCONT)
            self.paused = False

    def phase_compute(self, process):
        """Called while the worker waits at a compute barrier; no DB lock is held."""
        self._owned(process)
        if not self.available():
            return False
        self.phase = "compute"
        if self.paused and process.returncode is None:
            process.send_signal(signal.SIGCONT)
            self.paused = False
        return True

    def _owned(self, process):
        if self.process is not process:
            raise RiderError("analysis_executor_owner", 409)

    def detach(self, process):
        """Detach before child cleanup; no later live call can pause it again."""
        self._owned(process)
        paused = self.paused
        self.process = None
        self.phase = "idle"
        self.paused = False
        return paused
