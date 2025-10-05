import pytest

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.script_engine import ScriptRegistry, script_registry
from econ_sim.script_engine.registry import ScriptExecutionError
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
    metadata = await registry.register_script(
        simulation_id="sim-a",
        user_id="u1",
        script_code=script_code,
        description="bank tweak",
    )
    assert metadata.script_id
    assert metadata.code_version

    config = get_world_config()
    orchestrator = SimulationOrchestrator()
    world_state = await orchestrator.create_simulation("sim-a")

    overrides = await registry.generate_overrides("sim-a", world_state, config)
    assert overrides is not None
    assert overrides.bank is not None
    assert overrides.bank.deposit_rate >= 0.01


@pytest.mark.asyncio
async def test_script_overrides_affect_tick_execution() -> None:
    await script_registry.clear()

    await script_registry.register_script(
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

    await script_registry.clear()


@pytest.mark.asyncio
async def test_script_can_attach_after_pre_upload() -> None:
    registry = ScriptRegistry()
    preloaded = await registry.register_script(
        simulation_id=None,
        user_id="late-user",
        script_code="""
def generate_decisions(context):
    return {"government": {"tax_rate": 0.12}}
""",
        description="pending policy",
    )

    assert preloaded.simulation_id is None

    attached = await registry.attach_script(
        preloaded.script_id, "delayed-sim", "late-user"
    )
    assert attached.simulation_id == "delayed-sim"

    config = get_world_config()
    orchestrator = SimulationOrchestrator()
    world_state = await orchestrator.create_simulation("delayed-sim")
    overrides = await registry.generate_overrides("delayed-sim", world_state, config)
    assert overrides is not None
    assert overrides.government is not None


@pytest.mark.asyncio
async def test_list_user_scripts_includes_unattached() -> None:
    await script_registry.clear()

    meta = await script_registry.register_script(
        simulation_id=None,
        user_id="collector",
        script_code="""
def generate_decisions(context):
    return None
""",
        description="noop script",
    )

    scripts = await script_registry.list_user_scripts("collector")
    assert any(item.script_id == meta.script_id for item in scripts)

    await script_registry.clear()


@pytest.mark.asyncio
async def test_delete_script_by_id() -> None:
    registry = ScriptRegistry()
    meta = await registry.register_script(
        simulation_id=None,
        user_id="cleanup",
        script_code="""
def generate_decisions(context):
    return {}
""",
    )

    assert await registry.delete_script_by_id(meta.script_id)

    with pytest.raises(ScriptExecutionError):
        await registry.delete_script_by_id(meta.script_id)
