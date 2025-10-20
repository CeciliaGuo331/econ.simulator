"""轻量的 per-simulation orchestrator 工厂与映射。

提供按 simulation_id 获取单例 SimulationOrchestrator 的能力，
并保证延迟创建、进程内锁保护，以及对闲置实例的回收。

行为要点：
- 支持在应用启动时注入共享的 `DataAccessLayer`（避免为每个 orchestrator 创建独立的 DB 连接池）。
- 为每个 simulation_id 提供协程级别的互斥锁，供 API 在变更操作时序列化调用。
- 启动一个后台回收任务，将超过空闲 TTL 的 orchestrator 从缓存中移除以释放内存。
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional

from ..data_access.redis_client import DataAccessLayer
from .orchestrator import SimulationOrchestrator

logger = logging.getLogger(__name__)

# 映射：simulation_id -> SimulationOrchestrator 实例缓存
_ORCH_MAP: Dict[str, SimulationOrchestrator] = {}
# 最近一次访问时间（monotonic 秒数）
_LAST_USED: Dict[str, float] = {}
# 每个 simulation 的操作锁，用于对可变操作进行序列化
_OP_LOCKS: Dict[str, asyncio.Lock] = {}
# 工厂级别锁，用于保护并发创建
_FACTORY_LOCK = asyncio.Lock()

# 在应用启动期间注入的共享 DataAccessLayer，用于复用连接池
_SHARED_DAL: Optional[DataAccessLayer] = None

# 后台回收任务与配置
_EVICTOR_TASK: Optional[asyncio.Task] = None
_EVICTOR_INTERVAL = float(
    int(__import__("os").environ.get("ECON_SIM_ORCH_EVICT_INTERVAL", "30"))
)
_EVICT_TTL = float(int(__import__("os").environ.get("ECON_SIM_ORCH_IDLE_TTL", "600")))


def init_shared_data_access(dal: DataAccessLayer) -> None:
    """注入共享的 DataAccessLayer，供所有创建的 orchestrator 复用。

    必须在应用启动（lifespan）期间调用，并且在创建任何 orchestrator 之前完成，
    以避免为每个 orchestrator 创建独立的数据库/连接池实例。
    """
    global _SHARED_DAL
    _SHARED_DAL = dal
    # start evictor when DAL initialized
    _start_evictor()


def _start_evictor() -> None:
    global _EVICTOR_TASK
    if _EVICTOR_TASK is not None and not _EVICTOR_TASK.done():
        return

    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 如果当前没有运行事件循环，调用者可以在稍后启动回收器
        return

    _EVICTOR_TASK = loop.create_task(_evictor_loop())


def _stop_evictor() -> None:
    global _EVICTOR_TASK
    if _EVICTOR_TASK is not None:
        try:
            _EVICTOR_TASK.cancel()
        except Exception:
            pass
        _EVICTOR_TASK = None


async def _evictor_loop() -> None:
    """后台任务：回收闲置的 orchestrator 以释放内存。

    回收为 best-effort：仅清理缓存的引用。实际的世界状态仍保存在配置的存储（Redis/Postgres），
    下一次 get_orchestrator 会按需重新加载。
    """
    try:
        while True:
            now = time.monotonic()
            to_remove = []
            for sim_id, last in list(_LAST_USED.items()):
                if now - last > _EVICT_TTL:
                    to_remove.append(sim_id)
            if to_remove:
                async with _FACTORY_LOCK:
                    for sim_id in to_remove:
                        inst = _ORCH_MAP.pop(sim_id, None)
                        _LAST_USED.pop(sim_id, None)
                        _OP_LOCKS.pop(sim_id, None)
                        if inst is not None:
                            logger.info("回收闲置 orchestrator：%s", sim_id)
            await asyncio.sleep(_EVICTOR_INTERVAL)
    except asyncio.CancelledError:
        return


@asynccontextmanager
async def get_orchestrator_locked(simulation_id: str):
    """Async context manager that acquires a per-simulation lock and yields an orchestrator.

    Use this in API handlers that perform mutating operations to serialize calls
    for a given simulation_id. Example:

        async with get_orchestrator_locked(sim_id) as orch:
            await orch.run_tick(sim_id)

    """
    sim_id = simulation_id or "default"
    # 确保 orchestrator 存在
    orch = await get_orchestrator(sim_id)
    lock = _OP_LOCKS.get(sim_id)
    if lock is None:
        lock = asyncio.Lock()
        _OP_LOCKS[sim_id] = lock
    await lock.acquire()
    try:
        yield orch
    finally:
        try:
            lock.release()
        except Exception:
            pass


async def get_orchestrator(simulation_id: str) -> SimulationOrchestrator:
    """按 simulation_id 获取对应的 SimulationOrchestrator 实例。

    若实例尚不存在则延迟创建并缓存。调用方应传入非空的 simulation_id；
    若传入空字符串，函数将使用字符串 "default" 作为键。
    """
    key = simulation_id or "default"
    # 快速路径：如果已存在则避免获取工厂锁以提升并发性能。
    inst = _ORCH_MAP.get(key)
    if inst is not None:
        _LAST_USED[key] = time.monotonic()
        return inst

    async with _FACTORY_LOCK:
        inst = _ORCH_MAP.get(key)
        if inst is None:
            # Create a new orchestrator with shared data access when available.
            if _SHARED_DAL is not None:
                inst = SimulationOrchestrator(data_access=_SHARED_DAL)
            else:
                inst = SimulationOrchestrator()
            _ORCH_MAP[key] = inst
            _LAST_USED[key] = time.monotonic()
    return inst


async def list_known_simulations() -> list[str]:
    """返回当前工厂已创建的 orchestrator 的 simulation_id 列表。

    注意：这只反映进程内缓存的映射，不代表底层数据存储中的所有仿真。
    """
    return list(_ORCH_MAP.keys())


async def shutdown_all() -> None:
    """停止回收任务并清空缓存的 orchestrator 引用（best-effort）。"""
    _stop_evictor()
    async with _FACTORY_LOCK:
        _ORCH_MAP.clear()
        _LAST_USED.clear()
        _OP_LOCKS.clear()
    return None
