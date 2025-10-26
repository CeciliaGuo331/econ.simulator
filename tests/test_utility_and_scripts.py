import asyncio

import pytest

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import (
    AgentKind,
    StateUpdateCommand,
    TickDecisionOverrides,
)
from econ_sim.utils.settings import get_world_config


@pytest.mark.asyncio
async def test_utility_accumulation_and_discount():
    orch = SimulationOrchestrator()
    sim_id = "test_utility_acc"
    await orch.create_simulation(sim_id)

    # ensure household exists
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.HOUSEHOLD, "1")

    # ensure other singleton agents exist and register trivial placeholder scripts
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.FIRM, "firm_1")
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.BANK, "bank")
    await orch.data_access.ensure_entity_state(
        sim_id, AgentKind.GOVERNMENT, "government"
    )
    await orch.data_access.ensure_entity_state(
        sim_id, AgentKind.CENTRAL_BANK, "central_bank"
    )

    trivial = """
def generate_decisions(context):
    return None
"""
    # register trivial placeholders so run_tick doesn't raise MissingAgentScriptsError
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="1",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.FIRM,
        entity_id="firm_1",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.BANK,
        entity_id="bank",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.GOVERNMENT,
        entity_id="government",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.CENTRAL_BANK,
        entity_id="central_bank",
    )

    ws = await orch.data_access.get_world_state(sim_id)
    cfg = get_world_config()
    beta = float(cfg.policies.discount_factor_per_tick)
    gamma = float(cfg.policies.crra_gamma)
    eps = float(cfg.policies.utility_epsilon_for_log)

    # set last_consumption for household 1 to a known value
    c = 8.0
    await orch.data_access.apply_updates(
        sim_id,
        [
            StateUpdateCommand.assign(
                AgentKind.HOUSEHOLD, agent_id=1, last_consumption=c
            )
        ],
    )

    # run one tick to trigger utility accumulation
    res = await orch.run_tick(sim_id)
    ws_after = res.world_state
    hh = ws_after.households.get(1)
    assert hh is not None

    # compute expected instantaneous utility
    import math

    c_eff = max(0.0, float(c))
    if abs(gamma - 1.0) < 1e-12:
        u = math.log(max(c_eff, eps))
    else:
        u = (c_eff ** (1.0 - gamma) - 1.0) / (1.0 - gamma)

    # discount exponent used by module: exp = tick - 1, where tick is world_state.tick before market
    tick = int(ws.tick)
    exp = max(0, tick - 1)
    expected = float(u) * (float(beta) ** exp)

    assert pytest.approx(expected, rel=1e-6) == float(hh.lifetime_utility)


@pytest.mark.asyncio
async def test_script_can_read_lifetime_and_influence_decision():
    orch = SimulationOrchestrator()
    sim_id = "test_script_read_utility"
    await orch.create_simulation(sim_id)

    # ensure household and firm
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.HOUSEHOLD, "1")
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.FIRM, "firm_1")

    # set firm's inventory and price so consumption can occur
    await orch.data_access.apply_updates(
        sim_id,
        [
            StateUpdateCommand.assign(
                AgentKind.FIRM,
                agent_id="firm_1",
                balance_sheet={"inventory_goods": 100.0, "cash": 1000.0},
                price=1.0,
            )
        ],
    )

    # set household cash and pre-seed lifetime_utility so script sees it
    await orch.data_access.apply_updates(
        sim_id,
        [
            StateUpdateCommand.assign(
                AgentKind.HOUSEHOLD,
                agent_id=1,
                balance_sheet={"cash": 100.0, "deposits": 0.0, "inventory_goods": 0.0},
                lifetime_utility=5.0,
            )
        ],
    )

    # register a household script that reads lifetime_utility and sets consumption_budget
    script = """
def generate_decisions(context):
    eid = context.get('entity_id')
    ws = context.get('world_state', {})
    hh = ws.get('households', {}).get(str(eid), {})
    lu = hh.get('lifetime_utility', 0)
    from econ_sim.script_engine.user_api import OverridesBuilder
    b = OverridesBuilder()
    if lu and lu > 0:
        b.household(int(eid), consumption_budget=10.0)
    return b.build()
"""

    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=script,
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="1",
    )

    # register trivial placeholders for other required agent kinds
    trivial = """
def generate_decisions(context):
    return None
"""
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.FIRM,
        entity_id="firm_1",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.BANK,
        entity_id="bank",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.GOVERNMENT,
        entity_id="government",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.CENTRAL_BANK,
        entity_id="central_bank",
    )

    # run a tick; after script runs goods_market should record last_consumption for hh1
    res = await orch.run_tick(sim_id)
    ws_after = res.world_state
    hh = ws_after.households.get(1)
    assert hh is not None
    # household should have consumed 10 units (price=1 -> last_consumption==10) or at least >0
    assert float(hh.last_consumption) >= 1.0


@pytest.mark.asyncio
async def test_get_state_exposes_lifetime_utility():
    orch = SimulationOrchestrator()
    sim_id = "test_get_state_lifetime"
    await orch.create_simulation(sim_id)
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.HOUSEHOLD, "1")

    # set lifetime utility via update
    await orch.data_access.apply_updates(
        sim_id,
        [
            StateUpdateCommand.assign(
                AgentKind.HOUSEHOLD, agent_id=1, lifetime_utility=42.42
            )
        ],
    )

    ws = await orch.get_state(sim_id)
    hh = ws.households.get(1)
    assert hh is not None
    assert float(hh.lifetime_utility) == pytest.approx(42.42)
