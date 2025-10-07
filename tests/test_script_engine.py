import pytest

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import AgentKind
from econ_sim.script_engine import ScriptRegistry, script_registry
from econ_sim.script_engine.registry import ScriptExecutionError
from econ_sim.utils.settings import get_world_config


REQUIRED_AGENT_KINDS = (
    AgentKind.HOUSEHOLD,
    AgentKind.FIRM,
    AgentKind.BANK,
    AgentKind.GOVERNMENT,
    AgentKind.CENTRAL_BANK,
)

BASELINE_STUB_SCRIPT = """
def generate_decisions(context):
    return {}
"""


async def _seed_required_scripts(
    registry: ScriptRegistry,
    simulation_id: str,
    *,
    skip: set[AgentKind] | None = None,
    orchestrator: SimulationOrchestrator | None = None,
) -> None:
    skip_set = skip or set()
    if orchestrator is not None:
        await orchestrator.create_simulation(simulation_id)
    for kind in REQUIRED_AGENT_KINDS:
        if kind in skip_set:
            continue
        entity = f"{kind.value}_seed"
        if kind is AgentKind.HOUSEHOLD:
            entity = "0"
        metadata = await registry.register_script(
            simulation_id=simulation_id,
            user_id=f"seed-{kind.value}",
            script_code=BASELINE_STUB_SCRIPT,
            description=f"seed for {kind.value}",
            agent_kind=kind,
            entity_id=entity,
        )
        if orchestrator is not None:
            await orchestrator.data_access.ensure_entity_state(
                simulation_id, metadata.agent_kind, metadata.entity_id
            )


@pytest.mark.asyncio
async def test_script_registry_generates_overrides() -> None:
    registry = ScriptRegistry()
    orchestrator = SimulationOrchestrator()
    await _seed_required_scripts(
        registry,
        "sim-a",
        skip={AgentKind.BANK},
        orchestrator=orchestrator,
    )
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
        agent_kind=AgentKind.BANK,
        entity_id="bank_main",
    )
    assert metadata.script_id
    assert metadata.code_version

    config = get_world_config()
    world_state = await orchestrator.create_simulation("sim-a")

    overrides, failure_logs = await registry.generate_overrides(
        "sim-a", world_state, config
    )
    assert overrides is not None
    assert overrides.bank is not None
    assert overrides.bank.deposit_rate >= 0.01
    assert failure_logs == []


@pytest.mark.asyncio
async def test_script_overrides_affect_tick_execution() -> None:
    await script_registry.clear()

    orchestrator = SimulationOrchestrator()
    await _seed_required_scripts(
        script_registry,
        "shared-sim",
        skip={AgentKind.FIRM},
        orchestrator=orchestrator,
    )

    await script_registry.register_script(
        simulation_id="shared-sim",
        user_id="u2",
        script_code="""
def generate_decisions(context):
    return {"firm": {"price": 15.0}}
""",
        description="force firm price",
        agent_kind=AgentKind.FIRM,
        entity_id="firm_main",
    )
    await orchestrator.data_access.ensure_entity_state(
        "shared-sim", AgentKind.FIRM, "firm_main"
    )

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
        agent_kind=AgentKind.GOVERNMENT,
        entity_id="gov_pending",
    )

    assert preloaded.simulation_id is None

    attached = await registry.attach_script(
        preloaded.script_id, "delayed-sim", "late-user"
    )
    assert attached.simulation_id == "delayed-sim"

    orchestrator = SimulationOrchestrator()
    await _seed_required_scripts(
        registry,
        "delayed-sim",
        skip={AgentKind.GOVERNMENT},
        orchestrator=orchestrator,
    )

    config = get_world_config()
    world_state = await orchestrator.create_simulation("delayed-sim")
    overrides, failure_logs = await registry.generate_overrides(
        "delayed-sim", world_state, config
    )
    assert overrides is not None
    assert overrides.government is not None
    assert not failure_logs


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
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="0",
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
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="0",
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
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="0",
        )


@pytest.mark.asyncio
async def test_script_timeout_is_reported() -> None:
    registry = ScriptRegistry(sandbox_timeout=0.1)
    orchestrator = SimulationOrchestrator()
    await _seed_required_scripts(
        registry,
        "slow",
        skip={AgentKind.HOUSEHOLD},
        orchestrator=orchestrator,
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

    config = get_world_config()
    world_state = await orchestrator.create_simulation("slow")

    overrides, failure_logs = await registry.generate_overrides(
        "slow", world_state, config
    )
    assert overrides is None
    assert len(failure_logs) == 1
    assert meta.script_id in failure_logs[0].message

    refreshed = await registry.get_user_script(meta.script_id, "u4")
    assert refreshed.last_failure_reason is not None
    assert refreshed.last_failure_at is not None


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
        agent_kind=AgentKind.FIRM,
        entity_id="firm_primary",
    )

    with pytest.raises(ScriptExecutionError):
        await registry.register_script(
            simulation_id="sim-override",
            user_id="over-user",
            script_code="""
def generate_decisions(context):
    return {"firm": {"price": 8.5}}
""",
            agent_kind=AgentKind.FIRM,
            entity_id="firm_secondary",
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
        agent_kind=AgentKind.FIRM,
        entity_id="firm_slot",
    )

    with pytest.raises(ScriptExecutionError):
        await registry.register_script(
            simulation_id="limit-sim",
            user_id="limited-user",
            script_code="""
def generate_decisions(context):
    return {"firm": {"price": 11.0}}
""",
            agent_kind=AgentKind.FIRM,
            entity_id="firm_slot_extra",
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
        agent_kind=AgentKind.BANK,
        entity_id="bank_primary",
    )

    queued = await registry.register_script(
        simulation_id=None,
        user_id="limited-user",
        script_code="""
def generate_decisions(context):
    return {"bank": {"deposit_rate": 0.03}}
""",
        agent_kind=AgentKind.BANK,
        entity_id="bank_queue",
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
