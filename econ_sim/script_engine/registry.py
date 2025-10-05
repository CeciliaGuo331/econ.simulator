"""管理用户上传脚本并在仿真过程中执行的注册中心。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, ValidationError

from ..data_access.models import TickDecisionOverrides, WorldState
from ..logic_modules.agent_logic import merge_tick_overrides
from ..utils.settings import WorldConfig

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


@dataclass
class _ScriptRecord:
    metadata: ScriptMetadata
    func: Callable[[dict], object]


class ScriptRegistry:
    """维护脚本与仿真实例之间关联关系的容器。"""

    def __init__(self) -> None:
        self._scripts: Dict[str, Dict[str, _ScriptRecord]] = {}

    def register_script(
        self,
        simulation_id: str,
        user_id: str,
        script_code: str,
        description: Optional[str] = None,
    ) -> ScriptMetadata:
        """编译并注册脚本，使其在后续 Tick 中参与决策。"""

        func = self._compile_script(script_code)
        script_id = str(uuid.uuid4())
        metadata = ScriptMetadata(
            script_id=script_id,
            simulation_id=simulation_id,
            user_id=user_id,
            description=description,
            created_at=datetime.now(timezone.utc),
        )
        bucket = self._scripts.setdefault(simulation_id, {})
        bucket[script_id] = _ScriptRecord(metadata=metadata, func=func)
        return metadata

    def list_scripts(self, simulation_id: str) -> List[ScriptMetadata]:
        """列出指定仿真实例下已注册的脚本。"""

        return [
            record.metadata for record in self._scripts.get(simulation_id, {}).values()
        ]

    def list_all_scripts(self) -> List[ScriptMetadata]:
        """返回所有仿真实例下的脚本元数据。"""

        scripts: List[ScriptMetadata] = []
        for bucket in self._scripts.values():
            scripts.extend(record.metadata for record in bucket.values())
        return scripts

    def remove_script(self, simulation_id: str, script_id: str) -> None:
        """根据脚本 ID 删除已注册脚本。"""

        bucket = self._scripts.get(simulation_id)
        if not bucket:
            raise ScriptExecutionError("Simulation has no registered scripts")
        if script_id not in bucket:
            raise ScriptExecutionError("Script not found for simulation")
        del bucket[script_id]
        if not bucket:
            self._scripts.pop(simulation_id, None)

    def remove_scripts_by_user(self, user_id: str) -> int:
        """批量移除指定用户的所有脚本，返回删除数量。"""

        removed = 0
        simulations_to_clear: List[str] = []
        for simulation_id, bucket in self._scripts.items():
            to_delete = [
                sid
                for sid, record in bucket.items()
                if record.metadata.user_id == user_id
            ]
            for sid in to_delete:
                del bucket[sid]
                removed += 1
            if not bucket:
                simulations_to_clear.append(simulation_id)
        for simulation_id in simulations_to_clear:
            self._scripts.pop(simulation_id, None)
        return removed

    def clear(self) -> None:
        """清空所有已注册脚本，主要用于测试。"""

        self._scripts.clear()

    def generate_overrides(
        self,
        simulation_id: str,
        world_state: WorldState,
        config: WorldConfig,
    ) -> Optional[TickDecisionOverrides]:
        """依次执行所有脚本，并合并生成的决策覆盖。"""

        bucket = self._scripts.get(simulation_id)
        if not bucket:
            return None

        combined: Optional[TickDecisionOverrides] = None
        for record in bucket.values():
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


__all__ = ["ScriptExecutionError", "ScriptMetadata", "ScriptRegistry"]
