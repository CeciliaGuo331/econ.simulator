import sys, os

sys.path.insert(0, os.getcwd())
import asyncio
from econ_sim.script_engine import ScriptRegistry
from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import AgentKind
from econ_sim.utils.settings import get_world_config


async def main():
    print("DBG: start main")
    sys.stdout.flush()
    os.environ["ECON_SIM_TEST_FORCE_POOL"] = "1"
    print("DBG: set ENV ECON_SIM_TEST_FORCE_POOL")
    sys.stdout.flush()
    registry = ScriptRegistry(sandbox_timeout=0.1)
    print("DBG: created registry")
    sys.stdout.flush()
    orchestrator = SimulationOrchestrator()
    print("DBG: created orchestrator")
    sys.stdout.flush()
    # seed required scripts minimal
    from tests.utils import seed_required_scripts

    print("DBG: importing seed_required_scripts")
    sys.stdout.flush()
    await seed_required_scripts(
        registry, "slow", skip={AgentKind.HOUSEHOLD}, orchestrator=orchestrator
    )
    print("DBG: seed_required_scripts done")
    sys.stdout.flush()
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
    print("DBG: register_script done, meta=", meta)
    sys.stdout.flush()
    world_state = await orchestrator.create_simulation("slow")
    print("DBG: create_simulation done")
    sys.stdout.flush()
    config = get_world_config()
    print("DBG: got world config")
    sys.stdout.flush()
    overrides, failure_logs, failure_events = await registry.generate_overrides(
        "slow", world_state, config
    )
    print("DBG: generate_overrides done")
    sys.stdout.flush()
    print("overrides:", overrides)
    print("failure_logs:", failure_logs)
    print("failure_events:", failure_events)


if __name__ == "__main__":
    asyncio.run(main())
