"""Cancellation-safe completion of owned HTTP transport cleanup."""

import asyncio


async def finish_native_cleanup(task):
    """Finish owned cleanup even if its caller is cancelled again, then propagate it."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            if task.cancelled():
                raise
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
