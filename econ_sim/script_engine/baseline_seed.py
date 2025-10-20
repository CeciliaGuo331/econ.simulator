"""用于为默认用户准备基线策略脚本的工具函数。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..data_access.models import AgentKind
from .registry import ScriptExecutionError, ScriptMetadata, ScriptRegistry

logger = logging.getLogger(__name__)

BASELINE_DIR = Path(__file__).resolve().parents[2] / "deploy" / "baseline_scripts"
# 使用纯数字 ID 以满足校验要求，同时选取远离常规种子范围的值以避免冲突。
BASELINE_HOUSEHOLD_ENTITY_ID = "900000"


@dataclass(frozen=True)
class BaselineScriptDefinition:
    user_id: str
    filename: str
    description: str
    agent_kind: AgentKind
    entity_id: str

    @property
    def path(self) -> Path:
        return BASELINE_DIR / self.filename


BASELINE_DEFINITIONS: Sequence[BaselineScriptDefinition] = (
    BaselineScriptDefinition(
        user_id="baseline.household@econ.sim",
        filename="household_baseline.py",
        description="[baseline] Household reference strategy",
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id=BASELINE_HOUSEHOLD_ENTITY_ID,
    ),
    BaselineScriptDefinition(
        user_id="baseline.firm@econ.sim",
        filename="firm_baseline.py",
        description="[baseline] Firm reference strategy",
        agent_kind=AgentKind.FIRM,
        entity_id="baseline_firm",
    ),
    BaselineScriptDefinition(
        user_id="baseline.bank@econ.sim",
        filename="bank_baseline.py",
        description="[baseline] Commercial bank reference strategy",
        agent_kind=AgentKind.BANK,
        entity_id="baseline_bank",
    ),
    BaselineScriptDefinition(
        user_id="baseline.central_bank@econ.sim",
        filename="central_bank_baseline.py",
        description="[baseline] Central bank reference strategy",
        agent_kind=AgentKind.CENTRAL_BANK,
        entity_id="baseline_central_bank",
    ),
    BaselineScriptDefinition(
        user_id="baseline.government@econ.sim",
        filename="government_baseline.py",
        description="[baseline] Government reference strategy",
        agent_kind=AgentKind.GOVERNMENT,
        entity_id="baseline_government",
    ),
)


def _load_script(definition: BaselineScriptDefinition) -> str:
    path = definition.path
    if not path.exists():
        raise FileNotFoundError(f"Baseline script not found: {path}")
    raw = path.read_text(encoding="utf-8")
    lines: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("from __future__ import"):
            continue
        if stripped.startswith("from typing import"):
            continue
        lines.append(line)
    code = "\n".join(lines)
    code = code.replace("context: Dict[str, Any]", "context")
    code = code.replace(") -> Dict[str, Any]:", "):")
    if raw.endswith("\n") and not code.endswith("\n"):
        code += "\n"
    return code


async def ensure_baseline_scripts(
    registry: ScriptRegistry,
    *,
    attach_to_simulation: Optional[str] = None,
    overwrite: bool = False,
    strict: bool = False,
) -> Dict[str, List[str]]:
    """确保为默认用户创建并（可选）挂载基线策略脚本。

    参数
    -----
    registry:
        目标脚本注册表实例。
    attach_to_simulation:
        如果提供，则会将基线脚本（无论是已有还是新建）挂载到该仿真实例。
    overwrite:
        若为 True，会删除基线用户已有脚本再重新创建基线脚本副本。
    strict:
        若为 True，在遇到文件缺失或注册失败时会抛出异常；否则记录错误并继续。

    返回
    ----
    Dict[str, List[str]]
        汇总字典：包含已创建脚本 ID（``created``），已挂载脚本 ID（``attached``），
        被跳过的用户（``skipped_users``）以及遇到的错误（``errors``）。
    """

    summary: Dict[str, List[str]] = {
        "created": [],
        "attached": [],
        "skipped_users": [],
        "errors": [],
    }

    for definition in BASELINE_DEFINITIONS:
        user_id = definition.user_id

        if overwrite:
            removed = await registry.remove_scripts_by_user(user_id)
            if removed:
                logger.info(
                    "Removed %s existing scripts for baseline user %s",
                    removed,
                    user_id,
                )
            existing: List[ScriptMetadata] = []
        else:
            existing = [
                script
                for script in await registry.list_user_scripts(user_id)
                if script.agent_kind == definition.agent_kind
                and script.entity_id == definition.entity_id
            ]

        if not existing:
            try:
                code = _load_script(definition)
            except FileNotFoundError as exc:
                message = f"Baseline script file missing for {user_id}: {exc}"
                summary["errors"].append(message)
                logger.error(message)
                if strict:
                    raise
                continue

            try:
                metadata = await registry.register_script(
                    simulation_id=(
                        attach_to_simulation if attach_to_simulation else None
                    ),
                    user_id=user_id,
                    script_code=code,
                    description=definition.description,
                    agent_kind=definition.agent_kind,
                    entity_id=definition.entity_id,
                )
            except ScriptExecutionError as exc:
                message = f"Failed to register baseline script for {user_id}: {exc}"
                summary["errors"].append(message)
                logger.error(message)
                if strict:
                    raise
                continue

            summary["created"].append(metadata.script_id)
            existing = [metadata]
        else:
            summary["skipped_users"].append(user_id)

        if attach_to_simulation:
            already_attached = any(
                script.simulation_id == attach_to_simulation for script in existing
            )
            if not already_attached:
                # 优先挂载最新的脚本（已按时间排序，取最后一项）。
                latest = existing[-1]
                try:
                    updated = await registry.attach_script(
                        latest.script_id, attach_to_simulation, user_id
                    )
                except ScriptExecutionError as exc:
                    # 某些失败是可预期的（例如单例类型在仿真中已存在相同类型脚本），
                    # 将其视为非致命错误并记录为 debug，其他错误则记录并在 strict 模式下抛出。
                    msg = str(exc)
                    singleton_conflict = any(
                        kw in msg
                        for kw in ("仅支持一个", "only support one", "already")
                    )
                    if singleton_conflict:
                        logger.debug(
                            "Skipping attach for baseline script %s to %s: %s",
                            latest.script_id,
                            attach_to_simulation,
                            exc,
                        )
                    else:
                        message = (
                            "Failed to attach baseline script "
                            f"{latest.script_id} to {attach_to_simulation}: {exc}"
                        )
                        summary["errors"].append(message)
                        logger.error(message)
                        if strict:
                            raise
                else:
                    summary["attached"].append(updated.script_id)

    return summary


__all__ = [
    "ensure_baseline_scripts",
    "BASELINE_DEFINITIONS",
    "BaselineScriptDefinition",
]
