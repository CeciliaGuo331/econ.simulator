import asyncio, os, json
import sys

sys.path.insert(0, os.getcwd())
from econ_sim.script_engine import reset_script_registry, script_registry
from econ_sim.core.orchestrator import SimulationOrchestrator
from tests.utils import seed_required_scripts
from econ_sim.data_access.models import StateUpdateCommand, AgentKind


async def main():
    os.environ["ECON_SIM_SCRIPT_TIMEOUT_SECONDS"] = "1.0"
    reset_script_registry()
    registry = script_registry
    orch = SimulationOrchestrator()
    await seed_required_scripts(registry, "dbg-sim", orchestrator=orch)
    ws = await orch.create_simulation("dbg-sim")
    await orch.data_access.ensure_entity_state("dbg-sim", AgentKind.HOUSEHOLD, "0")
    # set firm employees
    await orch.data_access.apply_updates(
        "dbg-sim",
        [
            StateUpdateCommand.assign(
                AgentKind.FIRM,
                agent_id=ws.firm.id,
                employees=[0],
                balance_sheet=ws.firm.balance_sheet.model_dump(),
            )
        ],
    )
    await orch.data_access.apply_updates(
        "dbg-sim",
        [
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT,
                agent_id=ws.government.id,
                spending=500.0,
                balance_sheet=ws.government.balance_sheet.model_dump(),
            )
        ],
    )
    result = await orch.run_tick("dbg-sim")
    print("TICK LOGS:")
    for l in result.logs:
        print("-", l.message, l.context)
    print("\nUPDATES:")
    for u in result.updates:
        print("-", u.scope, u.agent_id, u.changes)
    print("\nMACRO GDP:", result.world_state.macro.gdp)


if __name__ == "__main__":
    asyncio.run(main())
