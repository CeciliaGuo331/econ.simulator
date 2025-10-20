"""用于为 "test_world" 仿真环境准备脚本和用户的实用工具。

此模块提供函数来在测试仿真（默认标识符为 ``test_world``）中创建
一组用于自动化测试和开发的用户账户及脚本。主要功能包括：

- 创建若干 household 脚本（默认 400 个），以及四个单例(agent)脚本：
    firm、bank、government 与 central_bank。
- 在需要时可复用已有的 orchestrator、script registry 与 user manager，
    并支持选择性覆盖已存在的脚本以便重新种子化。

所有修改仅限于注释与文档翻译，函数逻辑保持不变。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from ..auth import user_manager as default_user_manager
from ..auth.user_manager import UserAlreadyExistsError, UserManager
from ..core.orchestrator import SimulationOrchestrator
from ..data_access.models import AgentKind
from ..utils.settings import get_world_config
from . import script_registry as default_registry
from .registry import ScriptRegistry


TEST_WORLD_SIMULATION_ID = "test_world"
TEST_WORLD_DEFAULT_HOUSEHOLDS = 400
TEST_WORLD_PASSWORD = "TestWorldPass123!"


@dataclass(slots=True)
class SeedSummary:
    simulation_id: str
    users_created: int
    users_existing: int
    scripts_created: int
    scripts_existing: int

    @property
    def total_users(self) -> int:
        return self.users_created + self.users_existing

    @property
    def total_scripts(self) -> int:
        return self.scripts_created + self.scripts_existing


_HOUSEHOLD_SCRIPT = """
def generate_decisions(context):
    return {}
"""

_SINGLETON_SCRIPT = _HOUSEHOLD_SCRIPT

_SINGLETON_AGENTS: Sequence[tuple[str, AgentKind, str, str]] = (
    ("test_firm@econ.sim", AgentKind.FIRM, "firm_primary", "firm"),
    ("test_bank@econ.sim", AgentKind.BANK, "bank_primary", "commercial_bank"),
    (
        "test_government@econ.sim",
        AgentKind.GOVERNMENT,
        "government_primary",
        "government",
    ),
    (
        "test_central_bank@econ.sim",
        AgentKind.CENTRAL_BANK,
        "central_bank_primary",
        "central_bank",
    ),
)


async def seed_test_world(
    *,
    simulation_id: str = TEST_WORLD_SIMULATION_ID,
    household_count: Optional[int] = TEST_WORLD_DEFAULT_HOUSEHOLDS,
    orchestrator: Optional[SimulationOrchestrator] = None,
    registry: Optional[ScriptRegistry] = None,
    user_manager: Optional[UserManager] = None,
    overwrite_existing: bool = False,
) -> SeedSummary:
    """确保标准的 ``test_world`` 仿真已被完整种子化。

    参数
    -----
    simulation_id:
        目标仿真标识，默认 ``"test_world"``。
    household_count:
        要为其创建脚本的 household 数量。默认 400（与四个单例代理合计
        将产生 404 个脚本）。当传入 ``None`` 时，使用配置或默认值。
    orchestrator:
        可选的 orchestrator 实例以供复用；若未提供则创建新实例。
    registry:
        可选的脚本注册表；默认使用进程内的全局注册表实例。
    user_manager:
        可选的用户管理器用于创建账户；缺省使用全局 user manager。
    overwrite_existing:
        若为 ``True``，会先删除目标用户已有的脚本再创建新脚本；无论为
        True 还是 False，操作都保持幂等性。
    """

    orchestrator = orchestrator or SimulationOrchestrator()
    registry = registry or default_registry
    user_manager = user_manager or default_user_manager

    await orchestrator.create_simulation(simulation_id)
    existing_scripts = {
        (meta.agent_kind, meta.entity_id): meta
        for meta in await registry.list_scripts(simulation_id)
    }

    users_created = 0
    users_existing = 0
    scripts_created = 0
    scripts_existing = 0

    async def ensure_user(email: str, user_type: str) -> None:
        nonlocal users_created, users_existing
        try:
            await user_manager.register_user(email, TEST_WORLD_PASSWORD, user_type)
        except UserAlreadyExistsError:
            users_existing += 1
        else:
            users_created += 1

    async def ensure_script(
        email: str,
        agent_kind: AgentKind,
        entity_id: str,
        script_body: str = _HOUSEHOLD_SCRIPT,
    ) -> None:
        nonlocal scripts_created, scripts_existing, existing_scripts
        key = (agent_kind, entity_id)
        if overwrite_existing and key in existing_scripts:
            await registry.remove_scripts_by_user(email)
            existing_scripts.pop(key, None)

        meta = existing_scripts.get(key)
        if meta is None:
            meta = await registry.register_script(
                simulation_id=simulation_id,
                user_id=email,
                script_code=script_body,
                description=f"auto-seed for {agent_kind.value} {entity_id}",
                agent_kind=agent_kind,
                entity_id=entity_id,
            )
            existing_scripts[key] = meta
            scripts_created += 1
        else:
            scripts_existing += 1
        await orchestrator.data_access.ensure_entity_state(
            simulation_id, agent_kind, entity_id
        )

    # 确保管理员和基线默认项已准备妥当，以便后续的注册行为能复现
    # 生产环境的行为（如一致的默认密码与基线脚本）。先为四个单例
    # 代理(sequential singletons)创建用户与脚本。
    for email, agent_kind, entity_id, user_type in _SINGLETON_AGENTS:
        await ensure_user(email, user_type)
        await ensure_script(email, agent_kind, entity_id, _SINGLETON_SCRIPT)

    # 使用确定性命名规则为 households 生成脚本与账户。
    config_households = get_world_config().simulation.num_households
    base_target = max(TEST_WORLD_DEFAULT_HOUSEHOLDS, config_households)
    target_households = (
        base_target if household_count is None else max(base_target, household_count)
    )
    household_range = range(target_households)

    async def seed_households(range_iterable: Iterable[int]) -> None:
        for household_id in range_iterable:
            email = f"test_household_{household_id:03d}@econ.sim"
            await ensure_user(email, "individual")
            await ensure_script(
                email,
                AgentKind.HOUSEHOLD,
                str(household_id),
                _HOUSEHOLD_SCRIPT,
            )

    await seed_households(household_range)

    return SeedSummary(
        simulation_id=simulation_id,
        users_created=users_created,
        users_existing=users_existing,
        scripts_created=scripts_created,
        scripts_existing=scripts_existing,
    )


async def main() -> int:
    summary = await seed_test_world()
    print(
        "[seed] simulation=%s users=%s (created=%s, existing=%s) scripts=%s (created=%s, existing=%s)"
        % (
            summary.simulation_id,
            summary.total_users,
            summary.users_created,
            summary.users_existing,
            summary.total_scripts,
            summary.scripts_created,
            summary.scripts_existing,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
