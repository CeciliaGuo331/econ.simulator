import sys, os, asyncio

sys.path.insert(0, os.getcwd())
from econ_sim.script_engine import ScriptRegistry
from econ_sim.core.orchestrator import SimulationOrchestrator
from tests.utils import seed_required_scripts


async def main():
    os.environ["ECON_SIM_TEST_FORCE_POOL"] = "1"
    registry = ScriptRegistry(sandbox_timeout=1.0)
    orchestrator = SimulationOrchestrator()
    await seed_required_scripts(registry, "demo", orchestrator=orchestrator)
    # ensure simulation exists
    world_state = await orchestrator.create_simulation("demo")
    print("created simulation demo, tick=", world_state.tick)
    result = await orchestrator.run_tick("demo")
    print("run_tick returned:", result)
    # try to access macro gdp
    try:
        print("macro.gdp =", result.world_state.macro.gdp)
    except Exception as e:
        print("failed to read macro.gdp:", e)


if __name__ == "__main__":
    asyncio.run(main())
