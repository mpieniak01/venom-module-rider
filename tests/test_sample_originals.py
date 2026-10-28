"""Concurrent gallery reads preserve writer admission and source integrity."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from test_samples_api import BASE, rig
from test_samples_store import frame, save

from venom_module_rider.errors import RiderError
from venom_module_rider.sample_originals import OriginalReader


async def test_twelve_http_originals_succeed_while_the_writer_is_full(tmp_path):
    service, pi, registry, app, client = await rig(tmp_path)
    gate = threading.Event()
    writing = None
    try:
        session = await registry.start("gallery", "demo")
        expected = {}
        for index in range(12):
            source = frame(colour=(index * 10, 10, 30), device="demo")
            saved = await registry.read(
                save, registry.store, session["session_id"], source, str(index)
            )
            expected[saved["sample_id"]] = source.payload
        writing = [registry._submit(lambda: gate.wait(3)) for _ in range(4)]
        responses = await asyncio.gather(
            *(
                client.get(BASE + "/samples/items/" + sid + "/original")
                for sid in expected
            )
        )
        assert [response.status_code for response in responses] == [200] * 12
        assert [response.content for response in responses] == list(expected.values())
        assert not any(job.done() for job in writing)
        assert pi.state == "DISARMED"
        assert not pi.camera_on
    finally:
        gate.set()
        if writing:
            await asyncio.gather(*(asyncio.wrap_future(job) for job in writing))
        await client.aclose()
        await registry.close()
        await service.close()


async def test_reader_admission_is_bounded_and_cancellation_releases_it():
    gate = threading.Event()
    started = threading.Event()

    def read(identifier):
        started.set()
        if not gate.wait(3):
            raise RuntimeError("test reader remained blocked")
        return identifier

    reader = OriginalReader(SimpleNamespace(original=read), workers=1, capacity=2)
    first = asyncio.create_task(reader.get("first"))
    queued = None
    try:
        while not started.is_set():
            await asyncio.sleep(0.001)
        queued = asyncio.create_task(reader.get("queued"))
        await asyncio.sleep(0)
        with pytest.raises(RiderError, match="samples_read_queue_full"):
            await reader.get("overflow")
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        gate.set()
        assert await first == "first"
        assert await reader.get("after-cancel") == "after-cancel"
    finally:
        gate.set()
        await asyncio.gather(
            first, *([queued] if queued else []), return_exceptions=True
        )
        await reader.close()
    with pytest.raises(RiderError, match="samples_closed"):
        await reader.get("closed")
    await reader.close()


async def test_source_integrity_errors_still_reach_the_http_client(tmp_path):
    service, pi, registry, app, client = await rig(tmp_path)
    try:
        session = await registry.start("corrupt-gallery", "demo")
        source = frame(device="demo")
        result = await registry.read(
            save, registry.store, session["session_id"], source
        )
        (registry.store.blobs / (source.sha256 + ".bin")).write_bytes(b"broken")
        response = await client.get(
            BASE + "/samples/items/" + result["sample_id"] + "/original"
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "samples_source_corrupt"
    finally:
        await client.aclose()
        await registry.close()
        await service.close()


@pytest.mark.parametrize(
    "workers,capacity", [(0, 16), (5, 16), (2, 1), (1, 33), (True, 2)]
)
def test_reader_limits_are_explicit(workers, capacity):
    with pytest.raises(ValueError):
        OriginalReader(None, workers=workers, capacity=capacity)
