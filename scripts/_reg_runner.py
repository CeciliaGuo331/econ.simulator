import sys, os

sys.path.insert(0, os.getcwd())
import asyncio
from econ_sim.script_engine import ScriptRegistry
from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import AgentKind
from econ_sim.utils.settings import get_world_config


async def main():
    os.environ["ECON_SIM_TEST_FORCE_POOL"] = "1"
    registry = ScriptRegistry(sandbox_timeout=0.1)
    orchestrator = SimulationOrchestrator()
    # seed required scripts minimal
    from tests.utils import seed_required_scripts

    await seed_required_scripts(
        registry, "slow", skip={AgentKind.HOUSEHOLD}, orchestrator=orchestrator
    )
    meta = await registry.register_script(
        simulation_id="slow",
        user_id="u4",
        script_code="""
def generate_decisions(context):
    while True:
        pass
""",
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="0",
    )
    world_state = await orchestrator.create_simulation("slow")
    config = get_world_config()
    overrides, failure_logs, failure_events = await registry.generate_overrides(
        "slow", world_state, config
    )
    print("overrides:", overrides)
    print("failure_logs:", failure_logs)
    print("failure_events:", failure_events)


if __name__ == "__main__":
    asyncio.run(main())
