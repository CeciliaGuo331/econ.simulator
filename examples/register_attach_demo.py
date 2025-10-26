"""演示：如何使用 ScriptRegistry 注册、挂载外部脚本并在单个 Tick 上执行。

此脚本为演示用途：不做网络调用，直接在内存中创建 Registry、注册示例脚本并运行一次 generate_overrides。
"""

import asyncio
from pathlib import Path

from econ_sim.script_engine.registry import ScriptRegistry
from econ_sim.data_access.models import (
    WorldState,
    MacroState,
    FirmState,
    BankState,
    GovernmentState,
    CentralBankState,
    HouseholdState,
)
from econ_sim.utils.settings import get_world_config
from econ_sim.data_access.models import AgentKind


async def demo():
    # minimal world state required by ScriptRegistry.generate_overrides
    world = WorldState(
        simulation_id="demo",
        tick=1,
        day=0,
        firm=FirmState(),
        bank=BankState(),
        government=GovernmentState(),
        central_bank=CentralBankState(),
        macro=MacroState(gdp=1000.0, inflation=0.01, unemployment_rate=0.04),
    )

    registry = ScriptRegistry()

    # load sample household external script from examples
    script_path = Path(__file__).parent / "external_scripts" / "household_external.py"
    code = script_path.read_text(encoding="utf-8")

    # register script for a placeholder household id "1"
    meta = await registry.register_script(
        simulation_id="demo",
        user_id="demo_user",
        script_code=code,
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="1",
    )
    print("registered:", meta.script_id)

    # attach is already done by register_script when simulation_id provided; list overrides
    overrides, logs, failures = await registry.generate_overrides(
        "demo", world, get_world_config()
    )
    print("overrides:", overrides)
    print("logs:", logs)
    print("failures:", failures)


if __name__ == "__main__":
    asyncio.run(demo())
