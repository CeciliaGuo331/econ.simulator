"""轻量的 per-simulation orchestrator 工厂与映射。

提供按 simulation_id 获取单例 SimulationOrchestrator 的能力，
并保证延迟创建和基本的并发安全性。
"""

from __future__ import annotations

import asyncio
from typing import Dict

from .orchestrator import SimulationOrchestrator

# mapping: simulation_id -> SimulationOrchestrator
_ORCH_MAP: Dict[str, SimulationOrchestrator] = {}
# async lock to guard concurrent creations
_ORCH_LOCK = asyncio.Lock()


async def get_orchestrator(simulation_id: str) -> SimulationOrchestrator:
    """按 simulation_id 获取对应的 SimulationOrchestrator 实例。

    若实例尚不存在则延迟创建并缓存。调用方应传入非空的 simulation_id；
    若传入空字符串，函数将使用字符串 "default" 作为键。
    """
    key = simulation_id or "default"
    # Fast path: avoid acquiring lock if already created.
    inst = _ORCH_MAP.get(key)
    if inst is not None:
        return inst

    async with _ORCH_LOCK:
        inst = _ORCH_MAP.get(key)
        if inst is None:
            # Create a new orchestrator with default config/store.
            inst = SimulationOrchestrator()
            _ORCH_MAP[key] = inst
    return inst


async def list_known_simulations() -> list[str]:
    """返回当前工厂已创建的 orchestrator 的 simulation_id 列表。

    注意：这只反映进程内缓存的映射，不代表底层数据存储中的所有仿真。
    """
    return list(_ORCH_MAP.keys())


async def shutdown_all() -> None:
    """若未来需要清理 orchestrator 资源，可在此实现。当前为占位函数。"""
    # SimulationOrchestrator 目前没有异步关闭方法；保留占位以便未来扩展。
    return None
