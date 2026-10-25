"""Conservative admission reservations only inside the leased serial IO lane."""

import shutil
import time
from contextlib import contextmanager

from .errors import RiderError
from .recording_metrics import RecordingMetrics


class QuotaLedger:
    def __init__(self, store):
        self.store = store
        self.owned = False
        self.epoch = 0
        self.cached = None
        self.job_epoch = None
        self.metrics = RecordingMetrics()

    def ownership(self, acquired):
        # Release invalidates before the OS lease is unlocked. Immutable cache
        # tuples let an already admitted IO job retain its own reservations.
        self.epoch += 1
        self.owned = acquired

    def invalidate(self):
        self.cached = None

    @contextmanager
    def job(self):
        prior = self.job_epoch
        if prior is not None:
            yield
            return
        self.job_epoch = self.epoch if self.owned else None
        try:
            if self.job_epoch is not None:
                cached = self.cached
                if (
                    cached is None
                    or cached[0] != self.job_epoch
                    or time.monotonic() - cached[1] >= 1
                ):
                    with self.metrics.track("quota_reconcile_ms"):
                        used = self.store.usage()
                    self.cached = (self.job_epoch, time.monotonic(), used)
            yield
        finally:
            self.job_epoch = prior

    def admit(self, extra=0):
        cached = self.cached
        leased_job = (
            self.job_epoch is not None
            and cached is not None
            and cached[0] == self.job_epoch
        )
        if leased_job and cached is not None:
            used = cached[2]
            reservation = extra + 64 * 1024
        else:
            with self.metrics.track("quota_uncached_scan_ms"):
                used = self.store.usage()
            reservation = extra
        if used + reservation > self.store.limits.total_bytes:
            raise RiderError("samples_quota_reached", 507)
        if (
            shutil.disk_usage(self.store.root).free
            < self.store.limits.min_free_bytes + reservation
        ):
            raise RiderError("samples_disk_full", 507)
        if leased_job and cached is not None:
            # Even failed writes keep their reservation until the NEXT job's
            # reconciliation. Never scan/drop earlier reservations mid-operation.
            self.cached = (cached[0], cached[1], used + reservation)
