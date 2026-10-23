"""Existing host queue and state integration without chat/model invocation."""

from venom_core.api.dependencies import get_orchestrator, get_state_manager
from venom_core.core.models import TaskStatus
from venom_core.core.orchestrator.task_manager import TaskManager
from venom_core.core.queue_manager import QueueManager


class HostTasks:
    def __init__(self):
        self.state = get_state_manager()
        try:
            self.queue = get_orchestrator().task_manager
        except RuntimeError:
            # llm_off still has the shared StateManager; no chat runtime needed.
            self.queue = TaskManager(QueueManager(self.state))

    def create(self, run):
        label = (
            "Rider model installation "
            if run.get("kind") == "perception-install"
            else "Rider offline analysis "
        )
        task = self.state.create_task(label + run["id"])
        self.state.update_context(
            task.id,
            {
                "rider_analysis": {
                    "run_id": run["id"],
                    "session_id": run["session_id"],
                },
                "execution_mode": "offline-cpu",
            },
        )
        return task.id

    def abandoned(self, identity):
        task = self.state.get_task(identity)
        return task is None or task.status == TaskStatus.FAILED

    async def admit(self, identity, handle):
        if self.queue.is_paused or not (await self.queue.check_capacity())[0]:
            return False
        await self.queue.register_task(identity, handle)
        await self.state.update_status(identity, TaskStatus.PROCESSING)
        return True

    async def finish(self, identity, state, error):
        await self.queue.unregister_task(identity)
        await self.state.update_status(
            identity,
            TaskStatus.COMPLETED if state == "succeeded" else TaskStatus.FAILED,
            result=error or state,
        )
