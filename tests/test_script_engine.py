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


@pytest.mark.asyncio
async def test_rejects_forbidden_import() -> None:
    registry = ScriptRegistry()
    with pytest.raises(ScriptExecutionError):
        await registry.register_script(
            simulation_id="danger",
            user_id="u3",
            script_code="""
import os

def generate_decisions(context):
    return {}
""",
        )


@pytest.mark.asyncio
async def test_script_timeout_is_reported() -> None:
    registry = ScriptRegistry(sandbox_timeout=0.1)
    await registry.register_script(
        simulation_id="slow",
        user_id="u4",
        script_code="""
def generate_decisions(context):
    while True:
        pass
""",
    )

    config = get_world_config()
    orchestrator = SimulationOrchestrator()
    world_state = await orchestrator.create_simulation("slow")

    with pytest.raises(ScriptExecutionError):
        await registry.generate_overrides("slow", world_state, config)


@pytest.mark.asyncio
async def test_simulation_specific_limit_overrides_default() -> None:
    registry = ScriptRegistry(max_scripts_per_user=3)

    await registry.set_simulation_limit("sim-override", 1)

    await registry.register_script(
        simulation_id="sim-override",
        user_id="over-user",
        script_code="""
def generate_decisions(context):
    return {"firm": {"price": 9.0}}
""",
    )

    with pytest.raises(ScriptExecutionError):
        await registry.register_script(
            simulation_id="sim-override",
            user_id="over-user",
            script_code="""
def generate_decisions(context):
    return {"firm": {"price": 8.5}}
""",
        )

    limit = await registry.get_simulation_limit("sim-override")
    assert limit == 1

    await registry.set_simulation_limit("sim-override", None)
    restored_limit = await registry.get_simulation_limit("sim-override")
    assert restored_limit == 3


@pytest.mark.asyncio
async def test_register_script_enforces_per_user_limit() -> None:
    registry = ScriptRegistry(max_scripts_per_user=1)

    await registry.register_script(
        simulation_id="limit-sim",
        user_id="limited-user",
        script_code="""
def generate_decisions(context):
    return {"firm": {"price": 10.0}}
""",
    )

    with pytest.raises(ScriptExecutionError):
        await registry.register_script(
            simulation_id="limit-sim",
            user_id="limited-user",
            script_code="""
def generate_decisions(context):
    return {"firm": {"price": 11.0}}
""",
        )

    scripts = await registry.list_scripts("limit-sim")
    assert len(scripts) == 1


@pytest.mark.asyncio
async def test_attach_script_respects_per_user_limit() -> None:
    registry = ScriptRegistry(max_scripts_per_user=1)

    primary = await registry.register_script(
        simulation_id="attach-sim",
        user_id="limited-user",
        script_code="""
def generate_decisions(context):
    return {"bank": {"deposit_rate": 0.02}}
""",
    )

    queued = await registry.register_script(
        simulation_id=None,
        user_id="limited-user",
        script_code="""
def generate_decisions(context):
    return {"bank": {"deposit_rate": 0.03}}
""",
    )

    assert primary.simulation_id == "attach-sim"
    assert queued.simulation_id is None

    with pytest.raises(ScriptExecutionError):
        await registry.attach_script(
            queued.script_id,
            "attach-sim",
            "limited-user",
        )

    scripts = await registry.list_scripts("attach-sim")
    assert len(scripts) == 1


class StubLimitStore:
    def __init__(self) -> None:
        self._limits: dict[str, int] = {}

    async def set_script_limit(self, simulation_id: str, limit: int) -> None:
        self._limits[simulation_id] = limit

    async def delete_script_limit(self, simulation_id: str) -> None:
        self._limits.pop(simulation_id, None)

    async def get_script_limit(self, simulation_id: str) -> int | None:
        return self._limits.get(simulation_id)

    async def list_script_limits(self) -> dict[str, int]:
        return dict(self._limits)

    async def clear(self) -> None:
        self._limits.clear()


@pytest.mark.asyncio
async def test_limits_are_persisted_via_store() -> None:
    store = StubLimitStore()
    registry = ScriptRegistry(limit_store=store)

    await registry.set_simulation_limit("persisted-sim", 2)
    assert store._limits["persisted-sim"] == 2

    recovered = ScriptRegistry(limit_store=store)
    limit = await recovered.get_simulation_limit("persisted-sim")
    assert limit == 2

    await recovered.set_simulation_limit("persisted-sim", None)
    assert "persisted-sim" not in store._limits
