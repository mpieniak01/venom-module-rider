"""Recover stored analyses without delaying the manual panel's startup."""

import asyncio
import logging

from .analysis_manager import AnalysisManager
from .analysis_priority import AnalysisPriority

logger = logging.getLogger(__name__)


class AnalysisStartup:
    def __init__(self, app, samples):
        self.app = app
        self.manager: AnalysisManager | None = None
        self.closing = False
        self.priority = AnalysisPriority(
            busy=lambda: bool(
                app.state.rider_vision.busy or app.state.rider_vision.depth_busy
            )
        )
        self.task = asyncio.create_task(self._recover(samples))

    async def _recover(self, samples):
        try:
            manager = await asyncio.to_thread(
                AnalysisManager, samples, priority=self.priority
            )
            self.manager = manager
            await manager.initialize()
            if not self.closing:
                self.app.state.rider_analysis = manager
                self.app.state.rider_vision.priority = self.priority
        except Exception:
            self.app.state.rider_analysis_error = "analysis_unavailable"
            logger.exception("Stored Rider analysis recovery failed")
            await self._close_manager()
        finally:
            if self.closing:
                await self._close_manager()

    async def _close_manager(self):
        manager, self.manager = self.manager, None
        if manager is not None:
            if self.app.state.rider_analysis is manager:
                self.app.state.rider_analysis = None
            await manager.close()

    async def close(self):
        self.closing = True
        # Recovery owns queued disk IO and leases. Drain it before closing the
        # samples registry; cancelling an await cannot stop its worker thread.
        await asyncio.shield(self.task)
        await self._close_manager()
