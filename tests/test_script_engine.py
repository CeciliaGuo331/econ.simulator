import pytest

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.script_engine import ScriptRegistry, script_registry
from econ_sim.utils.settings import get_world_config


@pytest.mark.asyncio
async def test_script_registry_generates_overrides() -> None:
    registry = ScriptRegistry()
    script_code = """
def generate_decisions(context):
    macro = context["world_state"]["macro"]
    inflation = macro["inflation"]
    return {"bank": {"deposit_rate": max(0.0, inflation + 0.01)}}
"""
    metadata = registry.register_script(
        simulation_id="sim-a",
        user_id="u1",
        script_code=script_code,
        description="bank tweak",
    )
    assert metadata.script_id

    config = get_world_config()
    orchestrator = SimulationOrchestrator()
    world_state = await orchestrator.create_simulation("sim-a")

    overrides = registry.generate_overrides("sim-a", world_state, config)
    assert overrides is not None
    assert overrides.bank is not None
    assert overrides.bank.deposit_rate >= 0.01


@pytest.mark.asyncio
async def test_script_overrides_affect_tick_execution() -> None:
    script_registry.clear()

    script_registry.register_script(
        simulation_id="shared-sim",
        user_id="u2",
        script_code="""
def generate_decisions(context):
    return {"firm": {"price": 15.0}}
""",
        description="force firm price",
    )

    orchestrator = SimulationOrchestrator()
    result = await orchestrator.run_tick("shared-sim")

    assert result.world_state.firm.price == pytest.approx(15.0)

    script_registry.clear()
