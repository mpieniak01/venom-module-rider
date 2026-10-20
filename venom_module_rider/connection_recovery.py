"""Bounded local transport recovery. Only trusted server configuration selects it."""

from __future__ import annotations

import asyncio
import os
import signal
from contextlib import suppress

from .errors import RiderError

RECOVERY_TIMEOUT = 12.0


async def recover_connection(executable: str):
    # No shell, device URL, operator arguments or credentials in the command.
    # WSL interop is optional; the configured helper owns its transport details.
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "WSL_INTEROP", "WSL_DISTRO_NAME")
        if key in os.environ
    }
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
    except OSError as exc:
        raise RiderError("connection_recovery_failed", 503) from exc
    try:
        async with asyncio.timeout(RECOVERY_TIMEOUT):
            if await process.wait() != 0:
                raise RiderError("connection_recovery_failed", 503)
    except TimeoutError as exc:
        raise RiderError("connection_recovery_timeout", 504) from exc
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
