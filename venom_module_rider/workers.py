"""Owned worker lanes; callers retain their existing bounded admission slots."""

import asyncio
import contextvars
import functools
from concurrent.futures import ThreadPoolExecutor


class VisionWorkers:
    def __init__(self, name, workers=2):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)

    async def run(self, function, *args):
        # Inference and JPEG preparation must not queue behind unrelated LLM,
        # storage or API work in asyncio's process-wide default executor.
        context = contextvars.copy_context()
        call = functools.partial(context.run, function, *args)
        return await asyncio.get_running_loop().run_in_executor(self.executor, call)

    def close(self):
        # Owners first drain their admitted jobs. Shutdown never blocks the
        # event loop or leaves another thread pool alive after module teardown.
        self.executor.shutdown(wait=False, cancel_futures=True)
