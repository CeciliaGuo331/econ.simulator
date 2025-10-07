"""Shared testing helpers for seeding minimal simulation coverage."""

from __future__ import annotations

from typing import Iterable, Sequence

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import AgentKind
from econ_sim.script_engine import ScriptRegistry

REQUIRED_AGENT_KINDS: Sequence[AgentKind] = (
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


async def seed_required_scripts(
    registry: ScriptRegistry,
    simulation_id: str,
    *,
    orchestrator: SimulationOrchestrator | None = None,
    households: Iterable[int] | None = None,
    skip: Iterable[AgentKind] | None = None,
) -> None:
    """Ensure a minimal set of scripts is registered for the given simulation."""

    skip_set = set(skip or [])
    household_ids = list(households or [0])

    if orchestrator is not None:
        await orchestrator.create_simulation(simulation_id)

    for kind in REQUIRED_AGENT_KINDS:
        if kind in skip_set:
            continue

        if kind is AgentKind.HOUSEHOLD:
            for household_id in household_ids:
                metadata = await registry.register_script(
                    simulation_id=simulation_id,
                    user_id=f"seed-{kind.value}-{household_id}",
                    script_code=BASELINE_STUB_SCRIPT,
                    description=f"seed for {kind.value} {household_id}",
                    agent_kind=kind,
                    entity_id=str(household_id),
                )
                if orchestrator is not None:
                    await orchestrator.data_access.ensure_entity_state(
                        simulation_id,
                        metadata.agent_kind,
                        metadata.entity_id,
                    )
        else:
            entity_id = f"{kind.value}_seed"
            metadata = await registry.register_script(
                simulation_id=simulation_id,
                user_id=f"seed-{kind.value}",
                script_code=BASELINE_STUB_SCRIPT,
                description=f"seed for {kind.value}",
                agent_kind=kind,
                entity_id=entity_id,
            )
            if orchestrator is not None:
                await orchestrator.data_access.ensure_entity_state(
                    simulation_id,
                    metadata.agent_kind,
                    metadata.entity_id,
                )
