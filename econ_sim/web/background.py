"""简单的后台任务调度器，用于在不阻塞请求的情况下执行长耗时操作。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional


class JobConflictError(RuntimeError):
    """当同一仿真实例已有进行中的后台任务时抛出的异常。"""

    def __init__(self, simulation_id: str, existing_job_id: str) -> None:
        super().__init__(
            f"simulation '{simulation_id}' already has an active job {existing_job_id}"
        )
        self.simulation_id = simulation_id
        self.existing_job_id = existing_job_id


@dataclass
class BackgroundJob:
    """后台任务的状态记录。"""

    job_id: str
    simulation_id: str
    action: str
    status: str = "queued"
    message: Optional[str] = None
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "job_id": self.job_id,
            "simulation_id": self.simulation_id,
            "action": self.action,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if self.message is not None:
            payload["message"] = self.message
        if self.error is not None:
            payload["error"] = self.error
        if self.extra:
            payload["extra"] = self.extra
        return payload


class BackgroundJobManager:
    """基于 asyncio 的轻量后台任务管理器。"""

    def __init__(self) -> None:
        self._jobs: Dict[str, BackgroundJob] = {}
        self._active_simulations: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        # keep references to asyncio.Task objects for active runners so we can
        # cancel or await them during shutdown
        self._tasks: Dict[str, asyncio.Task] = {}

    async def enqueue(
        self,
        simulation_id: str,
        action: str,
        factory: Callable[[], Awaitable[Dict[str, Any]]],
    ) -> BackgroundJob:
        """提交新的后台任务。

        若同一仿真实例存在运行中的任务，将抛出 ``JobConflictError``。
        ``factory`` 应返回一个协程，最终产出包含 ``message`` 及其他字段的字典。
        """

        async with self._lock:
            existing_job_id = self._active_simulations.get(simulation_id)
            if existing_job_id:
                raise JobConflictError(simulation_id, existing_job_id)

            job_id = uuid.uuid4().hex
            job = BackgroundJob(
                job_id=job_id,
                simulation_id=simulation_id,
                action=action,
                status="queued",
                started_at=time.time(),
            )
            self._jobs[job_id] = job
            self._active_simulations[simulation_id] = job_id

        async def _runner() -> None:
            await self._run_job(job_id, factory)

        task = asyncio.create_task(_runner())
        # keep reference so shutdown can cancel/await
        async with self._lock:
            self._tasks[job_id] = task
        return job

    async def _run_job(
        self,
        job_id: str,
        factory: Callable[[], Awaitable[Dict[str, Any]]],
    ) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = time.time()

        try:
            result = await factory()
        except Exception as exc:  # pragma: no cover - 最终兜底
            async with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.error = str(exc)
                job.finished_at = time.time()
                self._active_simulations.pop(job.simulation_id, None)
            return

        async with self._lock:
            job = self._jobs[job_id]
            job.status = "succeeded"
            job.finished_at = time.time()
            if isinstance(result, dict):
                if "message" in result and result["message"] is not None:
                    job.message = result["message"]
                extra_payload = result.get("extra")
                if isinstance(extra_payload, dict):
                    job.extra = extra_payload
                else:
                    extra = {
                        key: value
                        for key, value in result.items()
                        if key not in {"message"}
                    }
                    if extra:
                        job.extra = extra
            self._active_simulations.pop(job.simulation_id, None)
        # cleanup task reference if present
        async with self._lock:
            try:
                self._tasks.pop(job_id, None)
            except Exception:
                pass

    async def get(self, job_id: str) -> Optional[BackgroundJob]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def get_for_simulation(self, simulation_id: str) -> Optional[BackgroundJob]:
        async with self._lock:
            job_id = self._active_simulations.get(simulation_id)
            if not job_id:
                return None
            return self._jobs.get(job_id)

    async def shutdown(self, *, cancel: bool = True, timeout: float = 5.0) -> None:
        """Gracefully shutdown background manager.

        If `cancel` is True, cancel all active runner tasks immediately and wait
        up to `timeout` seconds for them to finish. Otherwise wait for them to
        complete naturally up to `timeout` seconds.
        """
        # Snapshot current tasks
        async with self._lock:
            tasks = list(self._tasks.items())

        if not tasks:
            return

        pending = [t for _, t in tasks if not t.done()]
        if not pending:
            return

        if cancel:
            for t in pending:
                try:
                    t.cancel()
                except Exception:
                    pass

        # wait for tasks to finish or timeout
        try:
            await asyncio.wait(pending, timeout=timeout)
        except Exception:
            # best-effort: ignore exceptions during shutdown
            pass

        # After waiting, mark any still-pending jobs as failed
        async with self._lock:
            for job_id, task in list(self._tasks.items()):
                if not task.done():
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job.status = "failed"
                        job.error = "shutdown: task cancelled or timed out"
                        job.finished_at = time.time()
                    # remove task reference
                    self._tasks.pop(job_id, None)


__all__ = [
    "BackgroundJob",
    "BackgroundJobManager",
    "JobConflictError",
]
