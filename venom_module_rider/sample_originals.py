"""Bounded reads of immutable image originals, separate from capture writes."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from .errors import RiderError


class OriginalReader:
    """One gallery page can queue safely without consuming the session writer."""

    def __init__(self, store, *, workers=2, capacity=16):
        if (
            type(workers) is not int
            or type(capacity) is not int
            or not 1 <= workers <= 4
            or not workers <= capacity <= 32
        ):
            raise ValueError("invalid original reader limits")
        self.store = store
        self.pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="rider-originals"
        )
        self.slots = threading.BoundedSemaphore(capacity)
        self.closed = False

    async def get(self, sample_id):
        return await self.call(self.store.original, sample_id)

    async def call(self, method, *args, **kwargs):
        """Share bounded immutable media reads without using the capture writer."""
        if self.closed:
            raise RiderError("samples_closed", 503)
        if not self.slots.acquire(blocking=False):
            raise RiderError("samples_read_queue_full", 429)
        try:
            future = self.pool.submit(partial(method, *args, **kwargs))
        except Exception:
            self.slots.release()
            raise
        future.add_done_callback(lambda _: self.slots.release())
        return await asyncio.wrap_future(future)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await asyncio.to_thread(self.pool.shutdown, wait=True, cancel_futures=True)
