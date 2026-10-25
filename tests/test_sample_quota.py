"""Admission never drops reservations mid-job or reuses a released lease."""

from dataclasses import replace

import pytest

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_store import SampleStore


def test_multiple_reservations_survive_expired_ttl_inside_one_job(
    tmp_path, monkeypatch
):
    store = SampleStore(tmp_path)
    scans = []
    monkeypatch.setattr(store, "usage", lambda: scans.append(1) or 1000)
    store.limits = replace(store.limits, total_bytes=1000 + 2 * (64 * 1024 + 100))
    clock = [0]
    monkeypatch.setattr(
        "venom_module_rider.sample_quota.time.monotonic", lambda: clock[0]
    )
    store.quota.ownership(True)
    with store.quota.job():
        store._space(100)
        clock[0] = 10
        store._space(100)
        with pytest.raises(RiderError, match="samples_quota_reached"):
            store._space(1)
    assert len(scans) == 1
    with store.quota.job():
        store._space(100)
    assert len(scans) == 2


def test_recovery_and_release_force_fresh_accounting(tmp_path, monkeypatch):
    store = SampleStore(tmp_path)
    scans = []
    monkeypatch.setattr(store, "usage", lambda: scans.append(1) or 1000)
    store.quota.ownership(True)
    with store.quota.job():
        store._space(10)
    store.recover_interrupted()
    with store.quota.job():
        store._space(10)
    assert len(scans) == 2
    store.quota.ownership(False)
    store._space(10)
    store._space(10)
    assert len(scans) == 4
