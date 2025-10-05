"""管理用户上传脚本并在仿真过程中执行的注册中心。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Protocol, TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from ..data_access.models import TickDecisionOverrides, WorldState
from ..logic_modules.agent_logic import merge_tick_overrides
from ..utils.settings import WorldConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .postgres_store import StoredScript


logger = logging.getLogger(__name__)

_ALLOWED_BUILTINS = {
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "sorted": sorted,
    "round": round,
    "enumerate": enumerate,
    "range": range,
}


class ScriptExecutionError(RuntimeError):
    """在脚本编译或执行阶段抛出的异常。"""


class ScriptMetadata(BaseModel):
    """描述脚本的基本信息，便于前端展示与管理。"""

    script_id: str
    simulation_id: str
    user_id: str
    description: Optional[str] = None
    created_at: datetime
    code_version: str


@dataclass
class _ScriptRecord:
    metadata: ScriptMetadata
    func: Callable[[dict], object]


class ScriptStore(Protocol):
    async def save_script(self, metadata: ScriptMetadata, code: str) -> None: ...

    async def fetch_simulation_scripts(
        self, simulation_id: str
    ) -> List["StoredScript"]: ...

    async def list_all_metadata(self) -> List[ScriptMetadata]: ...

    async def delete_script(self, simulation_id: str, script_id: str) -> bool: ...

    async def delete_by_user(self, user_id: str) -> List[tuple[str, str]]: ...

    async def delete_simulation(self, simulation_id: str) -> List[str]: ...

    async def clear(self) -> None: ...


class ScriptRegistry:
    """维护脚本与仿真实例之间关联关系的容器。"""

    def __init__(self, store: Optional[ScriptStore] = None) -> None:
        self._store = store
        self._scripts: Dict[str, Dict[str, _ScriptRecord]] = {}
        self._load_lock = asyncio.Lock()
        self._registry_lock = asyncio.Lock()

    async def _ensure_simulation_loaded(self, simulation_id: str) -> None:
        if simulation_id in self._scripts:
            return
        if self._store is None:
            self._scripts.setdefault(simulation_id, {})
            return

        async with self._load_lock:
            if simulation_id in self._scripts:
                return
            try:
                stored_scripts = await self._store.fetch_simulation_scripts(
                    simulation_id
                )
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to load scripts for simulation %s",
                    simulation_id,
                    exc_info=exc,
                )
                self._scripts.setdefault(simulation_id, {})
                return

            bucket: Dict[str, _ScriptRecord] = {}
            for stored in stored_scripts:
                try:
                    func = self._compile_script(stored.code)
                except ScriptExecutionError as exc:
                    logger.warning(
                        "Skip persisted script %s for simulation %s: %s",
                        stored.metadata.script_id,
                        simulation_id,
                        exc,
                    )
                    continue
                bucket[stored.metadata.script_id] = _ScriptRecord(
                    metadata=stored.metadata,
                    func=func,
                )
            self._scripts[simulation_id] = bucket

    async def register_script(
        self,
        simulation_id: str,
        user_id: str,
        script_code: str,
        description: Optional[str] = None,
    ) -> ScriptMetadata:
        """编译并注册脚本，使其在后续 Tick 中参与决策。"""

        func = self._compile_script(script_code)
        await self._ensure_simulation_loaded(simulation_id)

        metadata = ScriptMetadata(
            script_id=str(uuid.uuid4()),
            simulation_id=simulation_id,
            user_id=user_id,
            description=description,
            created_at=datetime.now(timezone.utc),
            code_version=str(uuid.uuid4()),
        )

        if self._store is not None:
            await self._store.save_script(metadata, script_code)

        async with self._registry_lock:
            bucket = self._scripts.setdefault(simulation_id, {})
            bucket[metadata.script_id] = _ScriptRecord(metadata=metadata, func=func)

        return metadata

    async def list_scripts(self, simulation_id: str) -> List[ScriptMetadata]:
        """列出指定仿真实例下已注册的脚本。"""

        await self._ensure_simulation_loaded(simulation_id)
        async with self._registry_lock:
            bucket = self._scripts.get(simulation_id, {})
            return sorted(
                (record.metadata for record in bucket.values()),
                key=lambda meta: meta.created_at,
            )

    async def list_all_scripts(self) -> List[ScriptMetadata]:
        """返回所有仿真实例下的脚本元数据。"""

        if self._store is not None:
            try:
                return await self._store.list_all_metadata()
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to list scripts from persistent store", exc_info=exc
                )

        async with self._registry_lock:
            scripts: List[ScriptMetadata] = []
            for bucket in self._scripts.values():
                scripts.extend(record.metadata for record in bucket.values())
            scripts.sort(key=lambda meta: meta.created_at)
            return scripts

    async def remove_script(self, simulation_id: str, script_id: str) -> None:
        """根据脚本 ID 删除已注册脚本。"""

        await self._ensure_simulation_loaded(simulation_id)
        async with self._registry_lock:
            bucket = self._scripts.get(simulation_id)
            if not bucket or script_id not in bucket:
                raise ScriptExecutionError("Script not found for simulation")
            del bucket[script_id]
            if not bucket:
                self._scripts.pop(simulation_id, None)

        if self._store is not None:
            try:
                await self._store.delete_script(simulation_id, script_id)
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to delete script %s for simulation %s in persistent store",
                    script_id,
                    simulation_id,
                    exc_info=exc,
                )

    async def remove_scripts_by_user(self, user_id: str) -> int:
        """批量移除指定用户的所有脚本，返回删除数量。"""

        removed = 0
        async with self._registry_lock:
            simulations_to_clear: List[str] = []
            for simulation_id, bucket in self._scripts.items():
                to_delete = [
                    script_id
                    for script_id, record in bucket.items()
                    if record.metadata.user_id == user_id
                ]
                for script_id in to_delete:
                    del bucket[script_id]
                    removed += 1
                if not bucket:
                    simulations_to_clear.append(simulation_id)
            for simulation_id in simulations_to_clear:
                self._scripts.pop(simulation_id, None)

        if self._store is not None:
            try:
                store_removed = await self._store.delete_by_user(user_id)
                removed = max(removed, len(store_removed))
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to delete scripts for user %s in persistent store",
                    user_id,
                    exc_info=exc,
                )
        return removed

    async def clear(self) -> None:
        """清空所有已注册脚本，主要用于测试。"""

        async with self._registry_lock:
            self._scripts.clear()
        if self._store is not None:
            try:
                await self._store.clear()
            except Exception:  # pragma: no cover - best effort
                logger.exception("Failed to clear script store")

    async def detach_simulation(self, simulation_id: str) -> int:
        """移除与指定仿真实例关联的所有脚本，返回解除数量。"""

        await self._ensure_simulation_loaded(simulation_id)
        async with self._registry_lock:
            bucket = self._scripts.pop(simulation_id, None)
            removed = len(bucket or {})

        if self._store is not None:
            try:
                store_removed = await self._store.delete_simulation(simulation_id)
                removed = max(removed, len(store_removed))
            except Exception as exc:  # pragma: no cover - defensive log
                logger.error(
                    "Failed to detach scripts for simulation %s in persistent store",
                    simulation_id,
                    exc_info=exc,
                )
        return removed

    async def generate_overrides(
        self,
        simulation_id: str,
        world_state: WorldState,
        config: WorldConfig,
    ) -> Optional[TickDecisionOverrides]:
        """依次执行所有脚本，并合并生成的决策覆盖。"""

        await self._ensure_simulation_loaded(simulation_id)
        async with self._registry_lock:
            bucket = self._scripts.get(simulation_id)
            if not bucket:
                return None
            records = sorted(
                bucket.values(), key=lambda record: record.metadata.created_at
            )

        combined: Optional[TickDecisionOverrides] = None
        for record in records:
            overrides = self._execute_script(record.func, world_state, config)
            combined = merge_tick_overrides(combined, overrides)

        return combined

    def _compile_script(self, script_code: str) -> Callable[[dict], object]:
        """在受限环境下执行脚本，提取 `generate_decisions` 函数。"""

        global_env = {"__builtins__": _ALLOWED_BUILTINS}
        local_env: Dict[str, object] = {}
        try:
            exec(script_code, global_env, local_env)
        except Exception as exc:  # pragma: no cover - 语法或执行错误
            raise ScriptExecutionError(f"脚本编译失败: {exc}") from exc

        func = local_env.get("generate_decisions")
        if func is None or not callable(func):
            raise ScriptExecutionError(
                "脚本中必须定义可调用的 generate_decisions(context) 函数"
            )
        return func  # type: ignore[return-value]

    def _execute_script(
        self,
        func: Callable[[dict], object],
        world_state: WorldState,
        config: WorldConfig,
    ) -> Optional[TickDecisionOverrides]:
        """调用脚本函数并解析返回的决策覆盖。"""

        context = {
            "world_state": world_state.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
        }
        try:
            result = func(context)
        except Exception as exc:
            raise ScriptExecutionError(f"脚本执行失败: {exc}") from exc

        if result in (None, {}, []):
            return None

        try:
            return TickDecisionOverrides.model_validate(result)
        except ValidationError as exc:
            raise ScriptExecutionError(f"脚本返回结果解析失败: {exc}") from exc


__all__ = ["ScriptExecutionError", "ScriptMetadata", "ScriptRegistry", "ScriptStore"]
